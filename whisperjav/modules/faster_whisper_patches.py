#!/usr/bin/env python3
"""
Runtime guards applied to faster-whisper.

Importing this module installs the patches. It is imported by every ASR module
that constructs a faster_whisper.WhisperModel; the patches are idempotent and
applied to the class, so importing it once is enough for the whole process.
"""

from faster_whisper import WhisperModel

from whisperjav.utils.logger import logger


def install_find_alignment_guard() -> None:
    """
    Guard word-timestamp alignment against a 0- or 1-frame segment.

    ctranslate2's native align() divides by num_frames // 2, so a segment of
    0 or 1 mel frames is an integer divide by zero -- a hardware fault
    (0xC0000094 STATUS_INTEGER_DIVIDE_BY_ZERO on Windows) that kills the
    process outright. It cannot be caught with try/except, so the value must
    never reach the native call.

    Verified against faster-whisper 1.2.1 / ctranslate2 4.7.1 by calling
    WhisperModel.model.align() directly in a fresh process: num_frames of 0
    and 1 both crash, 2 and above return normally.

    How a 1-frame segment arises (faster_whisper/transcribe.py):
        1162  if seek >= seek_clip_end: ...  # lets seek == seek_clip_end - 1 through
        1173  segment_size = min(nb_max_frames, content_frames - seek,
                                 seek_clip_end - seek)
        1283  add_word_timestamps(..., segment_size, ...)   # -> num_frames
    A mel frame is 10ms, so any clip whose last word ends within 10ms of the
    clip end reaches this. The 'balanced' pipeline feeds many short VAD groups,
    which is why it surfaces there first.

    faster-whisper 1.2.1 is the newest release and still lacks the guard, so
    upgrading does not help. Remove this once upstream adds it.

    Returning one empty alignment per batch entry is the shape callers expect:
    add_word_timestamps then sets "words": [] on every subsegment (its
    `while word_index < len(alignments[...])` never runs and `if len(words) > 0`
    blocks all indexing), and utils.get_end falls back to the segment end.
    Skipping add_word_timestamps altogether would NOT be safe -- get_end reads
    s["words"] unconditionally and would raise KeyError.
    """
    if getattr(WhisperModel.find_alignment, "_whisperjav_guarded", False):
        return

    original = WhisperModel.find_alignment

    def find_alignment(self, tokenizer, text_tokens, encoder_output,
                       num_frames, *args, **kwargs):
        if num_frames < 2:
            logger.debug(
                "Skipping word alignment for a %s-frame segment "
                "(ctranslate2 align() would divide by zero)", num_frames)
            return [[] for _ in text_tokens]
        return original(self, tokenizer, text_tokens, encoder_output,
                        num_frames, *args, **kwargs)

    find_alignment._whisperjav_guarded = True
    WhisperModel.find_alignment = find_alignment


install_find_alignment_guard()
