"""Face box on the mirrored proxy; layout keeps captions/graphics off the head."""
import subprocess

import pytest

from reelstudio.config import LayoutSpec
from reelstudio.media.face import compute_layout, head_box, measure_face
from reelstudio.models import Box, FaceInfo, Take

DATA = __import__("pathlib").Path(__file__).parent / "data"


def test_head_box_adds_clearance_and_shadow():
    head = head_box(Box(x0=800, y0=600, x1=1300, y1=1250), (2160, 3840), LayoutSpec())
    assert head.y0 < 600 - 0.45 * 650 and head.y1 > 1250 + 0.25 * 650
    assert head.x0 < 800 and head.x1 > 1300


def test_layout_bands_never_touch_the_head():
    face = FaceInfo(take_id="t01", canvas_w=2160, canvas_h=3840, samples=5, detections=5,
                    head=Box(x0=400, y0=300, x1=1800, y1=1900))
    layout, warnings = compute_layout([face])
    assert not warnings
    assert layout.caption_band.start >= 1900 and layout.caption_band.end <= 3840 * 0.8
    for band in layout.graphics_bands:
        assert band.start >= 1900 or band.end <= 300
    assert layout.graphics_bands and layout.graphics_bands[0].end <= layout.caption_band.start


def test_no_face_is_conservative():
    layout, warnings = compute_layout([FaceInfo(take_id="t01", canvas_w=2160, canvas_h=3840,
                                                samples=3, detections=0)])
    assert warnings and layout.head.y1 == int(0.6 * 3840)


@pytest.mark.slow
def test_measure_face_on_a_real_portrait(tmp_path):
    video = tmp_path / "proxy.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(DATA / "portrait_pd.jpg"),
                    "-t", "4", "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,hflip",
                    "-r", "30", "-pix_fmt", "yuv420p", str(video)], check=True)
    info = measure_face(Take(id="t01", filename="x", source_path=str(video), order=1), video)
    assert info.detections >= 1 and info.face is not None and info.head is not None
    assert info.head.y0 <= info.face.y0 and info.head.y1 >= info.face.y1
