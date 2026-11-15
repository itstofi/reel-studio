"""ffprobe -> MediaInfo: display size after rotation, HDR kind, range, bit depth, fps.

Probe every take before grading: his newer rolls are HLG / BT.2020 / Dolby Vision
10-bit, and treating HLG as SDR looks flat and grey.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ..config import SETTINGS
from ..models import HDRKind, MediaInfo
from ..util.proc import run_json


def _fps(rate: str | None) -> tuple[float, str]:
    if not rate or rate in ("0/0", "0"):
        return 0.0, "0/1"
    try:
        fr = Fraction(rate)
        return float(fr), f"{fr.numerator}/{fr.denominator}"
    except (ValueError, ZeroDivisionError):
        return 0.0, "0/1"


def _bit_depth(pix_fmt: str) -> int:
    for depth in (16, 12, 10):
        if f"p{depth}" in pix_fmt or pix_fmt.endswith(f"{depth}le") or pix_fmt.endswith(f"{depth}be"):
            return depth
    return 8


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0))
    except ValueError:
        return 0


def _hdr(stream: dict) -> HDRKind:
    trc = (stream.get("color_transfer") or "").lower()
    dovi = next((sd for sd in stream.get("side_data_list", []) or []
                 if "dovi" in str(sd.get("side_data_type", "")).lower()), None)
    if trc == "arib-std-b67":
        return HDRKind.hlg
    if dovi is not None:
        compat = dovi.get("dv_bl_signal_compatibility_id")
        return HDRKind.hlg if compat == 4 else HDRKind.dolby_vision
    if trc == "smpte2084":
        return HDRKind.pq
    return HDRKind.sdr


def probe(path: Path | str, settings=SETTINGS) -> MediaInfo:
    data = run_json([settings.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValueError(f"{path}: no video stream")
    rot = _rotation(v)
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps, fps_r = _fps(v.get("avg_frame_rate") or v.get("r_frame_rate"))
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    pix = v.get("pix_fmt", "") or ""
    rng = v.get("color_range")
    if rng is None and pix.startswith("yuvj"):
        rng = "pc"
    return MediaInfo(
        path=str(path), duration=duration, width=w, height=h, rotation=rot, fps=fps, fps_rational=fps_r,
        vcodec=v.get("codec_name", ""), pix_fmt=pix, bit_depth=_bit_depth(pix),
        color_primaries=v.get("color_primaries"), color_trc=v.get("color_transfer"),
        color_space=v.get("color_space"), color_range=rng, hdr=_hdr(v),
        has_audio=a is not None, audio_codec=a.get("codec_name") if a else None,
        audio_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        audio_channels=a.get("channels") if a else None, size_bytes=int(fmt.get("size") or 0))
