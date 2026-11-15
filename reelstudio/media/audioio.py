"""Audio I/O via ffmpeg pipes (no soundfile dependency).

Takes are decoded once to 48 kHz mono float WAV at ingest; analysis slices numpy
arrays instead of re-running ffmpeg per block.
"""
from __future__ import annotations

import functools
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from ..config import SETTINGS
from ..util.proc import run

SR = 48000          # analysis sample rate
ASR_SR = 16000      # whisper sample rate


def read_audio(path: Path | str, sr: int = SR, start: float | None = None,
               end: float | None = None) -> np.ndarray:
    """Decode any audio/video file to mono float32 at ``sr``."""
    args: list[str] = [SETTINGS.ffmpeg, "-v", "error", "-nostdin"]
    if start is not None:
        args += ["-ss", f"{max(0.0, start):.6f}"]
    args += ["-i", str(path)]
    if end is not None:
        dur = end - (start or 0.0)
        args += ["-t", f"{max(0.0, dur):.6f}"]
    args += ["-vn", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    out = run(args).stdout
    return np.frombuffer(out, dtype="<f4").copy()


@functools.lru_cache(maxsize=4)
def _cached(path: str, mtime: float, sr: int) -> np.ndarray:
    return read_audio(path, sr=sr)


def load_cached(path: Path | str, sr: int = SR) -> np.ndarray:
    """Whole-file decode, cached per (path, mtime) — analysis reuses one decode."""
    p = Path(path)
    return _cached(str(p), p.stat().st_mtime, sr)


def slice_s(samples: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    a = max(0, int(round(start * sr)))
    b = min(len(samples), int(round(end * sr)))
    return samples[a:b] if b > a else samples[0:0]


def to_asr(samples: np.ndarray, sr: int = SR) -> np.ndarray:
    """Resample to 16 kHz float32 for whisper (polyphase, anti-aliased)."""
    if sr == ASR_SR:
        return samples.astype(np.float32, copy=False)
    from math import gcd
    g = gcd(sr, ASR_SR)
    return resample_poly(samples, ASR_SR // g, sr // g).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> Path:
    """Write 16-bit PCM mono WAV (stdlib; float WAV is not supported by `wave`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def duration_of(path: Path | str) -> float:
    """Container duration in seconds via ffprobe."""
    out = run([SETTINGS.ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)]).stdout.decode().strip()
    try:
        return float(out)
    except ValueError:
        return 0.0
