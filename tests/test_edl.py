"""Timeline: frame-exact, never replays audio (the 'what went what went' defect)."""
import pytest

from reelstudio.edit.edl import (TimelineError, beat_spans, build_timeline, src_to_out,
                                 validate_timeline)
from reelstudio.models import Beat, Plan, Segment


def plan(*beats):
    return Plan(beats=[Beat(id=f"B{i+1:02d}", segments=segs) for i, segs in enumerate(beats)])


def seg(sid, take, a, b):
    return Segment(id=sid, take_id=take, src_in=a, src_out=b)


def test_frames_snap_outward_and_accumulate():
    tl = build_timeline(plan([seg("s1", "t01", 1.004, 2.0)], [seg("s2", "t01", 5.0, 6.01)]), 60)
    e1, e2 = tl.entries
    assert (e1.src_in_f, e1.src_out_f) == (60, 120)      # 1.004 floors to frame 60
    assert (e2.src_in_f, e2.src_out_f) == (300, 361)     # 6.01 ceils to frame 361
    assert (e2.out_in_f, e2.out_out_f) == (60, 121)
    assert tl.duration == pytest.approx(121 / 60)


def test_consecutive_overlap_is_joined_not_replayed():
    # seg 12 ended at 335.22, seg 13 began at 334.47 (the shipped defect)
    tl = build_timeline(plan([seg("s12", "t01", 330.0, 335.22)], [seg("s13", "t01", 334.47, 340.0)]), 60)
    a, b = tl.entries
    assert b.src_in_f == a.src_out_f            # continues exactly where s12 ended
    assert validate_timeline(tl) == []


def test_contained_segment_is_dropped():
    tl = build_timeline(plan([seg("s1", "t01", 10, 20)], [seg("s2", "t01", 12, 18)]), 60)
    assert [e.segment_id for e in tl.entries] == ["s1"]


def test_non_adjacent_overlap_raises():
    with pytest.raises(TimelineError, match="overlaps"):
        build_timeline(plan([seg("s1", "t01", 10, 20)], [seg("s2", "t02", 0, 5)],
                            [seg("s3", "t01", 19, 25)]), 60)


def test_src_to_out_and_beat_spans():
    tl = build_timeline(plan([seg("s1", "t01", 10, 12)], [seg("s2", "t02", 3, 4)]), 60)
    assert src_to_out(tl, "t01", 11.0) == pytest.approx(1.0)
    assert src_to_out(tl, "t02", 3.5) == pytest.approx(2.5)
    assert src_to_out(tl, "t01", 13.0) is None
    assert beat_spans(tl) == {"B01": (0.0, 2.0), "B02": (2.0, 3.0)}


def test_disabled_beats_are_skipped():
    p = plan([seg("s1", "t01", 0, 1)], [seg("s2", "t01", 2, 3)])
    p.beats[0].enabled = False
    assert [e.segment_id for e in build_timeline(p, 60).entries] == ["s2"]
