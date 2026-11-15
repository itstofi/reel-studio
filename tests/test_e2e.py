"""End-to-end: synthetic takes -> plan -> approve -> 4K60 render -> adversarial QA.

Slow (whisper large-v3 + 4K encodes). Run: .venv/bin/python -m pytest -q -m slow tests/test_e2e.py
Set REEL_E2E_NO_LLM=1 to skip Claude calls (graphics suggestions).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "tests" / "media"


def ensure_media() -> dict:
    if not (MEDIA / "expected.json").exists():
        subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/make_test_takes.py"), str(MEDIA)],
                       check=True)
    return json.loads((MEDIA / "expected.json").read_text())


def frame_rgb(video: Path, t: float, w: int = 540, h: int = 960) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)


def red_fraction(img: np.ndarray) -> float:
    r, g, b = (img[..., i].astype(int) for i in range(3))
    return float(np.mean((r > 170) & (g < 90) & (b < 90)))


def probe_stream(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,codec_tag_string,width,height,pix_fmt,r_frame_rate,color_range,"
                          "color_space,color_transfer,color_primaries", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)["streams"][0]


def build(tmp_path: Path, lang: str, takes: list[str]):
    from reelstudio import pipeline
    from reelstudio.store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    p = pipeline.create_project(store, f"E2E {lang}", lang=lang)
    pipeline.import_takes(store, p.id, [str(MEDIA / t) for t in takes])
    pipeline.set_script(store, p.id, ar_text=(MEDIA / "script_ar.txt").read_text(encoding="utf-8"),
                        en_text=(MEDIA / "script_en.txt").read_text(encoding="utf-8"))
    pipeline.ingest(store, p.id)
    pipeline.analyze(store, p.id)
    plan = pipeline.plan(store, p.id)
    return pipeline, store, p.id, plan


def assert_delivery(final: Path) -> None:
    s = probe_stream(final)
    assert (s["codec_name"], s["codec_tag_string"], s["width"], s["height"]) == ("hevc", "hvc1", 2160, 3840)
    assert s["r_frame_rate"] == "60/1" and s["pix_fmt"] == "yuv420p"
    assert s.get("color_range") == "tv"
    assert {s.get("color_space"), s.get("color_transfer"), s.get("color_primaries")} == {"bt709"}


@pytest.mark.slow
def test_arabic_reel_end_to_end(tmp_path):
    expected = ensure_media()
    pipeline, store, pid, plan = build(tmp_path, "ar", ["t01_ar.mov", "t02_ar_hlg.mov"])
    project = store.load(pid)
    by_file = {Path(t.filename).name: t.id for t in project.takes}
    want = {line: by_file[{"t01": "t01_ar.mov", "t02": "t02_ar_hlg.mov"}[take]]
            for line, take in expected["ar"].items()}

    # plan: one beat per line, in script order, LAST clean repeat wins (L03 comes from the HLG take)
    assert [b.line_ids for b in plan.beats] == [["L01"], ["L02"], ["L03"], ["L04"]]
    for beat in plan.beats:
        assert beat.segments, f"{beat.id} has no segments: {plan.warnings}"
        assert beat.segments[0].take_id == want[beat.line_ids[0]], beat.note
    # L01's partial retry lost to the full attempt that followed it
    gap_a, gap_b = expected["t01_mid_phrase_gap"]
    assert plan.beats[0].segments[0].src_in > 2.0
    # the 1.2 s mid-phrase dropout in L02 was found and closed after an A/B check
    l2_gaps = [g for g in plan.beats[1].gaps if abs(g.start - gap_a) < 0.3]
    assert l2_gaps and l2_gaps[0].action == "close" and l2_gaps[0].verified is True, plan.beats[1].gaps
    # English captions on the Arabic cut, name spelled right
    assert plan.beats[0].caption_en.startswith("Hi friends") and "Jweed" in plan.beats[0].caption_en

    with pytest.raises(Exception):
        pipeline.render(store, pid)             # unapproved plans never render
    pipeline.approve(store, pid)
    final = pipeline.render(store, pid)
    assert_delivery(final)

    # mirror rule: the marker drawn on the TRUE left is back on the left in the cut
    paths = store.paths(pid)
    img = frame_rgb(paths.cut, 1.0)
    left, right = red_fraction(img[380:425, 20:65]), red_fraction(img[380:425, 475:520])
    assert left > 0.9 and right < 0.1, (left, right)   # marker drawn at x60 y1500 on the true left

    captions = json.loads(paths.captions_json.read_text(encoding="utf-8"))
    text = " ".join(w["text"] for c in captions for w in c["words"])
    assert text and not any("؀" <= ch <= "ۿ" for ch in text)

    report = pipeline.qa(store, pid)
    failed = [(c.name, c.summary) for c in report.checks if c.status == "fail"]
    assert not failed, failed


@pytest.mark.slow
def test_english_reel_end_to_end_with_rotation_and_stutter(tmp_path):
    ensure_media()
    pipeline, store, pid, plan = build(tmp_path, "en", ["t03_en.mov"])
    project = store.load(pid)
    info = project.takes[0].info
    assert (info.width, info.height) == (2160, 3840), "display rotation must be applied"
    assert [b.line_ids for b in plan.beats] == [["L01"], ["L02"], ["L03"], ["L04"]]
    assert all(b.segments for b in plan.beats), plan.warnings
    pipeline.approve(store, pid)
    final = pipeline.render(store, pid)
    assert_delivery(final)
    report = pipeline.qa(store, pid)
    stutter = next(c for c in report.checks if c.name == "stutter_scan")
    assert stutter.status != "fail", stutter.summary   # "Breaking news, breaking news" restart was cut
    failed = [(c.name, c.summary) for c in report.checks if c.status == "fail"]
    assert not failed, failed
