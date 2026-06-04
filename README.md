# Voice SSR

视频处理工具箱：音频降噪 + 中文字幕提取与烧录。

## 功能概览

### 三个脚本对比

|  | `audio_denoise.py` | `extract_subtitle.py` | `process_video.py` |
|---|---|---|---|
| **功能** | 音频降噪 | 字幕识别 + 烧录 | 降噪 → 字幕 → 烧录（一键） |
| **输入** | MP4 视频 | MP4 视频 / SRT 文件 | MP4 视频 / SRT 文件 |
| **输出** | 降噪后的 MP4 | 含字幕的 MP4 + SRT 文件 | 处理完成的 MP4 |
| **降噪** | ✅ 核心功能 | ❌ | ✅ 可开关 |
| **语音识别** | ❌ | ✅ Whisper | ✅ Whisper |
| **字幕烧录** | ❌ | ✅ ffmpeg | ✅ ffmpeg |
| **生成 SRT** | ❌ | ✅ | ✅（可选 `--srt-only`） |
| **独立运行** | ✅ | ✅ | ✅ |
| **降噪参数** | 全部可调 | 不涉及 | 全部可调（透传） |
| **字幕参数** | 不涉及 | 全部可调 | 全部可调（透传） |
| **颜色/字体** | 不涉及 | ✅ `--font-color` `--back-color` `--font-name` | ✅ 同左 |
| **体积增益** | 不涉及 | ❌ | ✅ `--gain` `--normalize` |
| **典型命令** | `python audio_denoise.py in.mp4 -o out.mp4` | `python extract_subtitle.py in.mp4 -o out.mp4` | `python process_video.py in.mp4` |

### 使用场景选择

| 你的需求 | 推荐脚本 |
|---|---|
| 只想去除背景噪声 | `audio_denoise.py` |
| 只想添加字幕，音频干净 | `extract_subtitle.py` |
| 需要降噪 + 字幕一步到位 | `process_video.py` |
| 手动修正字幕文本后重新烧录 | `extract_subtitle.py --burn-from-srt` |
| 降噪 + 字幕，但不烧录（仅出 SRT） | `process_video.py --srt-only` |
| 音频噪声大，先降噪再对识别结果微调 | 先用 `audio_denoise.py`，再用 `extract_subtitle.py` 识别 |

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
--preset          降噪预设: gentle（轻柔）/ normal（标准）/ aggressive（强力）
-s, --strength    降噪强度 0.0~1.0（默认 0.8）
--stationary      使用平稳噪声模型（适用于风扇、空调等持续底噪）
--n-fft           FFT 窗口大小，2 的幂次（默认 1024）
--noise-start     噪声样本起始时间，秒（默认 0，音频开头）
--noise-duration  噪声样本时长，秒（默认 1.0）
--n-std-thresh    平稳噪声检测阈值，越小越激进（默认 1.5）
--freq-smooth     频率平滑 Hz，越大越平滑（默认 500）
--time-smooth     时间平滑 ms，越大越平滑（默认 50）
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
--model           Whisper 模型: tiny/base/small/medium/large-v3/turbo（默认 medium）
--fontsize        字幕字号（默认 24）
--font-name       字幕字体（默认 Microsoft YaHei，微软雅黑）
--font-color      字幕前景色，&HAABBGGRR 格式（默认 &H00FFFFFF，白色）
--back-color      字幕背景/描边色，&HAABBGGRR 格式（默认 &H00000000，黑色）
--margin          字幕距底部边距（默认 30）
--srt-only        只生成 SRT，不烧录
--burn-from-srt   跳过识别，直接用已有 SRT 烧录
--keep-wav        保留中间 WAV 文件
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
--preset          降噪预设: gentle（轻柔）/ normal（标准）/ aggressive（强力）
-s, --strength    降噪强度 0~1（默认 1.0）
--stationary      平稳噪声模型
--n-fft           FFT 窗口大小（默认 1024）
--noise-start     噪声样本起始时间，秒（默认 0）
--noise-duration  噪声样本时长，秒（默认 2.0）
--n-std-thresh    平稳噪声检测阈值，越小越激进（默认 1.5）
--freq-smooth     频率平滑 Hz，越大越平滑（默认 500）
--time-smooth     时间平滑 ms，越大越平滑（默认 50）
-g, --gain        音量增益 dB
--normalize       峰值归一化
--no-denoise      跳过降噪
```

字幕参数：
```
--model           Whisper 模型（默认 medium）
--fontsize        字幕字号（默认 24）
--font-name       字幕字体（默认 Microsoft YaHei，微软雅黑）
--font-color      字幕前景色，&HAABBGGRR 格式（默认 &H00FFFFFF，白色）
--back-color      字幕背景/描边色，&HAABBGGRR 格式（默认 &H00000000，黑色）
--margin          字幕距底部边距（默认 30）
--burn-from-srt   直接使用已有 SRT 烧录
--keep-tmp        保留临时文件
```

### 字幕颜色格式

颜色参数使用 ffmpeg 的 `&HAABBGGRR` 十六进制格式：

| 参数位置 | 含义 | 范围 |
|----------|------|------|
| `AA` | 透明度 | `00`（不透明）~ `FF`（全透明） |
| `BB` | 蓝色分量 | `00` ~ `FF` |
| `GG` | 绿色分量 | `00` ~ `FF` |
| `RR` | 红色分量 | `00` ~ `FF` |

**常用颜色示例：**

| 颜色 | 值 |
|------|-----|
| 白色 | `&H00FFFFFF` |
| 黑色 | `&H00000000` |
| 红色 | `&H000000FF` |
| 绿色 | `&H0000FF00` |
| 蓝色 | `&H00FF0000` |
| 黄色 | `&H0000FFFF` |
| 灰色 | `&H00808080` |

> **注意**：BB、GG、RR 顺序与常规 RGB 相反，书写时蓝在前、红在后。

## 降噪调优

### 预设方案

通过 `--preset` 快速切换三种预设：

| 预设 | 强度 | 检测阈值 | 频率平滑 | 时间平滑 | 适用场景 |
|------|------|----------|----------|----------|----------|
| `gentle` | 0.3 | 2.5 | 200 Hz | 100 ms | 轻微底噪，保留更多语音细节 |
| `normal` | 0.8 | 1.5 | 500 Hz | 50 ms | **默认**，通用场景 |
| `aggressive` | 1.0 | 1.0 | 800 Hz | 25 ms | 强噪声环境，最大限度去噪 |

```bash
# 使用强力预设
python audio_denoise.py input.mp4 --preset aggressive

