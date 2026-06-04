#!/usr/bin/env python3
"""从视频音频提取中文字幕并烧录回视频

使用 OpenAI Whisper 做中文语音识别 → 生成 SRT 字幕 → 用 ffmpeg 烧录到视频上。

依赖:
    openai-whisper  - 语音识别
    ffmpeg          - 音频提取 / 字幕烧录

首次运行会下载 Whisper 模型（~500MB small / ~1.5GB medium），之后缓存复用。
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import whisper


# ── 配置 ──────────────────────────────────────────────
DEFAULT_MODEL = "medium"        # tiny/small/medium/large-v3，中文推荐 medium 以上
SRT_MAX_CHARS = 22             # 单条字幕最大字符数（中文自然语速约2~3秒）
MIN_SEG_OVERLAP = 0.05         # 重叠阈值（秒），相邻段重叠超过此值才合并，紧贴不合并

# 字幕样式默认配置（命令行参数可覆盖）
DEFAULT_FONT_NAME = "Microsoft YaHei"       # 字体名称（Windows 推荐微软雅黑）
DEFAULT_FONT_SIZE = 24                      # 字号
DEFAULT_FONT_COLOR = "&H00FFFFFF"           # 前景色（白色, &HAABBGGRR 格式）
DEFAULT_BACK_COLOR = "&H00000000"           # 背景/描边色（黑色, &HAABBGGRR 格式）

# ffmpeg 候选路径（复用 audio_denoise 的逻辑）
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

# 确保 whisper 内部 subprocess 也能找到 ffmpeg
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


# ── 步骤 1: 提取音频 ──────────────────────────────────
def extract_audio(video_path: Path, wav_path: Path) -> None:
    run_cmd(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_path)],
        desc="提取音频",
    )


# ── 步骤 2: Whisper 中文识别 ──────────────────────────
def transcribe(wav_path: Path, model_name: str) -> list[dict]:
    """返回 segments 列表，每个 dict 含 start/end/text。"""
    print(f"  加载 Whisper 模型: {model_name}")
    model = whisper.load_model(model_name)
    print(f"  开始语音识别...")
    result = model.transcribe(
        str(wav_path),
        language="zh",
        task="transcribe",
        verbose=False,
        word_timestamps=False,
    )
    segs = []
    for seg in result.get("segments", []):
        segs.append({
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": seg["text"].strip(),
        })
    return segs


def merge_overlapping_segments(segments: list[dict],
                                min_overlap: float = MIN_SEG_OVERLAP) -> list[dict]:
    """合并真正重叠的相邻片段。仅当后一段在前一段结束前 >min_overlap 秒就开始时才合并。"""
    if not segments:
        return segments

    merged = []
    current = segments[0].copy()

    for next_seg in segments[1:]:
        gap = next_seg["start"] - current["end"]
        if gap < -min_overlap:
            # B 在 A 结束前已经开始，真正重叠 → 合并
            current["end"] = max(current["end"], next_seg["end"])
            current["text"] = current["text"].rstrip("，。；,.!?") + "，" + next_seg["text"]
            print(f"  [合并] {current['start']:.1f}s~{current['end']:.1f}s (重叠{-gap:.2f}s)")
        else:
            merged.append(current)
            current = next_seg.copy()

    merged.append(current)

    if len(merged) < len(segments):
        print(f"  合并: {len(segments)} 段 → {len(merged)} 段（仅合并真正重叠）")
    return merged


# ── 步骤 3: 生成 SRT 字幕文件 ─────────────────────────
def _split_text(text: str, max_chars: int = SRT_MAX_CHARS) -> list[str]:
    """按自然语义断句，生成适合字幕显示的短句序列。

    断句优先级:
        1. 强标点（。！？）→ 必须断
        2. 中标点（，；：）→ 优先断
        3. 自然短语边界（虚词后）
        4. 超 max_chars 时在最近的弱断点处切
    """
    hard_breaks = set("。！？!?")
    soft_breaks = set("，、；：,;:")
    weak_words = set("的了吗呢吧啊呀嗯哦哈哟")
    weak_after = set("了着过")

    n = len(text)
    if n <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < n:
        remaining = n - start
        if remaining <= max_chars:
            chunks.append(text[start:])
            break

        search_end = min(start + max_chars, n)

        # 优先级1: 强标点
        best = -1
        for i in range(search_end - 1, start, -1):
            if text[i] in hard_breaks:
                best = i + 1
                break

        # 优先级2: 中标点
        if best == -1:
            for i in range(search_end - 1, start, -1):
                if text[i] in soft_breaks:
                    best = i + 1
                    break

        # 优先级3: 自然短语边界
        if best == -1:
            for i in range(search_end - 1, start, -1):
                ch = text[i]
                if ch in weak_words:
                    best = i + 1
                    break
                if ch in weak_after:
                    best = i + 1
                    break
                if ch == "的" and i > start:
                    best = i + 1
                    break

        # 优先级4: 硬切，至少保留3字
        if best == -1 or best - start < 3:
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


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(segments: list[dict], srt_path: Path) -> None:
    """生成标准 SRT 字幕文件，每屏只显示一条（≤10字）。"""
    entries = _split_segment_into_entries(segments)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(entries, 1):
            f.write(f"{i}\n")
            f.write(f"{_format_time(seg['start'])} --> {_format_time(seg['end'])}\n")
            f.write(seg["text"] + "\n\n")
    print(f"  生成字幕: {srt_path}  ({len(segments)} 段 → {len(entries)} 条字幕)")


# ── 步骤 4: 烧录字幕到视频 ────────────────────────────
def burn_subtitle(video_path: Path, srt_path: Path, out_path: Path,
                  fontsize: int = DEFAULT_FONT_SIZE, margin: int = 30,
                  font_name: str = DEFAULT_FONT_NAME,
                  font_color: str = DEFAULT_FONT_COLOR,
                  back_color: str = DEFAULT_BACK_COLOR) -> None:
    """用 ffmpeg 将 SRT 字幕烧录到视频。"""
    # Windows 下路径冒号需转义：C:/... → C\:/...
    srt_escaped = srt_path.as_posix().replace(":", "\\:")
    vf = (
        f"subtitles='{srt_escaped}'"
        f":force_style='FontName={font_name},FontSize={fontsize},"
        f"PrimaryColour={font_color},OutlineColour={back_color},"
        f"MarginV={margin},Alignment=2,Outline=1,Shadow=1'"
    )
    run_cmd(
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-vf", vf,
         "-c:a", "copy",
         str(out_path)],
        desc="烧录字幕",
    )


# ── 主流程 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="从视频提取中文字幕并烧录")
    parser.add_argument("input", help="输入视频文件路径")
    parser.add_argument("-o", "--output", help="输出视频路径（默认: 输入名_subbed.mp4）")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Whisper 模型: tiny(151M)/base(145M)/small(461M)/"
                             f"medium(1.5G)/large-v3(2.9G)/turbo(809M)（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--srt-only", action="store_true",
                        help="只生成 SRT 字幕文件，不烧录到视频")
    parser.add_argument("--burn-from-srt", metavar="SRT文件",
                        help="跳过识别，直接用已有 SRT 文件烧录（支持手动修正后重新烧录）")
    parser.add_argument("--fontsize", type=int, default=DEFAULT_FONT_SIZE,
                        help=f"字幕字号（默认 {DEFAULT_FONT_SIZE}）")
    parser.add_argument("--font-name", default=DEFAULT_FONT_NAME,
                        help=f"字幕字体名称（默认 {DEFAULT_FONT_NAME}）")
    parser.add_argument("--font-color", default=DEFAULT_FONT_COLOR,
                        help=f"字幕前景色 &HAABBGGRR 格式（默认 {DEFAULT_FONT_COLOR}，白色）")
    parser.add_argument("--back-color", default=DEFAULT_BACK_COLOR,
                        help=f"字幕背景/描边色 &HAABBGGRR 格式（默认 {DEFAULT_BACK_COLOR}，黑色）")
    parser.add_argument("--margin", type=int, default=30,
                        help="字幕距底部边距（默认 30）")
    parser.add_argument("--keep-wav", action="store_true",
                        help="保留中间 WAV 文件")
    args = parser.parse_args()

    video = Path(args.input).resolve()
    if not video.is_file():
        print(f"[错误] 文件不存在: {video}")
        sys.exit(1)

    # ── 模式 A: 直接烧录已有 SRT（跳过识别）────
    if args.burn_from_srt:
        srt_path = Path(args.burn_from_srt).resolve()
        if not srt_path.is_file():
            print(f"[错误] SRT 文件不存在: {srt_path}")
            sys.exit(1)
        output = Path(args.output) if args.output else video.with_stem(video.stem + "_subbed")
        print(f"输入视频: {video}")
        print(f"SRT字幕:  {srt_path}")
        print(f"输出:     {output}")
        print()
        print("[烧录] 字幕烧录到视频...")
        burn_subtitle(video, srt_path, output, args.fontsize, args.margin,
                      args.font_name, args.font_color, args.back_color)
        print(f"\n[完成] {output}")
        return

    # ── 模式 B: 完整流程（识别 + 生成 SRT + 烧录）───
    output = Path(args.output) if args.output else video.with_stem(video.stem + "_subbed")
    wav_path = output.with_suffix(".subtitle_tmp.wav")
    srt_path = output.with_suffix(".srt")

    print(f"输入:     {video}")
    print(f"Whisper:  {args.model}")
    print(f"字幕文件: {srt_path}（已保留，可手动修正）")
    if not args.srt_only:
        print(f"输出视频: {output}")
    print()

    # 1) 提取音频
    print("[1/4] 提取音频...")
    extract_audio(video, wav_path)

    # 2) 语音识别
    print("[2/4] 语音识别...")
    segments = transcribe(wav_path, args.model)
    if not segments:
        print("[错误] 未识别到任何语音内容")
        sys.exit(1)
    total_text = sum(len(s["text"]) for s in segments)
    print(f"  识别完成: {len(segments)} 段, 共 {total_text} 字")

    # 2.5) 合并重叠/紧贴片段，消除重复显示
    segments = merge_overlapping_segments(segments)

    # 3) 生成 SRT
    print("[3/4] 生成字幕文件...")
    generate_srt(segments, srt_path)

    # 4) 烧录（可选）
    if args.srt_only:
        print("\n[完成] 仅生成字幕: " + str(srt_path))
        print("\n[提示] 如需修正识别错误:")
        print("   1. 用文本编辑器打开 SRT 文件修改文字和时间")
        print(f'   2. 重新烧录: python extract_subtitle.py "{video}" --burn-from-srt "{srt_path}"')
    else:
        print("[4/4] 烧录字幕到视频...")
        burn_subtitle(video, srt_path, output, args.fontsize, args.margin,
                      args.font_name, args.font_color, args.back_color)
        print(f"\n[完成] {output}")
        print(f"\n[提示] SRT 字幕已保留: {srt_path}")
        print("   如需修正识别错误:")
        print("   1. 用文本编辑器打开 SRT 文件修改文字和时间")
        print(f'   2. 重新烧录: python extract_subtitle.py "{video}" --burn-from-srt "{srt_path}"')

    # 清理
    if not args.keep_wav:
        wav_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
