#!/usr/bin/env python3
"""MP4 视频音频降噪工具（谱门控 / Spectral Gating）

依赖（轻量）:
    noisereduce  - 谱门控降噪
    soundfile    - 音频文件读写
    numpy        - noisereduce 依赖

还需要系统安装 ffmpeg（PATH 中可用）。
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


# ── 配置 ──────────────────────────────────────────────
# 降噪强度（0.0 ~ 1.0，越大降噪越狠，默认 0.8）
DEFAULT_PROP_DECREASE = 0.8
# 噪声样本起始时间（秒），0 表示从音频开头截取
DEFAULT_NOISE_START = 0.0
# 噪声样本时长（秒）
DEFAULT_NOISE_DURATION = 1.0
# FFT 窗口大小（2 的幂次，越大频率分辨率越高，默认 1024）
DEFAULT_N_FFT = 1024
# 平稳噪声检测阈值（越小越激进，默认 1.5）
DEFAULT_N_STD_THRESH = 1.5
# 频率平滑（Hz），越大频谱越平滑但可能模糊细节
DEFAULT_FREQ_SMOOTH = 500
# 时间平滑（ms），越大时间上越平滑但可能产生拖尾
DEFAULT_TIME_SMOOTH = 50

# ── 降噪预设 ────────────────────────────────────────
# 预设覆盖 prop_decrease / n_std_thresh / freq_smooth / time_smooth
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

# ffmpeg 常见安装路径（Windows / Linux / macOS）
_FFMPEG_CANDIDATES = [
    "ffmpeg",                                # PATH 中
    "C:\\ffmpeg\\bin\\ffmpeg.exe",           # Windows 手动安装
    "C:\\ffmpeg\\ffmpeg.exe",
    "/usr/bin/ffmpeg",                       # Linux
    "/usr/local/bin/ffmpeg",                 # macOS / Linux
    "/opt/homebrew/bin/ffmpeg",              # macOS Homebrew (Apple Silicon)
]


def _find_ffmpeg() -> str:
    """自动查找 ffmpeg 可执行文件路径。"""
    # 1) 优先用 shutil.which 查 PATH
    path = shutil.which("ffmpeg")
    if path:
        return path
    # 2) 遍历候选路径
    for p in _FFMPEG_CANDIDATES:
        if os.path.isfile(p):
            return p
    # 3) 没找到
    print("[错误] 找不到 ffmpeg！")
    print("  请下载 https://ffmpeg.org 或系统包管理器安装后重试。")
    print(f"  已搜索以下路径: {', '.join(_FFMPEG_CANDIDATES[1:])}")
    sys.exit(1)


FFMPEG = _find_ffmpeg()


def run_cmd(cmd: list[str], desc: str = "") -> None:
    """执行外部命令，失败时打印错误并退出。"""
    # 替换命令中的 "ffmpeg" 为实际路径
    cmd = [FFMPEG if c == "ffmpeg" else c for c in cmd]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[错误] {desc} 失败")
        print(f"  stderr: {e.stderr.strip()}")
        sys.exit(1)


def extract_audio(video_path: Path, wav_path: Path) -> None:
    """从 MP4 中提取音频为 16kHz 单声道 WAV。"""
    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",               # 不要视频
            "-acodec", "pcm_s16le",
            "-ar", "16000",      # 16kHz 采样率
            "-ac", "1",          # 单声道
            str(wav_path),
        ],
        desc="提取音频",
    )


def denoise_audio(
    wav_path: Path,
    out_wav_path: Path,
    prop_decrease: float,
    stationary: bool = False,
    n_fft: int = DEFAULT_N_FFT,
    gain_db: float = 0.0,
    normalize: bool = False,
    noise_start: float = DEFAULT_NOISE_START,
    noise_duration: float = DEFAULT_NOISE_DURATION,
    n_std_thresh_stationary: float = DEFAULT_N_STD_THRESH,
    freq_mask_smooth_hz: int = DEFAULT_FREQ_SMOOTH,
    time_mask_smooth_ms: int = DEFAULT_TIME_SMOOTH,
) -> None:
    """对 WAV 执行谱门控降噪 + 音量调节。"""
    data, sr = sf.read(wav_path, dtype="float32")

    if data.ndim > 1:   # 多声道 → 取平均转为单声道
        data = data.mean(axis=1)

    # 从指定位置截取噪声参考
    noise_start_idx = int(sr * noise_start)
    noise_len = int(sr * noise_duration)
    noise_sample = data[noise_start_idx:noise_start_idx + noise_len]

    # 降噪前 RMS
    rms_before = float(np.sqrt(np.mean(data ** 2)))

    reduced = nr.reduce_noise(
        y=data,
        sr=sr,
        y_noise=noise_sample,
        prop_decrease=prop_decrease,
        stationary=stationary,
        n_fft=n_fft,
        n_std_thresh_stationary=n_std_thresh_stationary,
        freq_mask_smooth_hz=freq_mask_smooth_hz,
        time_mask_smooth_ms=time_mask_smooth_ms,
    )

    # 降噪后 RMS
    rms_denoised = float(np.sqrt(np.mean(reduced ** 2)))
    rms_denoise_change = (1.0 - rms_denoised / rms_before) * 100 if rms_before > 0 else 0.0

    # ── 音量调节 ──
    peak = float(np.max(np.abs(reduced)))
    if normalize and peak > 0:
        # 峰值归一化到 -1dBFS（留余量防削波）
        target_peak = 10 ** (-1.0 / 20)  # ≈ 0.891
        gain_ratio = target_peak / peak
        reduced = reduced * gain_ratio
        gain_applied_db = 20 * np.log10(gain_ratio)
    elif gain_db != 0.0:
        gain_ratio = 10 ** (gain_db / 20)
        # 防削波：增益后峰值不超过 1.0
        new_peak = peak * gain_ratio
        if new_peak > 1.0:
            gain_ratio = 0.999 / peak
            gain_applied_db = 20 * np.log10(gain_ratio)
            print(f"  [注意] 指定增益会导致削波，已自动限制为 +{gain_applied_db:.1f} dB")
        else:
            gain_applied_db = gain_db
        reduced = reduced * gain_ratio
    else:
        gain_applied_db = 0.0

    # 最终 RMS
    rms_final = float(np.sqrt(np.mean(reduced ** 2)))

    sf.write(out_wav_path, reduced, sr)

    # 诊断输出
    print(f"  采样率: {sr} Hz | 时长: {len(data)/sr:.1f}s | FFT窗口: {n_fft}")
    print(f"  噪声模型: {'平稳（风扇/空调底噪）' if stationary else '非平稳（随机噪声）'}")
    print(f"  降噪:     RMS {rms_before:.6f} → {rms_denoised:.6f}  (降低 {rms_denoise_change:.1f}%)")
    if normalize:
        print(f"  归一化:   峰值 {peak:.4f} → {target_peak:.4f}  (增益 +{gain_applied_db:.1f} dB)")
    elif gain_applied_db != 0.0:
        print(f"  增益:     +{gain_applied_db:.1f} dB")
    print(f"  最终 RMS:  {rms_final:.6f}")

    if rms_denoise_change < 1.0:
        print(f"  [警告] 降噪几乎无变化！可能原因:")
        print(f"    1. 噪声样本段 ({noise_start}s~{noise_start + noise_duration}s) 不是纯噪声 → 试试 --noise-start 或 --preset aggressive")
        print(f"    2. 降噪强度太低 → 试试 -s 0.95 或 --preset aggressive")
        print(f"    3. 视频本身噪声很小 → 无需降噪")


def merge_audio(video_path: Path, wav_path: Path, out_path: Path) -> None:
    """将降噪后的音频合并回视频。"""
    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(wav_path),
            "-c:v", "copy",        # 视频流直接复制，不重新编码
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(out_path),
        ],
        desc="合并音视频",
    )


def main():
    parser = argparse.ArgumentParser(
        description="MP4 视频音频降噪（谱门控 Spectral Gating）",
    )
    parser.add_argument("input", help="输入 MP4 文件路径")
    parser.add_argument(
        "-o", "--output",
        help="输出路径（默认: 输入名_denoised.mp4）",
    )
    parser.add_argument(
        "--preset",
        choices=list(DENOISE_PRESETS.keys()),
        help="降噪预设方案: gentle（轻柔）/ normal（标准）/ aggressive（强力）",
    )
    parser.add_argument(
        "-s", "--strength",
        type=float,
        default=DEFAULT_PROP_DECREASE,
        help=f"降噪强度 0.0~1.0（默认 {DEFAULT_PROP_DECREASE}）",
    )
    parser.add_argument(
        "--stationary",
        action="store_true",
        help="使用平稳噪声模型（风扇、空调等持续底噪）",
    )
    parser.add_argument(
        "--n-fft",
        type=int,
        default=DEFAULT_N_FFT,
        help=f"FFT 窗口大小，2的幂次（默认 {DEFAULT_N_FFT}）",
    )
    parser.add_argument(
        "--noise-start",
        type=float,
        default=DEFAULT_NOISE_START,
        help=f"噪声样本起始时间，秒（默认 {DEFAULT_NOISE_START}，音频开头）",
    )
    parser.add_argument(
        "--noise-duration",
        type=float,
        default=DEFAULT_NOISE_DURATION,
        help=f"噪声样本时长，秒（默认 {DEFAULT_NOISE_DURATION}）",
    )
    parser.add_argument(
        "--n-std-thresh",
        type=float,
        default=DEFAULT_N_STD_THRESH,
        help=f"平稳噪声检测阈值，越小越激进（默认 {DEFAULT_N_STD_THRESH}）",
    )
    parser.add_argument(
        "--freq-smooth",
        type=int,
        default=DEFAULT_FREQ_SMOOTH,
        help=f"频率平滑 Hz，越大越平滑（默认 {DEFAULT_FREQ_SMOOTH}）",
    )
    parser.add_argument(
        "--time-smooth",
        type=int,
        default=DEFAULT_TIME_SMOOTH,
        help=f"时间平滑 ms，越大越平滑（默认 {DEFAULT_TIME_SMOOTH}）",
    )
    parser.add_argument(
        "-g", "--gain",
        type=float,
        default=0.0,
        help="音量增益（dB），如 -g 6 提升 6dB / -g -3 降低 3dB（默认 0）",
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="峰值归一化：自动将音量调到最大安全值（-1dBFS）",
    )
    parser.add_argument(
        "--keep-wav",
        action="store_true",
        help="保留中间 WAV 文件不删除",
    )
    args = parser.parse_args()

    video = Path(args.input).resolve()
    if not video.is_file():
        print(f"[错误] 文件不存在: {video}")
        sys.exit(1)

    output = Path(args.output) if args.output else video.with_stem(video.stem + "_denoised")

    # ── 应用预设（命令行单独指定的参数不覆盖）────
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

    # 临时 WAV 文件（同输出目录）
    wav_raw = output.with_suffix(".raw.wav")
    wav_denoised = output.with_suffix(".denoised.wav")

    print(f"输入: {video}")
    print(f"输出: {output}")
    if args.preset:
        print(f"降噪预设: {args.preset}")
    print(f"降噪强度: {args.strength}")
    print(f"噪声样本: {args.noise_start}s ~ {args.noise_start + args.noise_duration}s")
    if args.normalize:
        print(f"音量: 峰值归一化")
    elif args.gain != 0:
        print(f"音量增益: {args.gain:+.1f} dB")

    # 1) 提取音频
    print("\n[1/3] 提取音频...")
    extract_audio(video, wav_raw)

    # 2) 降噪
    print("[2/3] 降噪处理...")
    denoise_audio(wav_raw, wav_denoised, args.strength, args.stationary, args.n_fft,
                  args.gain, args.normalize, args.noise_start, args.noise_duration,
                  args.n_std_thresh, args.freq_smooth, args.time_smooth)

    # 3) 合并回去
    print("[3/3] 合并音视频...")
    merge_audio(video, wav_denoised, output)

    # 清理临时文件
    if not args.keep_wav:
        wav_raw.unlink(missing_ok=True)
        wav_denoised.unlink(missing_ok=True)

    print(f"\n[完成] {output}")


if __name__ == "__main__":
    main()
