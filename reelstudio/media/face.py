"""Face box per take (YuNet) and the layout that keeps every graphic off his head.

He rejects ANY graphic on or near his face or head ("always need to be somewhere not on
my face or head"), and cards touching the top of the head count as covering. So the
off-limits region is the face plus clearance for hair, chin and sides, plus a budget
for box-shadow alpha (a 0 28px 70px shadow paints ~60 px below a card at 1080p).
Faces are measured on the MIRRORED proxy, per take: he swaps sides between takes.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..config import SETTINGS, YUNET_MODEL, LayoutSpec
from ..models import Box, FaceInfo, Layout, Span, Take
from ..util.proc import run

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

SAMPLE_W, SAMPLE_H = 540, 960  # detection resolution (faces in a talking head are large)
MIN_SCORE = 0.7
MIN_BAND_PX = 280


def _detector(w: int, h: int):
    import cv2
    if not YUNET_MODEL.exists():
        raise FileNotFoundError(f"YuNet model missing: {YUNET_MODEL} (run scripts/setup.sh)")
    return cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (w, h), MIN_SCORE, 0.3, 5000)


def sample_frames(video: Path, every_s: float, w: int = SAMPLE_W, h: int = SAMPLE_H) -> list[np.ndarray]:
    """BGR frames every `every_s` seconds, scaled to w x h (aspect-preserving pad)."""
    vf = (f"fps=1/{every_s},scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    raw = run([SETTINGS.ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-vf", vf,
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]).stdout
    frame_bytes = w * h * 3
    n = len(raw) // frame_bytes
    return [np.frombuffer(raw[i * frame_bytes:(i + 1) * frame_bytes], dtype=np.uint8).reshape(h, w, 3)
            for i in range(n)]


def detect_faces(frames: list[np.ndarray]) -> list[tuple[float, float, float, float, float]]:
    """Largest confident face per frame: (x0, y0, x1, y1, score) in frame pixels."""
    if not frames:
        return []
    h, w = frames[0].shape[:2]
    det = _detector(w, h)
    found = []
    for frame in frames:
        _, faces = det.detect(frame)
        if faces is None or len(faces) == 0:
            continue
        best = max(faces, key=lambda f: float(f[2] * f[3]))
        if float(best[14]) < MIN_SCORE:
            continue
        x, y, bw, bh = (float(v) for v in best[:4])
        found.append((x, y, x + bw, y + bh, float(best[14])))
    return found


def head_box(face: Box, canvas: tuple[int, int], spec: LayoutSpec) -> Box:
    """Face + clearance (hair above, chin below, sides) + shadow budget, clamped."""
    fw, fh = face.x1 - face.x0, face.y1 - face.y0
    sh = spec.shadow_budget_px
    return Box(x0=max(0, int(face.x0 - spec.head_clearance_side * fw - sh)),
               y0=max(0, int(face.y0 - spec.head_clearance_top * fh - sh)),
               x1=min(canvas[0], int(face.x1 + spec.head_clearance_side * fw + sh)),
               y1=min(canvas[1], int(face.y1 + spec.head_clearance_bottom * fh + sh)))


def measure_face(take: Take, proxy: Path, spec: LayoutSpec | None = None,
                 canvas: tuple[int, int] | None = None) -> FaceInfo:
    spec = spec or SETTINGS.layout
    canvas = canvas or (SETTINGS.delivery.width, SETTINGS.delivery.height)
    frames = sample_frames(proxy, spec.face_sample_every_s)
    dets = detect_faces(frames)
    info = FaceInfo(take_id=take.id, canvas_w=canvas[0], canvas_h=canvas[1], samples=len(frames),
                    detections=len(dets))
    if not dets:
        return info
    arr = np.array([d[:4] for d in dets])
    sx, sy = canvas[0] / SAMPLE_W, canvas[1] / SAMPLE_H
    face = Box(x0=int(arr[:, 0].min() * sx), y0=int(arr[:, 1].min() * sy),
               x1=int(arr[:, 2].max() * sx), y1=int(arr[:, 3].max() * sy))
    info.face = face
    info.head = head_box(face, canvas, spec)
    return info


def compute_layout(faces: list[FaceInfo], spec: LayoutSpec | None = None,
                   canvas: tuple[int, int] | None = None) -> tuple[Layout, list[str]]:
    """Caption band + graphics bands that never touch the union head region."""
    spec = spec or SETTINGS.layout
    canvas = canvas or (SETTINGS.delivery.width, SETTINGS.delivery.height)
    W, H = canvas
    warnings: list[str] = []
    top_ui, bottom_ui = int(spec.ig_top * H), int(H * (1 - spec.ig_bottom))
    safe_x0, safe_x1 = int(0.06 * W), int(W * (1 - spec.ig_right))
    heads = [f.head for f in faces if f.head is not None]
    if heads:
        head = Box(x0=min(h.x0 for h in heads), y0=min(h.y0 for h in heads),
                   x1=max(h.x1 for h in heads), y1=max(h.y1 for h in heads))
    else:
        head = Box(x0=0, y0=0, x1=W, y1=int(0.6 * H))
        warnings.append("no face detected in any take: treating the top 60% of the frame as his head")
    free: list[Span] = []
    if head.y0 - top_ui >= MIN_BAND_PX:
        free.append(Span(start=top_ui, end=head.y0))
    if bottom_ui - head.y1 >= MIN_BAND_PX:
        free.append(Span(start=max(head.y1, top_ui), end=bottom_ui))
    cap_h = int(spec.caption_font_px * 2.6)
    margin = 40
    lower = max(free, key=lambda s: s.end) if free else None
    if lower is not None and lower.end - lower.start >= cap_h + margin:
        caption = Span(start=lower.end - cap_h - margin, end=lower.end - margin)
    else:
        caption = Span(start=bottom_ui - cap_h, end=bottom_ui)
        warnings.append("no free band tall enough for captions below the head; captions sit at the bottom "
                        "safe edge — check the face clearance")
    bands: list[Span] = []
    for band in free:
        end = min(band.end, caption.start - margin) if band.end > caption.start else band.end
        if end - band.start >= MIN_BAND_PX:
            bands.append(Span(start=band.start, end=end))
    if not bands:
        warnings.append("no free band for graphics outside the head region; graphics will be skipped")
    layout = Layout(canvas_w=W, canvas_h=H, head=head, caption_band=caption, graphics_bands=bands,
                    safe_x0=safe_x0, safe_x1=safe_x1)
    return layout, warnings