# 在预设基础上单独微调参数
python audio_denoise.py input.mp4 --preset aggressive --freq-smooth 400
```

### 参数调优指南

如果预设效果不理想，可以逐一调参：

| 参数 | 作用 | 调优方向 |
|------|------|----------|
| `--strength` / `-s` | 降噪力度 | 越大去噪越狠，但可能损伤语音 |
| `--stationary` | 噪声类型 | 风扇、空调等持续底噪建议开启 |
| `--n-fft` | FFT 窗口 | 增大提高频率分辨率（512/1024/2048），适合低频噪声 |
| `--noise-start` | 噪声参考位置 | 如果开头不是纯噪声，指定到视频中只有噪声的时间点 |
| `--noise-duration` | 噪声参考长度 | 一般 0.5~3 秒，太短噪声特征不够，太长可能含语音 |
| `--n-std-thresh` | 检测敏感度 | 降低（如 1.0）更激进地识别噪声；提高（如 2.5）更保守 |
| `--freq-smooth` | 频谱平滑 | 提高可减少"音乐噪声"伪影，但可能模糊频谱细节 |
| `--time-smooth` | 时间平滑 | 提高可减少瞬时伪影，但可能产生拖尾感 |

### 常见降噪场景示例

```bash
# 场景 1：空调/风扇持续底噪
python audio_denoise.py interview.mp4 --stationary --preset normal

# 场景 2：室外风噪、街道噪声（非平稳）
python audio_denoise.py outdoor.mp4 --preset aggressive --noise-start 0.5

# 场景 3：轻微电流声，保留语音细节
python audio_denoise.py meeting.mp4 --preset gentle --freq-smooth 800

# 场景 4：视频中间部分才是纯噪声，手动指定
python audio_denoise.py noisy.mp4 --noise-start 30.0 --noise-duration 2.0 --preset aggressive

# 场景 5：降噪后音量偏小，自动归一化
python audio_denoise.py input.mp4 --preset normal --normalize
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
