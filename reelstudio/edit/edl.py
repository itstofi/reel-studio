"""Frame-exact timeline (EDL): plan segments -> source/output frame mapping.

HARD RULE (shipped defect): segments must never overlap. Seg 12 ended at 335.22 and
seg 13 began at 334.47 — the overlapped audio played twice ("what went what went
wrong"). Consecutive segments from the same take that touch or overlap are joined
seamlessly (the later one starts where the earlier ends); any other source overlap
within a take is an error, because it would replay audio.
"""
from __future__ import annotations

from ..models import Plan, Timeline, TimelineEntry
from ..util.timecode import ceil_frames, floor_frames


class TimelineError(ValueError):
    pass


def build_timeline(plan: Plan, fps: int) -> Timeline:
    entries: list[TimelineEntry] = []
    out_f = 0
    for beat in plan.beats:
        if not beat.enabled:
            continue
        for seg in beat.segments:
            a = floor_frames(seg.src_in, fps)   # outward snap: never cut into a pad
            b = ceil_frames(seg.src_out, fps)
            prev = entries[-1] if entries else None
            if prev and prev.take_id == seg.take_id and prev.src_in_f <= a < prev.src_out_f:
                # Overlaps the previous segment of the same take: continue from its end.
                a = prev.src_out_f
            if b <= a:
                continue  # empty, or fully contained in the previous segment
            entries.append(TimelineEntry(segment_id=seg.id, beat_id=beat.id, take_id=seg.take_id,
                                         src_in_f=a, src_out_f=b, out_in_f=out_f,
                                         out_out_f=out_f + (b - a)))
            out_f += b - a
    tl = Timeline(fps=fps, entries=entries)
    problems = validate_timeline(tl)
    if problems:
        raise TimelineError("; ".join(problems))
    return tl


def validate_timeline(tl: Timeline) -> list[str]:
    """All problems found (empty list = valid)."""
    problems: list[str] = []
    expected = 0
    for e in tl.entries:
        if e.src_out_f <= e.src_in_f:
            problems.append(f"{e.segment_id}: empty source range")
        if e.out_in_f != expected:
            problems.append(f"{e.segment_id}: output starts at frame {e.out_in_f}, expected {expected}")
        if e.out_out_f - e.out_in_f != e.src_out_f - e.src_in_f:
            problems.append(f"{e.segment_id}: output length != source length")
        expected = e.out_out_f
    by_take: dict[str, list[TimelineEntry]] = {}
    for e in tl.entries:
        by_take.setdefault(e.take_id, []).append(e)
    for take_id, items in by_take.items():
        items = sorted(items, key=lambda e: e.src_in_f)
        for x, y in zip(items, items[1:]):
            if y.src_in_f < x.src_out_f:
                overlap = (x.src_out_f - y.src_in_f) / tl.fps
                problems.append(
                    f"{take_id}: {x.segment_id} [{x.src_in_f / tl.fps:.3f}-{x.src_out_f / tl.fps:.3f}] overlaps "
                    f"{y.segment_id} [{y.src_in_f / tl.fps:.3f}-{y.src_out_f / tl.fps:.3f}] by {overlap:.3f}s "
                    "(audio would play twice)")
    return problems


def src_to_out(tl: Timeline, take_id: str, t: float) -> float | None:
    """Map a source time in a take to output time, or None if that moment was cut."""
    for e in tl.entries:
        if e.take_id != take_id:
            continue
        a, b = e.src_in_f / tl.fps, e.src_out_f / tl.fps
        if a <= t < b:
            return e.out_in_f / tl.fps + (t - a)
    return None


def entry_for_out(tl: Timeline, t: float) -> TimelineEntry | None:
    f = t * tl.fps
    for e in tl.entries:
        if e.out_in_f <= f < e.out_out_f:
            return e
    return None


def beat_spans(tl: Timeline) -> dict[str, tuple[float, float]]:
    """Output (start, end) seconds of every beat present in the timeline."""
    spans: dict[str, tuple[float, float]] = {}
    for e in tl.entries:
        a, b = e.out_in_f / tl.fps, e.out_out_f / tl.fps
        if e.beat_id in spans:
            spans[e.beat_id] = (min(spans[e.beat_id][0], a), max(spans[e.beat_id][1], b))
        else:
            spans[e.beat_id] = (a, b)
    return spans
