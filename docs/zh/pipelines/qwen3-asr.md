# Qwen3-ASR 处理管线

Qwen3-ASR 是一个基于阿里巴巴 Qwen 架构的替代语音识别引擎，以不同的语音识别方法提供高质量的日语文本输出。

---

## 模型

| 模型 | 大小 | 备注 |
|------|------|------|
| **Qwen3-ASR 1.7B**（`Qwen/Qwen3-ASR-1.7B-hf`） | ~4 GB | 完整模型，最佳质量 |
| **Qwen3-ASR 0.6B**（`Qwen/Qwen3-ASR-0.6B-hf`） | ~2 GB | 更小、更快，质量略低 |
| **Qwen3-ForcedAligner 0.6B**（`Qwen/Qwen3-ForcedAligner-0.6B-hf`） | ~1.2 GB | 逐词时间戳 |

WhisperJAV 使用 transformers 原生实现运行 `-hf` 版本模型。不带 `-hf` 的旧仓库 ID（qwen-asr 格式）会自动映射到对应的 `-hf` 版本。

---

## 使用方法

### GUI（Ensemble 选项卡）

1. 进入 **Ensemble** 选项卡
2. 将 **Pipeline** 设置为 **Qwen3-ASR**
3. 选择模型大小
4. 点击 **Start**

### CLI

```bash
whisperjav video.mp4 --mode qwen
```

也可以直接使用本地模型目录：

```bash
whisperjav video.mp4 --mode qwen   --qwen-model-id D:/models/Qwen3-ASR-1.7B-hf   --qwen-aligner D:/models/Qwen3-ForcedAligner-0.6B-hf
```

### 作为集成模式的 Pass 2

Qwen3-ASR 与基于 Whisper 的处理管线配合良好：

1. **Pass 1：** Balanced（Whisper — 良好的时间轴）
2. **Pass 2：** Qwen3-ASR（良好的文本质量）
3. **合并策略：** Smart Merge

---

## 系统要求

- 必须安装 **Qwen 扩展**：`pip install whisperjav[qwen]`
- 需要 `transformers>=5.13`（原生支持 Qwen3-ASR），不再使用 `qwen-asr` 包
- 日语对齐使用 `nagisa`，韩语使用 `soynlp`（均包含在 qwen 扩展中）
- 首次运行时会从 HuggingFace 下载模型

---

## 优势与局限

**优势：**

- 出色的日语文本质量
- 对口语化/日常对话处理良好
- 标点符号和句子结构表现强

**局限：**

- 时间轴可能与基于 Whisper 的处理管线有差异
- Apple Silicon：目前仅支持 CPU（MPS 尚不支持强制对齐器）
- 需要安装 HuggingFace 扩展
