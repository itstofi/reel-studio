"""Media core on the synthetic takes: probe, conform (mirror, HLG->SDR, range, rotation), cut, final spec."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelstudio.edit.captions import write_ass
from reelstudio.media.probe import probe
from reelstudio.models import (CaptionCue, CaptionWord, HDRKind, Layout, Project, Span, Take, Timeline,
                               TimelineEntry)

MEDIA = Path(__file__).parent / "media"
pytestmark = pytest.mark.skipif(not (MEDIA / "t01_ar.mov").exists(), reason="run scripts/make_test_takes.py")


def stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,codec_tag_string,width,height,pix_fmt,r_frame_rate,"
                          "color_range,color_space,color_transfer,color_primaries,duration", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def frame(path, t, w=540, h=960):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def red(img):
    return float(np.mean((img[..., 0] > 170) & (img[..., 1] < 90) & (img[..., 2] < 90)))


def test_probe_detects_hdr_range_and_rotation():
    a, b, c = (probe(MEDIA / n) for n in ("t01_ar.mov", "t02_ar_hlg.mov", "t03_en.mov"))
    assert a.color_range == "pc" and a.hdr == HDRKind.sdr and (a.width, a.height) == (2160, 3840)
    assert b.hdr == HDRKind.hlg and b.bit_depth == 10
    assert abs(c.rotation) == 90 and (c.width, c.height) == (2160, 3840)


@pytest.mark.slow
def test_cut_and_final_encode_meet_delivery_spec(tmp_path):
    from reelstudio.media.render import concat_segments, final_encode, render_segments
    takes = [Take(id=f"t0{i}", filename=n, source_path=str(MEDIA / n), order=i, info=probe(MEDIA / n))
             for i, n in ((1, "t01_ar.mov"), (2, "t02_ar_hlg.mov"))]
    project = Project(id="p", name="p", takes=takes)
    tl = Timeline(fps=60, entries=[
        TimelineEntry(segment_id="s1", beat_id="B01", take_id="t01", src_in_f=180, src_out_f=300, out_in_f=0, out_out_f=120),
        TimelineEntry(segment_id="s2", beat_id="B02", take_id="t02", src_in_f=60, src_out_f=210, out_in_f=120, out_out_f=270),
    ])
    segs = render_segments(project, tl, tmp_path / "segments")
    cut = concat_segments(segs, tmp_path / "cut.mov")
    # mirror rule: red marker drawn on the TRUE left is back on the left
    img = frame(cut, 0.5)
    assert red(img[380:425, 20:65]) > 0.9 and red(img[380:425, 475:520]) < 0.1  # marker box at x60 y1500 (4K)
    # HLG part is not flat grey: decent contrast
    hlg = frame(cut, 3.5)
    assert hlg.std() > 25
    # an alpha overlay clip and an ASS file
    ov = tmp_path / "ov.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue@0.8:s=2160x3840:r=60:d=1,format=yuva444p10le",
                    "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", str(ov)], check=True)
    layout = Layout(canvas_w=2160, canvas_h=3840, head=None, caption_band=Span(start=2700, end=3000),
                    graphics_bands=[], safe_x0=130, safe_x1=1900)
    cues = [CaptionCue(id="c1", beat_id="B01", start=0.2, end=1.5,
                       words=[CaptionWord(text="Hi", start=0.2, end=0.6), CaptionWord(text="friends", start=0.6, end=1.5)])]
    ass = write_ass(cues, layout, tmp_path / "c.ass")
    final = final_encode(cut, tmp_path / "out" / "final.mov", captions_ass=ass, overlays=[(ov, 2.0)])
    s = stream(final)
    v = s["video"]
    assert (v["codec_name"], v["codec_tag_string"], v["width"], v["height"], v["r_frame_rate"], v["pix_fmt"]) == \
        ("hevc", "hvc1", 2160, 3840, "60/1", "yuv420p")
    assert v["color_range"] == "tv" and {v["color_space"], v["color_transfer"], v["color_primaries"]} == {"bt709"}
    assert s["audio"]["codec_name"] == "aac"
    assert abs(float(v["duration"]) - 4.5) <= 1 / 60 + 1e-6 and abs(float(s["audio"]["duration"]) - 4.5) <= 0.03
    blue = frame(final, 2.5)
    assert blue[..., 2].mean() > blue[..., 0].mean() + 40      # overlay visible at 2.5 s
    plain = frame(final, 1.0)
    assert not (plain[..., 2].mean() > plain[..., 0].mean() + 40)  # not before its start
