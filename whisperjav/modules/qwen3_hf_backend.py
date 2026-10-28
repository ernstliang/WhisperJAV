#!/usr/bin/env python3
"""
Native transformers backend for Qwen3-ASR (``*-hf`` checkpoints).

Replaces the ``qwen-asr`` package, which pins ``transformers==4.57.6`` (and
through it ``huggingface-hub<1.0``). transformers >= 5.13 ships Qwen3-ASR
natively (``Qwen3ASRForConditionalGeneration`` / ``Qwen3ASRForTokenClassification``),
loadable from the ``-hf`` model repos:

    Qwen/Qwen3-ASR-1.7B-hf, Qwen/Qwen3-ASR-0.6B-hf, Qwen/Qwen3-ForcedAligner-0.6B-hf

The two classes here mirror the subset of the qwen-asr API that
``whisperjav.modules.qwen_asr`` uses, so callers stay unchanged:

    Qwen3ASRModel.from_pretrained(...).transcribe(audio, context, language, return_time_stamps)
    Qwen3ForcedAligner.from_pretrained(...).align(audio, text, language)

Long-audio chunking (180s with aligner, 1200s without) follows qwen-asr.
"""

from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple, Union

import numpy as np
import torch

from whisperjav.utils.logger import logger

SAMPLE_RATE = 16000
MAX_ASR_INPUT_SECONDS = 1200
MAX_FORCE_ALIGN_INPUT_SECONDS = 180
MIN_ASR_INPUT_SECONDS = 0.5

# Languages the Qwen3-ForcedAligner supports (transformers FORCED_ALIGNER_LANGUAGES)
FORCED_ALIGNER_LANGUAGES = {
    "Chinese", "English", "Cantonese", "French", "German",
    "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
}

# Pre-hf repo IDs (qwen-asr format) → native transformers equivalents
LEGACY_MODEL_IDS = {
    "Qwen/Qwen3-ASR-1.7B": "Qwen/Qwen3-ASR-1.7B-hf",
    "Qwen/Qwen3-ASR-0.6B": "Qwen/Qwen3-ASR-0.6B-hf",
    "Qwen/Qwen3-ForcedAligner-0.6B": "Qwen/Qwen3-ForcedAligner-0.6B-hf",
    "jaykwok/Qwen3-ASR-1.7B-JA-Anime-Galgame": "jaykwok/Qwen3-ASR-1.7B-JA-Anime-Galgame-hf",
}

AudioLike = Union[str, Tuple[np.ndarray, int], np.ndarray]


def resolve_model_id(model_id: str) -> str:
    """Map legacy qwen-asr repo IDs to their ``-hf`` equivalents; pass everything else through."""
    mapped = LEGACY_MODEL_IDS.get(model_id)
    if mapped:
        logger.warning(
            "Model '%s' is the qwen-asr format and cannot be loaded by transformers; "
            "using '%s' instead.", model_id, mapped,
        )
        return mapped
    return model_id


# ---------------------------------------------------------------------------
# Result types (attribute-compatible with qwen-asr)
# ---------------------------------------------------------------------------

@dataclass
class ForcedAlignItem:
    text: str
    start_time: float
    end_time: float


@dataclass
class ForcedAlignResult:
    items: List[ForcedAlignItem] = field(default_factory=list)

    def __iter__(self) -> Iterator[ForcedAlignItem]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


