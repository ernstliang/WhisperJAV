# Qwen3-ASR Pipeline

Qwen3-ASR is an alternative ASR engine based on Alibaba's Qwen architecture, offering strong Japanese text quality with a different approach to speech recognition.

---

## Models

| Model | Size | Notes |
|-------|------|-------|
| **Qwen3-ASR 1.7B** (`Qwen/Qwen3-ASR-1.7B-hf`) | ~4 GB | Full model, best quality |
| **Qwen3-ASR 0.6B** (`Qwen/Qwen3-ASR-0.6B-hf`) | ~2 GB | Smaller, faster, slightly lower quality |
| **Qwen3-ForcedAligner 0.6B** (`Qwen/Qwen3-ForcedAligner-0.6B-hf`) | ~1.2 GB | Word-level timestamps |

WhisperJAV runs the `-hf` checkpoints on native transformers. The older repo IDs
without `-hf` (qwen-asr format) are mapped to their `-hf` equivalents automatically.

---

## How to Use

### GUI (Ensemble Tab)

1. Go to the **Ensemble** tab
2. Set **Pipeline** to **Qwen3-ASR**
3. Select model size
4. Click **Start**

### CLI

```bash
whisperjav video.mp4 --mode qwen
```

Local model directories work too:

```bash
whisperjav video.mp4 --mode qwen   --qwen-model-id D:/models/Qwen3-ASR-1.7B-hf   --qwen-aligner D:/models/Qwen3-ForcedAligner-0.6B-hf
```

### As Ensemble Pass 2

Qwen3-ASR pairs well with Whisper-based pipelines:

1. **Pass 1:** Balanced (Whisper — good timing)
2. **Pass 2:** Qwen3-ASR (good text quality)
3. **Merge Strategy:** Smart Merge

---

## Requirements

- **Qwen extra** must be installed: `pip install whisperjav[qwen]`
- Requires `transformers>=5.13` (native Qwen3-ASR support); the `qwen-asr` package is no longer used
- Japanese alignment uses `nagisa`, Korean uses `soynlp` (both in the qwen extra)
- First run downloads the model from HuggingFace

---

## Strengths and Limitations

**Strengths:**

- Excellent Japanese text quality
- Good handling of casual/colloquial speech
- Strong punctuation and sentence structure

**Limitations:**

- Timing can differ from Whisper-based pipelines
- Apple Silicon: currently CPU-only (MPS not yet supported for forced aligner)
- Requires the HuggingFace extras installed
