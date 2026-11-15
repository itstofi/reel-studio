"""Speech map: where he is actually talking, measured at 10 ms.

His mic hard-gates every pause to true digital zero and real speech sits at
-39..-46 dB, so presence uses a -52 dB floor (-38 deleted words). Cut points come
from this map, never from whisper word times (whisper stretches a word's end across
a dropout). Blocks come from this map; each block is then isolate-transcribed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..config import SETTINGS, SpeechSpec
from ..models import Block, Span, SpeechMap
from .audioio import SR, load_cached


def frame_levels(samples: np.ndarray, sr: int, frame_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame RMS level (dBFS) and absolute peak. A trailing partial frame counts
    when it is at least half a frame long."""
    n = max(1, int(round(sr * frame_s)))
    count = len(samples) // n
    tail = len(samples) - count * n
    if count == 0 and tail == 0:
        return np.zeros(0), np.zeros(0)
    body = samples[: count * n].astype(np.float64).reshape(count, n) if count else np.zeros((0, n))
    rms = np.sqrt(np.mean(body ** 2, axis=1)) if count else np.zeros(0)
    peak = np.max(np.abs(body), axis=1) if count else np.zeros(0)
    if tail >= n // 2:
        last = samples[count * n:].astype(np.float64)
        rms = np.append(rms, np.sqrt(np.mean(last ** 2)))
        peak = np.append(peak, np.max(np.abs(last)))
    db = 20.0 * np.log10(rms + 1e-12)
    return db, peak


def runs_from_mask(mask: np.ndarray, frame_s: float) -> list[Span]:
    """Contiguous True runs of a per-frame mask -> time spans."""
    if mask.size == 0:
        return []
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    starts, ends = edges[0::2], edges[1::2]
    return [Span(start=round(s * frame_s, 4), end=round(e * frame_s, 4)) for s, e in zip(starts, ends)]


def merge_spans(spans: list[Span], max_gap: float) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if out and s.start - out[-1].end < max_gap:
            out[-1] = Span(start=out[-1].start, end=max(out[-1].end, s.end))
        else:
            out.append(Span(start=s.start, end=s.end))
    return out


def _runs_from_db(db: np.ndarray, spec: SpeechSpec) -> list[Span]:
    spans = runs_from_mask(db > spec.floor_db, spec.frame_s)
    spans = merge_spans(spans, spec.merge_run_gap_s)
    return [s for s in spans if s.dur >= spec.min_run_s]


def speech_map_array(samples: np.ndarray, sr: int = SR, spec: SpeechSpec | None = None) -> list[Span]:
    """Speech runs for any mono float array (QA reuses this on the delivered audio)."""
    spec = spec or SETTINGS.speech
    db, _ = frame_levels(samples, sr, spec.frame_s)
    return _runs_from_db(db, spec)


def speech_map(take_id: str, wav: Path, spec: SpeechSpec | None = None) -> SpeechMap:
    spec = spec or SETTINGS.speech
    samples = load_cached(wav, SR)
    db, peak = frame_levels(samples, SR, spec.frame_s)
    runs = _runs_from_db(db, spec)
    zero_ratio = float(np.mean(peak == 0.0)) if peak.size else 0.0
    quiet = db[(db <= spec.floor_db) & (peak > 0.0)]
    noise = float(np.percentile(quiet, 10)) if quiet.size else None
    return SpeechMap(take_id=take_id, duration=len(samples) / SR, frame_s=spec.frame_s,
                     floor_db=spec.floor_db, runs=runs, noise_floor_db=noise,
                     digital_zero_ratio=round(zero_ratio, 4))


def blocks_from_map(sm: SpeechMap, spec: SpeechSpec | None = None) -> list[Block]:
    """Group speech runs into blocks split by silences >= block_gap_s."""
    spec = spec or SETTINGS.speech
    groups: list[list[Span]] = []
    for run in sm.runs:
        if groups and run.start - groups[-1][-1].end < spec.block_gap_s:
            groups[-1].append(run)
        else:
            groups.append([run])
    blocks: list[Block] = []
    for group in groups:
        if sum(r.dur for r in group) < spec.min_block_speech_s:
            continue  # breath / click / blip
        blocks.append(Block(id=f"{sm.take_id}_b{len(blocks) + 1:03d}", take_id=sm.take_id,
                            start=group[0].start, end=group[-1].end, runs=group))
    return blocks


def gaps_between(runs: list[Span], start: float, end: float) -> list[Span]:
    """Silences between consecutive speech runs that lie inside [start, end]."""
    inside = [r for r in runs if r.end > start and r.start < end]
    return [Span(start=a.end, end=b.start) for a, b in zip(inside, inside[1:]) if b.start > a.end]


def run_containing(runs: list[Span], t: float, tol: float = 0.0) -> int | None:
    """Index of the run containing time t (with tolerance), else None."""
    for i, r in enumerate(runs):
        if r.start - tol <= t <= r.end + tol:
            return i
    return None
