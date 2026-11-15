"""Mid-phrase gap audit + A/B verification.

His mic hard-gates every pause to digital zero, so silence itself is normal. Only a
LONG gap INSIDE a phrase is a defect — and whisper hides it by stretching the previous
word across the dropout, so gaps are measured on the speech map, never on word times.

Closing a gap keeps pad_s of pause on each side (0.15 s clipped "engineers" to
"engines"; 0.25 s restored it). Every closure is A/B verified: transcribe the uncut and
the cut audio and compare the words. Any difference means the cut ate speech, so the
pause is kept — a long pause is always better than a lost word.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..config import SETTINGS, SpeechSpec
from ..media.audioio import SR, load_cached, slice_s, to_asr
from ..media.speech import gaps_between, run_containing
from ..models import GapIssue, SpeechMap, Word
from ..util.textnorm import ends_sentence, words as norm_words


def audit_gaps(take_id: str, seg: tuple[float, float], sm: SpeechMap, words: list[Word],
               spec: SpeechSpec | None = None) -> list[GapIssue]:
    """Internal silences >= long_gap_s inside the segment, classified by position."""
    spec = spec or SETTINGS.speech
    out: list[GapIssue] = []
    for g in gaps_between(sm.runs, seg[0], seg[1]):
        if g.end - g.start < spec.long_gap_s or g.start <= seg[0] or g.end >= seg[1]:
            continue
        before = [w for w in words if w.start < g.start]
        prev = max(before, key=lambda w: w.start) if before else None
        kind = "sentence" if prev is not None and ends_sentence(prev.text) else "mid_phrase"
        out.append(GapIssue(take_id=take_id, start=round(g.start, 4), end=round(g.end, 4), kind=kind,
                            action="close",
                            reason=f"{g.end - g.start:.2f}s silence after "
                                   f"\"{prev.text.strip() if prev else '?'}\""))
    return out


def split_segment(seg: tuple[float, float], gaps: list[GapIssue], pad_s: float) -> list[tuple[float, float]]:
    """Remove the middle of every gap marked `close`, keeping pad_s each side."""
    pieces: list[tuple[float, float]] = []
    cur = seg[0]
    for g in sorted((g for g in gaps if g.action == "close"), key=lambda g: g.start):
        head_end, tail_start = g.start + pad_s, g.end - pad_s
        if tail_start <= head_end or head_end <= cur or tail_start >= seg[1]:
            continue
        pieces.append((round(cur, 4), round(head_end, 4)))
        cur = tail_start
    pieces.append((round(cur, 4), round(seg[1], 4)))
    return pieces


def _widen(sm: SpeechMap, t: float, direction: int) -> float:
    """Move a window edge out of any speech run so no word is cut at the edge."""
    i = run_containing(sm.runs, t)
    if i is None:
        return t
    r = sm.runs[i]
    return max(0.0, r.start - 0.05) if direction < 0 else min(sm.duration, r.end + 0.05)


def ab_verify(gap: GapIssue, wav: Path, sm: SpeechMap, *, language: str | None, transcriber,
              pad_s: float | None = None, context_s: float = 1.5) -> tuple[bool, str]:
    """Transcribe the uncut window and the cut window; True when the words are identical."""
    pad_s = SETTINGS.speech.pad_s if pad_s is None else pad_s
    lo = _widen(sm, gap.start - context_s, -1)
    hi = _widen(sm, gap.end + context_s, +1)
    samples = load_cached(wav, SR)
    uncut = slice_s(samples, SR, lo, hi)
    cut = np.concatenate([slice_s(samples, SR, lo, gap.start + pad_s),
                          slice_s(samples, SR, gap.end - pad_s, hi)])
    w_uncut, _, _ = transcriber.transcribe(to_asr(uncut, SR), language=language, prompt=None)
    w_cut, _, _ = transcriber.transcribe(to_asr(cut, SR), language=language, prompt=None)
    t_cut = gap.start + pad_s - lo  # the cut sits at the same offset in both versions
    return compare_ab(w_uncut, w_cut, t_cut, t_cut)


def _tokens_with_ends(words) -> list[tuple[str, float]]:
    return [(t, w.end) for w in words for t in norm_words(w.text)]


def compare_ab(w_uncut, w_cut, cut_t_uncut: float, cut_t_cut: float, reach: int = 1) -> tuple[bool, str]:
    """A cut can only damage speech AT the cut. Any word difference within `reach` words of
    the cut point is a defect (this is how "engineers" became "engines"). Differences far
    from the cut, on identical audio, are whisper re-decoding noise: reported, not failed."""
    import difflib
    a, b = _tokens_with_ends(w_uncut), _tokens_with_ends(w_cut)
    ta, tb = [t for t, _ in a], [t for t, _ in b]
    why = f"uncut: {' '.join(ta)} | cut: {' '.join(tb)}"
    if ta == tb:
        return True, why
    ia = sum(1 for _, e in a if e <= cut_t_uncut + 0.05)   # tokens before the cut
    ib = sum(1 for _, e in b if e <= cut_t_cut + 0.05)
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=ta, b=tb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # words adjacent to the cut are indices [i - reach, i + reach); a deletion/insertion
        # (empty range) counts when it sits at the cut
        def touches(lo: int, hi: int, i: int) -> bool:
            return (i - reach <= lo <= i + reach - 1 + 1) if lo == hi else (lo < i + reach and hi > i - reach)
        if touches(a0, a1, ia) or touches(b0, b1, ib):
            return False, why
    return True, why + " (differences away from the cut: recognition noise)"
