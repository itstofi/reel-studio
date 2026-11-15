"""HyperFrames inserts: placed inside a graphics band, off his head; real alpha render."""
from pathlib import Path

import pytest

from reelstudio.edit.edl import build_timeline
from reelstudio.models import Beat, Box, GraphicSpec, Layout, Plan, Segment, Span
from reelstudio.motion.hyperframes import alpha_bbox, available, build_inserts, render_inserts

LAYOUT = Layout(canvas_w=2160, canvas_h=3840, head=Box(x0=400, y0=200, x1=1800, y1=1800),
                caption_band=Span(start=2700, end=3000), graphics_bands=[Span(start=1800, end=2660)],
                safe_x0=130, safe_x1=1900)


def plan(template="title", params=None, seg=(0, 4), dur=3.0):
    return Plan(beats=[Beat(id="B01", segments=[Segment(id="s", take_id="t01", src_in=seg[0], src_out=seg[1])],
                            graphics=[GraphicSpec(id="B01g1", template=template, params=params or {"text": "Hi"},
                                                  duration=dur)])])


def test_build_places_card_inside_band(tmp_path):
    p = plan()
    ins, warns = build_inserts(p, build_timeline(p, 60), LAYOUT, tmp_path)
    assert not warns and len(ins) == 1
    box = ins[0].box
    assert box.y0 >= 1800 and box.y1 <= 2660
    html = (Path(ins[0].comp_path) / "index.html").read_text()
    assert 'data-composition-id="B01g1"' in html and "D - 0.35" in html and "background: transparent" in html


def test_short_beat_and_no_band_are_skipped(tmp_path):
    p = plan(seg=(0, 0.5))
    assert build_inserts(p, build_timeline(p, 60), LAYOUT, tmp_path)[0] == []
    no_band = LAYOUT.model_copy(update={"graphics_bands": []})
    p = plan()
    ins, warns = build_inserts(p, build_timeline(p, 60), no_band, tmp_path)
    assert ins == [] and "no free band" in warns[0]


@pytest.mark.slow
@pytest.mark.skipif(not available()[0], reason="HyperFrames not installed")
def test_render_alpha_clip_stays_off_the_head(tmp_path):
    p = plan("stat", {"value": "72%", "label": "faster"})
    ins, _ = build_inserts(p, build_timeline(p, 60), LAYOUT, tmp_path / "ins")
    (clip, start), = render_inserts(ins, tmp_path / "clips")
    bb = alpha_bbox(clip)
    assert bb is not None and bb.y0 > LAYOUT.head.y1 and bb.y1 <= 2660
