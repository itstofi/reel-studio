"""Reference clip of HIS voice for cloning: the cleanest continuous speech from the chosen
attempts (fallback: the longest blocks), with the mic's digital-zero gate gaps squeezed
out and the level normalised."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..media.audioio import SR, load_cached, slice_s, write_wav
from ..models import Block, Plan, Project
from ..store import ProjectStore, read_json


def pick_reference(project: Project, store: ProjectStore, lang: str, seconds: float = 12.0) -> Path:
    pp = store.paths(project.id)
    out = pp.voice_dir / f"ref_{lang}_{int(seconds)}s.wav"
    if out.exists():
        return out
    takes = [t for t in project.takes if (t.lang or project.settings.lang) == lang and t.audio_path] or \
        [t for t in project.takes if t.audio_path]
    if not takes:
        raise RuntimeError("no analysed takes to take his voice from — run ingest + analyze")
    spans: list[tuple[str, float, float]] = []
    if pp.plan.exists() and lang == project.settings.lang:
        plan = read_json(pp.plan, Plan)
        spans = [(s.take_id, s.src_in, s.src_out) for b in plan.beats if b.enabled for s in b.segments]
    if not spans:
        for t in takes:
            if pp.blocks(t.id).exists():
                spans += [(t.id, b.start, b.end) for b in read_json(pp.blocks(t.id), list[Block])]
        spans.sort(key=lambda s: s[2] - s[1], reverse=True)
    pieces, total = [], 0.0
    for take_id, a, b in spans:
        take = project.take(take_id)
        if not take.audio_path:
            continue
        x = slice_s(load_cached(take.audio_path, SR), SR, a, b)
        x = _squeeze_silence(x)
        pieces.append(x)
        total += len(x) / SR
        if total >= seconds:
            break
    audio = np.concatenate(pieces)[: int(seconds * SR)] if pieces else np.zeros(SR, np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    return write_wav(out, audio * (0.89 / peak), SR)


def _squeeze_silence(x: np.ndarray, max_gap_s: float = 0.25) -> np.ndarray:
    """Shorten digital-zero runs (his gate) to max_gap_s so the clone hears speech, not holes."""
    silent = np.abs(x) < 1e-4
    out, run, limit = [], 0, int(max_gap_s * SR)
    for i, s in enumerate(silent):
        run = run + 1 if s else 0
        if run <= limit:
            out.append(i)
    return x[np.array(out, dtype=int)] if out else x
