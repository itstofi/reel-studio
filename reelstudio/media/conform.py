"""Conform chain: his footage -> mirrored, SDR Rec.709, tv range, canvas size, CFR.

HARD RULES: hflip every take of HIS footage here (never on a finished video — that
mirrors captions and titles); HLG / Dolby Vision is tone-mapped to SDR before anything
else touches it. Every chain ends with setparams because ffmpeg 9's -color_* output
flags do not tag untagged frames.
Decoding stays in software: VideoToolbox hardware frames skip autorotate.
"""
from __future__ import annotations

from pathlib import Path

from ..config import SETTINGS
from ..models import HDRKind, MediaInfo, Take
from ..util.proc import ProgressFn, run_ffmpeg

TONEMAP = "mobius"   # hable looks dull on HLG (0.75 -> Y 182); mobius -> 218. Calibrate on a real take.
NPL = 100
TAG_709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def video_chain(info: MediaInfo | None, *, mirror: bool, width: int, height: int, fps: int,
                out_pix_fmt: str = "yuv420p") -> str:
    f = [f"fps={fps}"]
    if mirror:
        f.append("hflip")
    hdr = info.hdr if info else HDRKind.sdr
    if hdr != HDRKind.sdr:
        f += [f"zscale=t=linear:npl={NPL}", "format=gbrpf32le", "zscale=p=bt709",
              f"tonemap=tonemap={TONEMAP}:desat=0", "zscale=t=bt709:m=bt709:r=tv:d=ordered"]
    elif info is not None and info.color_range == "pc":
        f.append("scale=in_range=full:out_range=limited:in_color_matrix=bt709:out_color_matrix=bt709")
    if info is None or (info.width, info.height) != (width, height):
        f += [f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
              f"crop={width}:{height}"]
    f += [f"format={out_pix_fmt}", TAG_709]
    return ",".join(f)


def extract_audio(take: Take, out: Path, settings=SETTINGS) -> Path:
    """48 kHz mono float WAV for analysis (the speech map is measured on this)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", take.source_path, "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", str(out)],
               ffmpeg=settings.ffmpeg)
    return out


def make_proxy(take: Take, out: Path, settings=SETTINGS, progress: ProgressFn | None = None) -> Path:
    """1080x1920 / 30 fps H.264 proxy with the full conform (mirrored + SDR), for the UI
    and for face measurement on the image he will actually be seen in."""
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = video_chain(take.info, mirror=take.mirror, width=1080, height=1920, fps=30)
    run_ffmpeg(["-i", take.source_path, "-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", "8M",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(out)],
               ffmpeg=settings.ffmpeg, total_s=take.info.duration if take.info else None, progress=progress)
    return out