@dataclass
class ASRTranscription:
    language: str
    text: str
    time_stamps: Optional[ForcedAlignResult] = None


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def load_audio(audio: AudioLike) -> np.ndarray:
    """Load one input as mono 16 kHz float32 in [-1, 1]."""
    if isinstance(audio, str):
        import librosa
        wav, _ = librosa.load(audio, sr=SAMPLE_RATE, mono=True)
    elif isinstance(audio, tuple):
        wav, sr = np.asarray(audio[0], dtype=np.float32), int(audio[1])
        if wav.ndim == 2:
            wav = wav.mean(axis=0 if wav.shape[0] <= 8 and wav.shape[1] > wav.shape[0] else -1)
        if sr != SAMPLE_RATE:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=SAMPLE_RATE)
    else:
        wav = np.asarray(audio, dtype=np.float32)

    wav = np.asarray(wav, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 1.0:
        wav = wav / peak
    return np.clip(wav, -1.0, 1.0)


def split_audio_into_chunks(
    wav: np.ndarray,
    max_chunk_sec: float,
    search_expand_sec: float = 5.0,
    min_window_ms: float = 100.0,
) -> List[Tuple[np.ndarray, float]]:
    """Split at low-energy points near ``max_chunk_sec``. Returns ``(chunk, offset_sec)`` pairs."""
    total = len(wav)
    max_len = int(max_chunk_sec * SAMPLE_RATE)
    if total <= max_len:
        chunks = [(wav, 0.0)]
    else:
        expand = int(search_expand_sec * SAMPLE_RATE)
        win = max(4, int(min_window_ms / 1000.0 * SAMPLE_RATE))
        chunks = []
        start = 0
        while total - start > max_len:
            cut = start + max_len
            left, right = max(start, cut - expand), min(total, cut + expand)
            if right - left <= win:
                boundary = cut
            else:
                seg_abs = np.abs(wav[left:right])
                sums = np.convolve(seg_abs, np.ones(win, dtype=np.float32), mode="valid")
                pos = int(np.argmin(sums))
                boundary = left + pos + int(np.argmin(seg_abs[pos:pos + win]))
            boundary = min(max(boundary, start + 1), total)
            chunks.append((wav[start:boundary], start / SAMPLE_RATE))
            start = boundary
        chunks.append((wav[start:], start / SAMPLE_RATE))

    # Too-short chunks are zero-padded to MIN_ASR_INPUT_SECONDS, as qwen-asr does
    min_len = int(MIN_ASR_INPUT_SECONDS * SAMPLE_RATE)
    return [
        (np.pad(c, (0, min_len - len(c))) if len(c) < min_len else c, off)
        for c, off in chunks
    ]


def _batches(xs: list, size: int) -> Iterator[list]:
    size = size if size and size > 0 else max(1, len(xs))
    for i in range(0, len(xs), size):
        yield xs[i:i + size]


def _ensure_list(x: Any) -> list:
    return x if isinstance(x, list) else [x]


def _check_not_legacy_format(model_id: str) -> None:
    """Fail clearly on a qwen-asr format checkpoint (``thinker_config`` in config.json).

    transformers cannot load those; without this check the load fails deep inside
    from_pretrained with a missing-weights error that does not say why.
    """
    import json
    from pathlib import Path

    try:
        local = Path(model_id) / "config.json"
        if local.is_file():
            config_path = local
        else:
            from huggingface_hub import hf_hub_download
            config_path = hf_hub_download(model_id, "config.json")
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception as e:  # offline, gated, ... — let from_pretrained report it
        logger.debug("Could not pre-check config of %s: %s", model_id, e)
        return
    if "thinker_config" in config:
        raise ValueError(
            f"'{model_id}' is a qwen-asr format checkpoint, which transformers cannot load. "
            "Use its -hf version, or convert it to the -hf format and pass the local "
            "directory (e.g. --qwen-model-id D:/models/Qwen3-ASR-1.7B-JA-hf)."
        )


def _load_processor_and_model(model_cls, model_id: str, dtype, device_map, **kwargs):
    _check_not_legacy_format(model_id)
    try:
        from transformers import AutoProcessor
        import transformers.models.qwen3_asr  # noqa: F401 — fails on transformers < 5.13
    except ImportError as e:
        raise ImportError(
            "Qwen3-ASR requires transformers>=5.13 with native Qwen3-ASR support. "
            "Install with: pip install 'whisperjav[qwen]'"
        ) from e
    processor = AutoProcessor.from_pretrained(model_id)
    model = model_cls.from_pretrained(model_id, dtype=dtype, **kwargs).to(device_map).eval()
    return processor, model


# ---------------------------------------------------------------------------
# Forced aligner
# ---------------------------------------------------------------------------

class Qwen3ForcedAligner:
    """Qwen3-ForcedAligner via ``AutoModelForTokenClassification``."""

    def __init__(self, processor, model):
        self.processor = processor
        self.model = model

    @classmethod
    def from_pretrained(cls, model_id: str, dtype=torch.bfloat16, device_map="cpu", **kwargs):
        from transformers import AutoModelForTokenClassification

        processor, model = _load_processor_and_model(
            AutoModelForTokenClassification, resolve_model_id(model_id), dtype, device_map, **kwargs
        )
        return cls(processor, model)

    @torch.inference_mode()
    def align(
        self,
        audio: Union[AudioLike, List[AudioLike]],
        text: Union[str, List[str]],
        language: Union[str, List[str]],
    ) -> List[ForcedAlignResult]:
        audios = [load_audio(a) for a in _ensure_list(audio)]
        texts = _ensure_list(text)
        languages = _ensure_list(language)
        if len(languages) == 1 and len(audios) > 1:
            languages = languages * len(audios)
        if not (len(audios) == len(texts) == len(languages)):
            raise ValueError(
                f"Batch size mismatch: audio={len(audios)}, text={len(texts)}, language={len(languages)}"
            )

        inputs, word_lists = self.processor.prepare_forced_aligner_inputs(
            audio=audios, transcript=texts, language=languages
        )
        inputs = inputs.to(self.model.device, self.model.dtype)
        logits = self.model(**inputs).logits
        decoded = self.processor.decode_forced_alignment(
            logits=logits,
            input_ids=inputs["input_ids"],
            word_lists=word_lists,
            timestamp_token_id=self.model.config.timestamp_token_id,
        )
        return [
            ForcedAlignResult([ForcedAlignItem(d["text"], d["start_time"], d["end_time"]) for d in items])
            for items in decoded
        ]


# ---------------------------------------------------------------------------
# ASR model
# ---------------------------------------------------------------------------

class Qwen3ASRModel:
    """Qwen3-ASR via ``AutoModelForMultimodalLM``, with optional forced aligner."""

    def __init__(
        self,
        processor,
        model,
        forced_aligner: Optional[Qwen3ForcedAligner] = None,
        max_inference_batch_size: int = 1,
        max_new_tokens: int = 4096,
    ):
        self.processor = processor
        self.model = model
        self.forced_aligner = forced_aligner
        self.max_inference_batch_size = max_inference_batch_size
        self.max_new_tokens = max_new_tokens
        # Batched generation with a decoder-only LM needs left padding
        self.processor.tokenizer.padding_side = "left"

    @classmethod
    def from_pretrained(
        cls,
        model_id: str,
        dtype=torch.bfloat16,
        device_map="cpu",
        max_inference_batch_size: int = 1,
        max_new_tokens: int = 4096,
        forced_aligner: Optional[str] = None,
        forced_aligner_kwargs: Optional[dict] = None,
        **kwargs,
    ) -> "Qwen3ASRModel":
        from transformers import AutoModelForMultimodalLM

        processor, model = _load_processor_and_model(
            AutoModelForMultimodalLM, resolve_model_id(model_id), dtype, device_map, **kwargs
        )
        aligner = None
        if forced_aligner:
            aligner = Qwen3ForcedAligner.from_pretrained(
                forced_aligner, **(forced_aligner_kwargs or {"dtype": dtype, "device_map": device_map})
            )
        return cls(processor, model, aligner, max_inference_batch_size, max_new_tokens)

    @property
    def generation_config(self):
        return self.model.generation_config

    @torch.inference_mode()
    def _generate(self, wavs: List[np.ndarray], contexts: List[str], languages: List[Optional[str]]) -> List[dict]:
        outputs: List[dict] = []
        for idx in _batches(list(range(len(wavs))), self.max_inference_batch_size):
            inputs = self.processor.apply_transcription_request(
                audio=[wavs[i] for i in idx],
                # Always send a system turn (empty when no context), matching qwen-asr's prompt
                prompt=[contexts[i] or "" for i in idx],
                language=[languages[i] for i in idx],
            )
            inputs = inputs.to(self.model.device, self.model.dtype)
            generated = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens)
            new_tokens = generated[:, inputs["input_ids"].shape[1]:]
            outputs.extend(self.processor.decode(new_tokens, return_format="parsed"))
        return outputs

    def transcribe(
        self,
        audio: Union[AudioLike, List[AudioLike]],
        context: Union[str, List[str]] = "",
        language: Optional[Union[str, List[Optional[str]]]] = None,
        return_time_stamps: bool = False,
    ) -> List[ASRTranscription]:
        if return_time_stamps and self.forced_aligner is None:
            raise ValueError("return_time_stamps=True requires a forced aligner")

        wavs = [load_audio(a) for a in _ensure_list(audio)]
        n = len(wavs)
        contexts = _ensure_list(context)
        if len(contexts) == 1 and n > 1:
            contexts = contexts * n
        languages = [None] * n if language is None else _ensure_list(language)
        if len(languages) == 1 and n > 1:
            languages = languages * n
        if not (len(contexts) == len(languages) == n):
            raise ValueError(f"Batch size mismatch: audio={n}, context={len(contexts)}, language={len(languages)}")
        languages = [lang or None for lang in languages]

        max_chunk_sec = MAX_FORCE_ALIGN_INPUT_SECONDS if return_time_stamps else MAX_ASR_INPUT_SECONDS
        # (orig_index, chunk_wav, offset_sec)
        chunks = [
            (i, cwav, off)
            for i, wav in enumerate(wavs)
            for cwav, off in split_audio_into_chunks(wav, max_chunk_sec)
        ]

        parsed = self._generate(
            [c[1] for c in chunks],
            [contexts[c[0]] for c in chunks],
            [languages[c[0]] for c in chunks],
        )
        chunk_text = [p["transcription"].strip() for p in parsed]
        # A forced language is not echoed back by the model, so report the forced one
        chunk_lang = [languages[c[0]] or p["language"] or "" for c, p in zip(chunks, parsed)]

        chunk_align: List[Optional[ForcedAlignResult]] = [None] * len(chunks)
        if return_time_stamps:
            todo = [
                k for k, (txt, lang) in enumerate(zip(chunk_text, chunk_lang))
                if txt and lang in FORCED_ALIGNER_LANGUAGES
            ]
            skipped = {chunk_lang[k] for k, txt in enumerate(chunk_text) if txt and k not in todo}
            if skipped:
                logger.warning("Forced aligner does not support %s; those chunks get no word timestamps",
                               ", ".join(sorted(s or "unknown" for s in skipped)))
            for batch in _batches(todo, self.max_inference_batch_size):
                results = self.forced_aligner.align(
                    audio=[chunks[k][1] for k in batch],
                    text=[chunk_text[k] for k in batch],
                    language=[chunk_lang[k] for k in batch],
                )
                for k, res in zip(batch, results):
                    off = chunks[k][2]
                    chunk_align[k] = ForcedAlignResult([
                        ForcedAlignItem(it.text, round(it.start_time + off, 3), round(it.end_time + off, 3))
                        for it in res.items
                    ])

        out: List[ASRTranscription] = []
        for i in range(n):
            ks = [k for k, c in enumerate(chunks) if c[0] == i]
            langs: List[str] = []
            for k in ks:
                if chunk_lang[k] and (not langs or langs[-1] != chunk_lang[k]):
                    langs.append(chunk_lang[k])
            stamps = None
            if return_time_stamps:
                stamps = ForcedAlignResult([it for k in ks if chunk_align[k] for it in chunk_align[k].items])
            out.append(ASRTranscription(
                language=",".join(langs),
                text="".join(chunk_text[k] for k in ks),
                time_stamps=stamps,
            ))
        return out
