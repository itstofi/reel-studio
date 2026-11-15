"""Loudness: two-pass loudnorm for delivery, ebur128 for QA of the delivered file."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import SETTINGS
from ..util.proc import run


def loudnorm_measure(input_args: list[str], audio_filter: str, settings=SETTINGS) -> dict:
    """Pass 1: run `audio_filter` (ending at the pre-normalisation mix) into loudnorm and
    return its JSON measurement. input_args are ffmpeg input options (-i ...)."""
    d = settings.delivery
    graph = f"{audio_filter},loudnorm=I={d.loudness_i}:TP={d.loudness_tp}:LRA={d.loudness_lra}:print_format=json"
    proc = run([settings.ffmpeg, "-hide_banner", "-nostdin", *input_args, "-filter_complex", graph,
                "-vn", "-f", "null", "-"])
    text = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        raise RuntimeError("loudnorm produced no measurement:\n" + text[-1500:])
    return json.loads(m.group(0))


def loudnorm_filter(meas: dict, settings=SETTINGS) -> str:
    """Pass 2 filter using the pass-1 measurement (linear mode keeps dynamics)."""
    d = settings.delivery
    return (f"loudnorm=I={d.loudness_i}:TP={d.loudness_tp}:LRA={d.loudness_lra}"
            f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
            f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
            f":offset={meas['target_offset']}:linear=true:print_format=summary,aresample=48000")


def measure(path: Path | str, settings=SETTINGS) -> dict:
    """Integrated loudness (LUFS), loudness range (LU) and true peak (dBTP) of a file."""
    proc = run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vn",
                "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = proc.stderr.decode("utf-8", "replace")
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text

    def grab(pattern: str) -> float | None:
        m = re.search(pattern, summary)
        return float(m.group(1)) if m else None

    return {"I": grab(r"I:\s*(-?[\d.]+|-inf)\s*LUFS"), "LRA": grab(r"LRA:\s*(-?[\d.]+)\s*LU"),
            "TP": grab(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS") or grab(r"Peak:\s*(-?[\d.]+)\s*dBFS")}
