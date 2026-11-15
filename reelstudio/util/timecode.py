"""Frame/second conversions. Timeline boundaries are integer frames so durations
never drift; a tiny epsilon keeps 2.5000000001 * 60 from becoming frame 151."""
from __future__ import annotations

import math

_EPS = 1e-6


def to_frames(t: float, fps: int) -> int:
    return int(round(t * fps))


def floor_frames(t: float, fps: int) -> int:
    return int(math.floor(t * fps + _EPS))


def ceil_frames(t: float, fps: int) -> int:
    return int(math.ceil(t * fps - _EPS))


def frames_to_s(frames: int, fps: int) -> float:
    return frames / fps


def fmt_tc(t: float) -> str:
    """Seconds -> M:SS.mmm for logs and the UI."""
    sign = "-" if t < 0 else ""
    t = abs(t)
    m, s = divmod(t, 60)
    return f"{sign}{int(m)}:{s:06.3f}"
