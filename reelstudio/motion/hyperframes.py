"""HyperFrames motion-graphic inserts: one small project per graphic, each rendered to a
ProRes 4444 alpha clip and composited by ffmpeg in media.render.final_encode.

Why per-graphic clips: alpha output forces PNG capture (8.3 MP/frame at 4K), so a
full-length transparent overlay would be very slow; short clips are cheap. The footage
never goes through Chrome, so mirroring, HDR conversion and delivery stay in ffmpeg.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from ..config import FONTS_DIR, RENDER_DIR, SETTINGS
from ..edit.edl import beat_spans
from ..models import Box, Layout, Plan, Span, Timeline
from ..util.proc import ProgressFn, run
from .templates import TEMPLATES, render_template

HF_BIN = RENDER_DIR / "node_modules" / ".bin" / "hyperframes"
MARGIN = 80


class InsertSpec(BaseModel):
    graphic_id: str
    beat_id: str
    template: str
    comp_path: str          # project dir holding index.html
    start: float            # output seconds
    duration: float
    box: Box                # rect the card may paint (inside a graphics band)


def available() -> tuple[bool, str]:
    if not HF_BIN.exists():
        return False, f"HyperFrames not installed ({HF_BIN}); run: cd render && npm ci"
    return True, "hyperframes 0.9.41"


def _env() -> dict:
    env = dict(os.environ)
    env.update(HYPERFRAMES_NO_TELEMETRY="1", HYPERFRAMES_NO_UPDATE_CHECK="1", HYPERFRAMES_NO_AUTO_INSTALL="1",
               DO_NOT_TRACK="1", HYPERFRAMES_FFMPEG_PATH=SETTINGS.ffmpeg, HYPERFRAMES_FFPROBE_PATH=SETTINGS.ffprobe)
    return env


def _pick_band(layout: Layout, band: str) -> Span | None:
    bands = sorted(layout.graphics_bands, key=lambda b: b.start)
    if not bands:
        return None
    if band == "upper":
        return bands[0]
    if band == "lower":
        return bands[-1]
    return max(bands, key=lambda b: b.end - b.start)


def build_inserts(plan: Plan, timeline: Timeline, layout: Layout, out_dir: Path, *,
                  canvas: tuple[int, int] = (2160, 3840), fps: int = 60) -> tuple[list[InsertSpec], list[str]]:
    """Write one HyperFrames project per graphic. Returns (inserts, warnings)."""
    spans = beat_spans(timeline)
    out_dir.mkdir(parents=True, exist_ok=True)
    inserts: list[InsertSpec] = []
    warnings: list[str] = []
    shadow = SETTINGS.layout.shadow_budget_px // 2
    for beat in plan.beats:
        if not beat.enabled or beat.id not in spans:
            continue
        b0, b1 = spans[beat.id]
        for g in beat.graphics:
            if g.template not in TEMPLATES:
                warnings.append(f"{g.id}: unknown template {g.template}")
                continue
            band = _pick_band(layout, g.band)
            if band is None:
                warnings.append(f"{g.id}: no free band outside his head — graphic skipped")
                continue
            start = round(min(max(b0, b0 + g.beat_offset), max(b0, b1 - 1.0)) * fps) / fps
            dur = round(min(g.duration, b1 - start) * fps) / fps
            if dur < 1.0:
                warnings.append(f"{g.id}: beat too short for a graphic ({dur:.2f}s) — skipped")
                continue
            if g.template == "screenshot" and not (g.params.get("src") and Path(str(g.params["src"])).exists()):
                warnings.append(f"{g.id}: screenshot needs a real image (params.src) of "
                                f"{g.params.get('source', 'the page')} — fetch it, never invent it; skipped")
                continue
            box = Box(x0=layout.safe_x0, y0=int(band.start) + MARGIN, x1=layout.safe_x1,
                      y1=int(band.end) - MARGIN - shadow)
            proj = out_dir / g.id
            if proj.exists():
                shutil.rmtree(proj)
            (proj / "assets").mkdir(parents=True)
            shutil.copyfile(FONTS_DIR / "NotoKufiArabic-Bold.ttf", proj / "assets" / "NotoKufiArabic-Bold.ttf")
            params = dict(g.params)
            for key in ("src",):  # local images are copied into the project (no fetching at render)
                src = params.get(key)
                if src and Path(src).exists():
                    dst = proj / "assets" / Path(src).name
                    shutil.copyfile(src, dst)
                    params[key] = f"assets/{dst.name}"
            comp_id = re.sub(r"[^a-zA-Z0-9_-]", "-", g.id)
            html = render_template(g.template, comp_id, dur,
                                   {"x": box.x0, "y": box.y0, "w": box.x1 - box.x0, "h": box.y1 - box.y0}, params)
            (proj / "index.html").write_text(html, encoding="utf-8")
            inserts.append(InsertSpec(graphic_id=g.id, beat_id=beat.id, template=g.template, comp_path=str(proj),
                                      start=start, duration=dur, box=box))
    return inserts, warnings


def render_inserts(inserts: list[InsertSpec], out_dir: Path, *, fps: int = 60, preview: bool = False,
                   progress: ProgressFn | None = None) -> list[tuple[Path, float]]:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: list[tuple[Path, float]] = []
    for i, ins in enumerate(inserts):
        out = out_dir / f"{Path(ins.comp_path).name}.mov"
        args = [str(HF_BIN), "render", ins.comp_path, "--format", "mov", "--prores-profile", "4444",
                "--fps", str(30 if preview else fps), "--strict", "--quiet", "--output", str(out)]
        # cache: same composition (HTML + assets) + same render args -> reuse the clip. Start time is not in
        # the HTML, so re-timing a reel only re-renders graphics whose duration or content changed.
        import hashlib
        h = hashlib.sha1(" ".join(args[1:-2]).encode())
        for f in sorted(Path(ins.comp_path).rglob("*")):
            if f.is_file():
                h.update(f.name.encode()); h.update(f.read_bytes())
        key, stamp = h.hexdigest(), out.with_suffix(".key")
        if not (out.exists() and stamp.exists() and stamp.read_text() == key):
            out.unlink(missing_ok=True)
            run(args, env=_env(), cwd=RENDER_DIR, timeout=1800)
            if not out.exists():
                raise RuntimeError(f"HyperFrames produced no file for {ins.graphic_id}")
            stamp.write_text(key)
        clips.append((out, ins.start))
        if progress:
            progress((i + 1) / max(1, len(inserts)), f"graphic {i + 1}/{len(inserts)}")
    return clips


_BBOX = re.compile(r"x1:(\d+)\s+x2:(\d+)\s+y1:(\d+)\s+y2:(\d+)")


def alpha_bbox(clip: Path, fps_sample: int = 6) -> Box | None:
    """Union bounding box of visible alpha (shadows included) over sampled frames."""
    thr = SETTINGS.qa.face_alpha_threshold
    proc = run([SETTINGS.ffmpeg, "-hide_banner", "-nostdin", "-i", str(clip), "-vf",
                f"fps={fps_sample},alphaextract,format=gray,bbox=min_val={thr}", "-f", "null", "-"])
    boxes = [tuple(map(int, m.groups())) for m in _BBOX.finditer(proc.stderr.decode("utf-8", "replace"))]
    boxes = [b for b in boxes if b[1] >= b[0] and b[3] >= b[2]]
    if not boxes:
        return None
    return Box(x0=min(b[0] for b in boxes), x1=max(b[1] for b in boxes),
               y0=min(b[2] for b in boxes), y1=max(b[3] for b in boxes))


def overlay_alpha_in_box(clip: Path, box: Box, fps_sample: int = 6) -> float:
    """Max fraction of `box` pixels with visible alpha in any sampled frame."""
    import numpy as np
    w, h = max(1, box.x1 - box.x0), max(1, box.y1 - box.y0)
    raw = run([SETTINGS.ffmpeg, "-v", "error", "-nostdin", "-i", str(clip), "-vf",
               f"fps={fps_sample},alphaextract,crop={w}:{h}:{box.x0}:{box.y0},format=gray",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]).stdout
    frames = np.frombuffer(raw, np.uint8)
    if frames.size == 0:
        return 0.0
    frames = frames[: (frames.size // (w * h)) * w * h].reshape(-1, h, w)
    return float((frames > SETTINGS.qa.face_alpha_threshold).mean(axis=(1, 2)).max())
