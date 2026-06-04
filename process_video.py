#!/usr/bin/env python3
"""视频处理一键流水线：降噪 + 中文字幕提取 + 烧录

流程:
    1. 提取原始音频
    2. 谱门控降噪 + 音量归一化
    3. 降噪后音频合并回视频 → 降噪版视频
    4. Whisper 中文语音识别 → SRT 字幕
    5. 字幕烧录到降噪版视频 → 最终输出

依赖:
    noisereduce>=3.0  soundfile>=0.12  numpy>=1.24  openai-whisper  ffmpeg
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import noisereduce as nr
import numpy as np
import soundfile as sf
import whisper


# ── 配置 ──────────────────────────────────────────────
DEFAULT_PROP_DECREASE = 1.0   # 降噪强度
DEFAULT_NOISE_START = 0.0     # 噪声样本起始时间（秒）
DEFAULT_NOISE_DURATION = 2.0  # 噪声参考时长（秒）
DEFAULT_N_FFT = 1024          # FFT 窗口
DEFAULT_N_STD_THRESH = 1.5    # 平稳噪声检测阈值
DEFAULT_FREQ_SMOOTH = 500     # 频率平滑 Hz
DEFAULT_TIME_SMOOTH = 50      # 时间平滑 ms
DEFAULT_MODEL = "medium"      # Whisper 模型（tiny/small/medium/large-v3，中文推荐 medium+）
SRT_MAX_CHARS = 22            # 单条字幕最大字符数（中文自然语速约2~3秒）
MIN_SEG_OVERLAP = 0.05         # 重叠阈值（秒），相邻段重叠超过此值才合并，紧贴不回合并

# 字幕样式默认配置（命令行参数可覆盖）
DEFAULT_FONT_NAME = "Microsoft YaHei"       # 字体名称（Windows 推荐微软雅黑）
DEFAULT_FONT_SIZE = 24                      # 字号
DEFAULT_FONT_COLOR = "&H00FFFFFF"           # 前景色（白色, &HAABBGGRR 格式）
DEFAULT_BACK_COLOR = "&H00000000"           # 背景/描边色（黑色, &HAABBGGRR 格式）

# ── 降噪预设 ────────────────────────────────────────
DENOISE_PRESETS = {
    "gentle": {
        "prop_decrease": 0.3,
        "n_std_thresh_stationary": 2.5,
        "freq_mask_smooth_hz": 200,
        "time_mask_smooth_ms": 100,
    },
    "normal": {
        "prop_decrease": 0.8,
        "n_std_thresh_stationary": 1.5,
        "freq_mask_smooth_hz": 500,
        "time_mask_smooth_ms": 50,
    },
    "aggressive": {
        "prop_decrease": 1.0,
        "n_std_thresh_stationary": 1.0,
        "freq_mask_smooth_hz": 800,
        "time_mask_smooth_ms": 25,
    },
}

_FFMPEG_CANDIDATES = [
    "ffmpeg",
    "C:\\ffmpeg\\bin\\ffmpeg.exe",
    "C:\\ffmpeg\\ffmpeg.exe",
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
]


def _find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    for p in _FFMPEG_CANDIDATES:
        if os.path.isfile(p):
            return p
    print("[错误] 找不到 ffmpeg！")
    sys.exit(1)


FFMPEG = _find_ffmpeg()
_ffmpeg_dir = os.path.dirname(FFMPEG)
if _ffmpeg_dir and _ffmpeg_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


def run_cmd(cmd: list[str], desc: str = "") -> None:
    cmd = [FFMPEG if c == "ffmpeg" else c for c in cmd]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[错误] {desc} 失败")
        print(f"  stderr: {e.stderr.strip()}")
        sys.exit(1)


# ════════════════════════════════════════════════════════
#  阶段 A: 降噪
# ════════════════════════════════════════════════════════

def extract_audio(video_path: Path, wav_path: Path) -> None:
    run_cmd(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_path)],
        desc="提取音频",
    )


def denoise_audio(wav_path: Path, out_wav_path: Path,
                  prop_decrease: float, stationary: bool,
                  n_fft: int, normalize: bool, gain_db: float,
                  noise_start: float = DEFAULT_NOISE_START,
                  noise_duration: float = DEFAULT_NOISE_DURATION,
                  n_std_thresh_stationary: float = DEFAULT_N_STD_THRESH,
                  freq_mask_smooth_hz: int = DEFAULT_FREQ_SMOOTH,
                  time_mask_smooth_ms: int = DEFAULT_TIME_SMOOTH) -> None:
    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)

    noise_start_idx = int(sr * noise_start)
    noise_len = int(sr * noise_duration)
    noise_sample = data[noise_start_idx:noise_start_idx + noise_len]
    rms_before = float(np.sqrt(np.mean(data ** 2)))

    reduced = nr.reduce_noise(
        y=data, sr=sr, y_noise=noise_sample,
        prop_decrease=prop_decrease, stationary=stationary,
        n_fft=n_fft,
        n_std_thresh_stationary=n_std_thresh_stationary,
        freq_mask_smooth_hz=freq_mask_smooth_hz,
        time_mask_smooth_ms=time_mask_smooth_ms,
    )

    rms_denoised = float(np.sqrt(np.mean(reduced ** 2)))
    rms_change = (1.0 - rms_denoised / rms_before) * 100 if rms_before > 0 else 0.0

    # 音量调节
    peak = float(np.max(np.abs(reduced)))
    total_gain_db = 0.0
    if normalize and peak > 0:
        # 第一步: 峰值归一化到 -1dBFS
        target_peak = 10 ** (-1.0 / 20)
        norm_ratio = target_peak / peak
        reduced = reduced * norm_ratio
        peak = float(np.max(np.abs(reduced)))
        total_gain_db = 20 * np.log10(norm_ratio)
    if gain_db != 0.0:
        # 第二步: 叠加手动增益（防削波）
        gain_ratio = 10 ** (gain_db / 20)
        if peak * gain_ratio > 1.0:
            gain_ratio = 0.999 / peak
            gain_db = 20 * np.log10(gain_ratio)
            print(f"  [注意] 叠加增益会导致削波，已自动限制为 +{gain_db:.1f} dB")
        reduced = reduced * gain_ratio
        total_gain_db += gain_db

    rms_final = float(np.sqrt(np.mean(reduced ** 2)))
    sf.write(out_wav_path, reduced, sr)

    print(f"  采样率: {sr} Hz | 时长: {len(data)/sr:.1f}s")
    print(f"  噪声模型: {'平稳' if stationary else '非平稳'}")
    print(f"  降噪: RMS {rms_before:.6f} → {rms_denoised:.6f}  (降低 {rms_change:.1f}%)")
    if normalize or gain_db != 0:
        print(f"  增益: +{total_gain_db:.1f} dB")
    print(f"  最终 RMS: {rms_final:.6f}")

    if rms_change < 1.0:
        print(f"  [警告] 降噪几乎无变化！试试 --stationary 或 -s 0.95")


def merge_audio_to_video(video_path: Path, wav_path: Path, out_path: Path) -> None:
    """将降噪音频合并回视频，自动对齐：视频长度决定最终时长，音频不足补静音。"""
    run_cmd(
        ["ffmpeg", "-y",
         "-i", str(video_path), "-i", str(wav_path),
         "-c:v", "copy",
         "-filter_complex", "[1:a]apad[a]",
         "-map", "0:v:0", "-map", "[a]", "-shortest",
         str(out_path)],
        desc="合并降噪音频",
    )


# ════════════════════════════════════════════════════════
#  阶段 B: 语音识别 & 字幕
# ════════════════════════════════════════════════════════

def transcribe(wav_path: Path, model_name: str) -> list[dict]:
    print(f"  加载 Whisper 模型: {model_name}")
    model = whisper.load_model(model_name)
    print(f"  开始语音识别...")
    result = model.transcribe(
        str(wav_path), language="zh", task="transcribe",
        verbose=False, word_timestamps=False,
    )
    return [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result.get("segments", [])
    ]


def merge_overlapping(segments: list[dict],
                      min_overlap: float = MIN_SEG_OVERLAP) -> list[dict]:
    """合并真正重叠的相邻片段。仅当 B 在 A 结束前 >min_overlap 秒就开始时才合并。

    A: |=======|
    B:      |=======|    ← gap < 0 (overlap > min_overlap) → 合并
    A: |=======|
    B:        |=======|  ← gap = 0 (紧贴) → 不合并
    A: |=======|
    B:         |======|  ← gap > 0 (有间隔) → 不合并
    """
    if not segments:
        return segments
    merged = []
    cur = segments[0].copy()
    for nxt in segments[1:]:
        gap = nxt["start"] - cur["end"]
        if gap < -min_overlap:
            # B 在 A 结束前已经开始，真正重叠
            cur["end"] = max(cur["end"], nxt["end"])
            cur["text"] = cur["text"].rstrip("，。；,.!?") + "，" + nxt["text"]
            print(f"  [合并] {cur['start']:.1f}s~{cur['end']:.1f}s (重叠{-gap:.2f}s)")
        else:
            merged.append(cur)
            cur = nxt.copy()
    merged.append(cur)
    if len(merged) < len(segments):
        print(f"  合并: {len(segments)} 段 → {len(merged)} 段（仅合并真正重叠）")
    return merged


def _split_text(text: str, max_chars: int = SRT_MAX_CHARS) -> list[str]:
    """按自然语义断句，生成适合字幕显示的短句序列。

    断句优先级:
        1. 强标点（。！？）→ 必须断
        2. 中标点（，；：）→ 优先断
        3. 自然短语边界（"的"/"了"/"是"/"在"/"和"/"与"等虚词后）
        4. 超 max_chars 时在最近的弱断点处切
    """
    # 强断点: 句末标点，必然断句
    hard_breaks = set("。！？!?")
    # 中断点: 句中停顿，优先断句
    soft_breaks = set("，、；：,;:")
    # 弱断点: 虚词之后，可在此处断句
    weak_words = set("的了吗呢吧啊呀嗯哦哈哟")
    weak_after = set("了着过")

    n = len(text)
    if n <= max_chars:
        return [text]

    # 第一步: 在 max_chars 范围内找最佳断点
    chunks = []
    start = 0
    while start < n:
        remaining = n - start
        if remaining <= max_chars:
            chunks.append(text[start:])
            break

        # 搜索范围: start ~ start+max_chars
        search_end = min(start + max_chars, n)

        # 优先级1: 找强标点
        best = -1
        for i in range(search_end - 1, start, -1):
            if text[i] in hard_breaks:
                best = i + 1  # 标点留在当前 chunk
                break

        # 优先级2: 找中标点
        if best == -1:
            for i in range(search_end - 1, start, -1):
                if text[i] in soft_breaks:
                    best = i + 1
                    break

        # 优先级3: 找自然短语边界（虚词后、词间停顿）
        if best == -1:
            for i in range(search_end - 1, start, -1):
                ch = text[i]
                # 在虚词后断
                if ch in weak_words:
                    best = i + 1
                    break
                # 在"着/了/过"后断
                if ch in weak_after:
                    best = i + 1
                    break
                # 在"的"字后断（如"系统的"/"开源的"）
                if ch == "的" and i > start:
                    best = i + 1
                    break

        # 优先级4: 硬切，但尽量在2字以上（避免切散词）
        if best == -1 or best - start < 3:
            # 在 max_chars 处硬切，但保证至少 3 字
            best = max(start + 3, min(search_end, start + max_chars))

        chunks.append(text[start:best])
        start = best

    return chunks or [text]


def _split_segment_into_entries(segments: list[dict],
                                 max_chars: int = SRT_MAX_CHARS) -> list[dict]:
    """超长 segment 拆为多条独立 SRT 条目，时间戳按字符数均分。"""
    entries = []
    for seg in segments:
        text = seg["text"]
        if len(text) <= max_chars:
            entries.append(seg.copy())
            continue
        chunks = _split_text(text, max_chars)
        duration = seg["end"] - seg["start"]
        total_chars = sum(len(c) for c in chunks)
        t = seg["start"]
        for chunk in chunks:
            chunk_dur = duration * len(chunk) / total_chars
            entries.append({
                "start": t,
                "end": min(t + chunk_dur, seg["end"]),
                "text": chunk,
            })
            t += chunk_dur
    return entries


def _fmt_time(sec: float) -> str:
    h, m = divmod(int(sec), 3600)
    m, s = divmod(m, 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(segments: list[dict], srt_path: Path) -> None:
    entries = _split_segment_into_entries(segments)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(entries, 1):
            f.write(f"{i}\n")
            f.write(f"{_fmt_time(seg['start'])} --> {_fmt_time(seg['end'])}\n")
            f.write(seg["text"] + "\n\n")
    print(f"  字幕: {srt_path}  ({len(segments)} 段 → {len(entries)} 条字幕)")


def burn_subtitle(video_path: Path, srt_path: Path, out_path: Path,
                  fontsize: int = DEFAULT_FONT_SIZE, margin: int = 30,
                  font_name: str = DEFAULT_FONT_NAME,
                  font_color: str = DEFAULT_FONT_COLOR,
                  back_color: str = DEFAULT_BACK_COLOR) -> None:
    srt_escaped = srt_path.as_posix().replace(":", "\\:")
    vf = (f"subtitles='{srt_escaped}'"
          f":force_style='FontName={font_name},FontSize={fontsize},"
          f"PrimaryColour={font_color},OutlineColour={back_color},"
          f"MarginV={margin},Alignment=2,Outline=1,Shadow=1'")
    run_cmd(
        ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
         "-c:a", "copy", str(out_path)],
        desc="烧录字幕",
    )


# ════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="视频一键处理：降噪 + 中文字幕")
    parser.add_argument("input", help="输入视频文件路径")
    parser.add_argument("-o", "--output", help="最终输出路径（默认: 输入名_final.mp4）")
    parser.add_argument("--srt-only", action="store_true",
                        help="只降噪+生成SRT字幕，不烧录到视频")
    parser.add_argument("--srt-only-no-denoise", action="store_true",
                        help="只生成SRT字幕，跳过降噪")

    # 降噪参数
    g_denoise = parser.add_argument_group("降噪参数")
    g_denoise.add_argument("--preset", choices=list(DENOISE_PRESETS.keys()),
                           help="降噪预设方案: gentle（轻柔）/ normal（标准）/ aggressive（强力）")
    g_denoise.add_argument("-s", "--strength", type=float, default=DEFAULT_PROP_DECREASE,
                           help=f"降噪强度 0~1（默认 {DEFAULT_PROP_DECREASE}）")
    g_denoise.add_argument("--stationary", action="store_true",
                           help="平稳噪声模型（风扇/空调底噪）")
    g_denoise.add_argument("--n-fft", type=int, default=DEFAULT_N_FFT,
                           help=f"FFT 窗口大小（默认 {DEFAULT_N_FFT}）")
    g_denoise.add_argument("--noise-start", type=float, default=DEFAULT_NOISE_START,
                           help=f"噪声样本起始时间，秒（默认 {DEFAULT_NOISE_START}）")
    g_denoise.add_argument("--noise-duration", type=float, default=DEFAULT_NOISE_DURATION,
                           help=f"噪声样本时长，秒（默认 {DEFAULT_NOISE_DURATION}）")
    g_denoise.add_argument("--n-std-thresh", type=float, default=DEFAULT_N_STD_THRESH,
                           help=f"平稳噪声检测阈值，越小越激进（默认 {DEFAULT_N_STD_THRESH}）")
    g_denoise.add_argument("--freq-smooth", type=int, default=DEFAULT_FREQ_SMOOTH,
                           help=f"频率平滑 Hz，越大越平滑（默认 {DEFAULT_FREQ_SMOOTH}）")
    g_denoise.add_argument("--time-smooth", type=int, default=DEFAULT_TIME_SMOOTH,
                           help=f"时间平滑 ms，越大越平滑（默认 {DEFAULT_TIME_SMOOTH}）")
    g_denoise.add_argument("-g", "--gain", type=float, default=0.0,
                           help="音量增益 dB（默认 0）")
    g_denoise.add_argument("--normalize", action="store_true",
                           help="峰值归一化，自动调到最大安全音量")
    g_denoise.add_argument("--no-denoise", action="store_true",
                           help="跳过降噪，只做字幕")

    # 字幕参数
    g_sub = parser.add_argument_group("字幕参数")
    g_sub.add_argument("--model", default=DEFAULT_MODEL,
                       help="Whisper 模型: tiny(151M)/base(145M)/small(461M)/"
                            f"medium(1.5G)/large-v3(2.9G)/turbo(809M)（默认 {DEFAULT_MODEL}）")
    g_sub.add_argument("--fontsize", type=int, default=DEFAULT_FONT_SIZE,
                       help=f"字幕字号（默认 {DEFAULT_FONT_SIZE}）")
    g_sub.add_argument("--font-name", default=DEFAULT_FONT_NAME,
                       help=f"字幕字体名称（默认 {DEFAULT_FONT_NAME}）")
    g_sub.add_argument("--font-color", default=DEFAULT_FONT_COLOR,
                       help=f"字幕前景色 &HAABBGGRR 格式（默认 {DEFAULT_FONT_COLOR}，白色）")
    g_sub.add_argument("--back-color", default=DEFAULT_BACK_COLOR,
                       help=f"字幕背景/描边色 &HAABBGGRR 格式（默认 {DEFAULT_BACK_COLOR}，黑色）")
    g_sub.add_argument("--margin", type=int, default=30, help="字幕距底部边距（默认 30）")
    g_sub.add_argument("--burn-from-srt", metavar="SRT文件",
                       help="跳过识别，用已有 SRT 直接烧录到降噪版视频")
    g_sub.add_argument("--keep-tmp", action="store_true", help="保留临时文件")

    args = parser.parse_args()

    video = Path(args.input).resolve()
    if not video.is_file():
        print(f"[错误] 文件不存在: {video}")
        sys.exit(1)

    do_denoise = not args.no_denoise and not args.srt_only_no_denoise

    # ── 应用降噪预设（命令行单独指定的参数不覆盖）────
    if args.preset:
        preset = DENOISE_PRESETS[args.preset]
        if args.strength == DEFAULT_PROP_DECREASE:
            args.strength = preset["prop_decrease"]
        if args.n_std_thresh == DEFAULT_N_STD_THRESH:
            args.n_std_thresh = preset["n_std_thresh_stationary"]
        if args.freq_smooth == DEFAULT_FREQ_SMOOTH:
            args.freq_smooth = preset["freq_mask_smooth_hz"]
        if args.time_smooth == DEFAULT_TIME_SMOOTH:
            args.time_smooth = preset["time_mask_smooth_ms"]

    output = Path(args.output) if args.output else video.with_stem(video.stem + "_final")
    srt_path = output.with_suffix(".srt")

    # ── 模式: 只生成 SRT 不降噪 ──
    if args.srt_only_no_denoise:
        wav_path = output.with_suffix(".raw_tmp.wav")
        print(f"输入: {video}")
        print(f"模式: 仅生成字幕（不降噪）")
        print()
        print("[1/2] 提取音频...")
        extract_audio(video, wav_path)
        print("[2/2] 语音识别...")
        segs = transcribe(wav_path, args.model)
        if not segs:
            print("[错误] 未识别到任何语音内容")
            sys.exit(1)
        print(f"  识别: {len(segs)} 段, 共 {sum(len(s['text']) for s in segs)} 字")
        segs = merge_overlapping(segs)
        generate_srt(segs, srt_path)
        print(f"\n[完成] {srt_path}")
        if not args.keep_tmp:
            wav_path.unlink(missing_ok=True)
        return

    # ── 模式: 用已有 SRT 烧录到降噪版视频 ──
    if args.burn_from_srt:
        srt_input = Path(args.burn_from_srt).resolve()
        if not srt_input.is_file():
            print(f"[错误] SRT 文件不存在: {srt_input}")
            sys.exit(1)
        wav_raw = output.with_suffix(".raw_tmp.wav")
        wav_denoised = output.with_suffix(".denoised_tmp.wav")
        denoised_video = output.with_stem(video.stem + "_denoised_tmp")

        print(f"输入视频: {video}")
        print(f"SRT字幕:  {srt_input}")
        print()

        if do_denoise:
            print("[1/3] 提取音频并降噪...")
            extract_audio(video, wav_raw)
            denoise_audio(wav_raw, wav_denoised, args.strength, args.stationary,
                          args.n_fft, args.normalize, args.gain,
                          args.noise_start, args.noise_duration,
                          args.n_std_thresh, args.freq_smooth, args.time_smooth)
            print("[2/3] 合并降噪音频到视频...")
            merge_audio_to_video(video, wav_denoised, denoised_video)
            video_to_burn = denoised_video
            step = 3
        else:
            video_to_burn = video
            step = 1

        print(f"[{step}/{step}] 烧录字幕...")
        burn_subtitle(video_to_burn, srt_input, output, args.fontsize, args.margin,
                      args.font_name, args.font_color, args.back_color)
        print(f"\n[完成] {output}")

        if not args.keep_tmp:
            wav_raw.unlink(missing_ok=True) if 'wav_raw' in dir() else None
            wav_denoised.unlink(missing_ok=True) if 'wav_denoised' in dir() else None
            if do_denoise:
                denoised_video.unlink(missing_ok=True)
        return

    # ── 完整流水线: 降噪 → 识别 → 字幕 → 烧录 ──
    wav_raw = output.with_suffix(".raw_tmp.wav")
    wav_denoised = output.with_suffix(".denoised_tmp.wav")
    denoised_video = output.with_stem(video.stem + "_denoised_tmp")

    print(f"输入:     {video}")
    print(f"输出:     {output}")
    print(f"字幕:     {srt_path}（已保留）")
    print(f"降噪:     {'是' if do_denoise else '否'}  | 强度 {args.strength}")
    if args.normalize:
        print(f"音量:     峰值归一化")
    elif args.gain != 0:
        print(f"音量:     +{args.gain:.1f} dB")
    print(f"Whisper:  {args.model}")
    print()

    # 1) 提取音频
    print("[1/5] 提取原始音频...")
    extract_audio(video, wav_raw)

    # 2) 降噪
    if do_denoise:
        print("[2/5] 音频降噪...")
        denoise_audio(wav_raw, wav_denoised, args.strength, args.stationary,
                      args.n_fft, args.normalize, args.gain,
                      args.noise_start, args.noise_duration,
                      args.n_std_thresh, args.freq_smooth, args.time_smooth)

        print("[3/5] 合并降噪音频到视频...")
        merge_audio_to_video(video, wav_denoised, denoised_video)
        video_working = denoised_video
        audio_for_asr = wav_denoised
        step_label = 4
    else:
        video_working = video
        audio_for_asr = wav_raw
        step_label = 2

    # 3) 语音识别
    print(f"[{step_label}/5] 语音识别...")
    segments = transcribe(audio_for_asr, args.model)
    if not segments:
        print("[错误] 未识别到任何语音内容")
        sys.exit(1)
    print(f"  识别: {len(segments)} 段, 共 {sum(len(s['text']) for s in segments)} 字")
    segments = merge_overlapping(segments)

    # 4) 生成 SRT
    print(f"[{step_label+1}/5] 生成字幕...")
    generate_srt(segments, srt_path)

    # 5) 烧录
    if args.srt_only:
        print(f"\n[完成] 降噪视频: {denoised_video if do_denoise else video}")
        print(f"[完成] 字幕文件: {srt_path}")
        print("\n[提示] 如需修正识别错误后重新烧录:")
        print(f'  python process_video.py "{video}" --burn-from-srt "{srt_path}"')
    else:
        print(f"[5/5] 烧录字幕到降噪版视频...")
        burn_subtitle(video_working, srt_path, output, args.fontsize, args.margin,
                      args.font_name, args.font_color, args.back_color)
        print(f"\n[完成] {output}")
        print(f"\n[提示] SRT 已保留: {srt_path}")
        print(f"  修正后重新烧录:")
        print(f'  python process_video.py "{video}" --burn-from-srt "{srt_path}"')

    # 清理
    if not args.keep_tmp:
        wav_raw.unlink(missing_ok=True)
        if do_denoise:
            wav_denoised.unlink(missing_ok=True)
            if not args.srt_only:
                denoised_video.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
