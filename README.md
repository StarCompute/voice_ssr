# Voice SSR

视频处理工具箱：音频降噪 + 中文字幕提取与烧录。

## 功能概览

| 脚本 | 功能 | 说明 |
|------|------|------|
| `audio_denoise.py` | 音频降噪 | 使用谱门控（Spectral Gating）对 MP4 视频音频降噪 |
| `extract_subtitle.py` | 字幕提取 | 使用 OpenAI Whisper 识别中文字幕并生成 SRT 文件，可烧录回视频 |
| `process_video.py` | 一键流水线 | 降噪 → 字幕提取 → 烧录，一步完成 |

## 依赖

### Python 包

```bash
pip install -r requirements.txt
```

### 系统依赖

需要安装 **ffmpeg** 并确保其在 PATH 中可用。

- [下载 ffmpeg](https://ffmpeg.org/download.html)
- Windows: 下载后将 `bin` 目录添加到系统环境变量，或放置在 `C:\ffmpeg\bin\` 下
- macOS: `brew install ffmpeg`
- Linux: `apt install ffmpeg` / `yum install ffmpeg`

## 使用方法

### 1. 音频降噪

```bash
python audio_denoise.py input.mp4 -o output.mp4
```

可选参数：
```
-s, --strength    降噪强度 0.0~1.0（默认 0.8）
--stationary      使用平稳噪声模型（适用于风扇、空调等持续底噪）
--n-fft           FFT 窗口大小，2 的幂次（默认 1024）
-g, --gain        音量增益 dB（如 -g 6 提升 6dB）
--normalize       峰值归一化，自动调至最大安全音量
--keep-wav        保留中间 WAV 文件
```

### 2. 字幕提取与烧录

```bash
# 完整流程：识别 + 生成 SRT + 烧录到视频
python extract_subtitle.py input.mp4 -o output.mp4

# 仅生成 SRT 文件（不烧录）
python extract_subtitle.py input.mp4 --srt-only

# 手动修正 SRT 后重新烧录
python extract_subtitle.py input.mp4 --burn-from-srt subtitle.srt -o output.mp4
```

可选参数：
```
--model          Whisper 模型: tiny/base/small/medium/large-v3/turbo（默认 medium）
--fontsize       字幕字号（默认 24）
--margin         字幕距底部边距（默认 30）
--srt-only       只生成 SRT，不烧录
--burn-from-srt  跳过识别，直接用已有 SRT 烧录
--keep-wav       保留中间 WAV 文件
```

### 3. 一键流水线

```bash
# 降噪 + 字幕识别 + 烧录，一步到位
python process_video.py input.mp4 -o final.mp4

# 仅降噪 + 生成字幕（不烧录）
python process_video.py input.mp4 --srt-only

# 跳过降噪，仅生成字幕
python process_video.py input.mp4 --srt-only-no-denoise
```

降噪参数：
```
-s, --strength    降噪强度 0~1（默认 1.0）
--stationary      平稳噪声模型
--n-fft           FFT 窗口大小（默认 1024）
-g, --gain        音量增益 dB
--normalize       峰值归一化
--no-denoise      跳过降噪
```

字幕参数：
```
--model           Whisper 模型（默认 medium）
--fontsize        字幕字号（默认 24）
--margin          字幕距底部边距（默认 30）
--burn-from-srt   直接使用已有 SRT 烧录
--keep-tmp        保留临时文件
```

## Whisper 模型选择

### 模型对比

| 模型 | 参数量 | 磁盘占用 | 中文 WER(↓) | 速度 | 推荐场景 |
|------|--------|----------|-------------|------|----------|
| `tiny` | 39M | ~151MB | ~25-30% | 极快 | 快速测试、实时转写 |
| `base` | 74M | ~145MB | ~20-25% | 快 | 简单场景、资源受限设备 |
| `small` | 244M | ~461MB | ~14-18% | 较快 | 日常使用 |
| `medium` | 769M | ~1.5GB | ~10-14% | 中等 | **推荐**，通用场景首选 |
| `turbo` | 809M | ~809MB | ~10-13% | 快 | 速度与准确度的平衡 |
| `large-v3` | 1.55B | ~2.9GB | ~7-10% | 慢 | 专业场景，追求最高准确度 |

> 中文 WER (词错误率) 为大致参考值，实际效果受音频质量、口音、背景噪声等因素影响。数据基于 Common Voice 等公开数据集的测试参考。

### 准确度说明

- **口音与方言**：Whisper 对标准普通话识别效果最好，方言和重口音场景建议使用 `medium` 或以上模型。
- **背景噪声**：有背景噪声的音频建议先使用 `audio_denoise.py` 降噪处理，能显著提升识别准确率。`tiny`/`base` 对噪声较敏感，`medium` 及以上抗噪能力更强。
- **专业术语**：对于技术、医学等专业领域的专有名词，`large-v3` 由于训练数据更丰富，识别效果明显优于小模型。
- **长音频**：长时间音频（>10 分钟）建议使用 `medium` 或 `turbo`，在准确度和内存占用之间取得平衡。`large-v3` 在长音频上可能消耗大量内存。

### 中文识别建议

| 场景 | 推荐模型 | 原因 |
|------|----------|------|
| 清晰录音、标准普通话 | `small` / `turbo` | 速度与准确度兼顾 |
| 有轻微噪声、语速较快 | `medium` | **通用推荐**，抗噪好 |
| 多人对话、口音较重 | `large-v3` | 最高识别准确率 |
| 实时转写、低配机器 | `tiny` / `base` | 速度快、占用少 |

首次运行会自动下载模型，之后缓存复用。

## 项目结构

```
voice_ssr/
├── audio_denoise.py      # 音频降噪工具
├── extract_subtitle.py   # 字幕提取与烧录工具
├── process_video.py      # 一键流水线
├── requirements.txt      # Python 依赖
├── LICENSE               # MIT 开源许可
└── README.md             # 本文件
```

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
