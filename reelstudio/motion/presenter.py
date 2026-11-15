"""Presenter anchor card for narrated reels: the creator's real photo (cut out on-device) in a circle, a name
tag, and a glow ring that pulses with the narration's loudness so it reads as "he is the one talking".

No lip-sync (the mouth never moves). Drawn with numpy + ffmpeg (no browser): a 65 s 4K HyperFrames render of a
static card would take ~45 min; this takes ~1-2 min. Output: ProRes 4444 alpha clip placed at (x, y).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..config import FONTS_DIR, SETTINGS

LAYER_W, LAYER_H = 1840, 600
CX, CY, DISC_R = 300, 300, 228       # avatar disc inside the layer
FPS = 60
ACCENT = np.array([255, 212, 0], np.float32)
PANEL = np.array([11, 15, 26], np.float32)


def _decode_rgba(path: Path, w: int, h: int, vf: str | None = None) -> np.ndarray:
    """First frame of an image/video as float RGBA (h, w, 4); `vf` must output w x h."""
    raw = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-i", str(path), "-vf", f"{vf or f'scale={w}:{h}'},format=rgba",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Straight-alpha 'over' (both HxWx4 float 0..1)."""
    sa, da = src[..., 3:4], dst[..., 3:4]
    oa = sa + da * (1 - sa)
    rgb = (src[..., :3] * sa + dst[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-6)
    return np.concatenate([rgb, oa], axis=-1)


def _text_layer(lines: list[tuple[str, int, str, int, int]], work: Path) -> np.ndarray:
    """Transparent LAYER_W x LAYER_H canvas with drawtext lines: (text, size, hex colour, x, y)."""
    filters = []
    for k, (text, size, colour, x, y) in enumerate(lines):
        tf = work / f"tag_{k}.txt"
        tf.write_text(text, encoding="utf-8")
        font = FONTS_DIR / ("Montserrat-ExtraBold.ttf" if size >= 60 else "Montserrat-Bold.ttf")
        filters.append(f"drawtext=fontfile={font}:textfile={tf}:fontsize={size}:fontcolor={colour}:x={x}:y={y}")
    raw = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                          f"color=c=black@0.0:s={LAYER_W}x{LAYER_H},format=rgba", "-vf", ",".join(filters),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(LAYER_H, LAYER_W, 4).astype(np.float32) / 255.0


def _static_layer(cutout: Path, title: str, subtitle: str, crop: tuple[int, int, int] | None, work: Path) -> np.ndarray:
    yy, xx = np.mgrid[0:LAYER_H, 0:LAYER_W].astype(np.float32)
    layer = np.zeros((LAYER_H, LAYER_W, 4), np.float32)
    # name panel (rounded rect + accent bar), drawn first so the disc overlaps its left edge
    px0, py0, px1, py1, rad = 470, 170, LAYER_W, 430, 46
    qx = np.clip(xx, px0 + rad, px1 - rad)
    qy = np.clip(yy, py0 + rad, py1 - rad)
    inside = (np.hypot(xx - qx, yy - qy) <= rad) & (xx >= px0) & (xx <= px1) & (yy >= py0) & (yy <= py1)
    panel = np.zeros_like(layer)
    panel[..., :3] = PANEL / 255
    panel[..., 3] = inside * 0.86
    layer = _over(layer, panel)
    # avatar disc: navy backdrop + cutout (head-and-shoulders square crop), circle mask with 2 px antialias
    side = 2 * DISC_R
    cx0, cy0, cs = crop or (0, 0, 0)
    vf = f"crop={cs}:{cs}:{cx0}:{cy0},scale={side}:{side}:flags=lanczos" if crop else f"scale={side}:{side}"
    face = _decode_rgba(cutout, side, side, vf=vf)
    disc = np.zeros((side, side, 4), np.float32)
    disc[..., :3] = np.array([29, 35, 64], np.float32) / 255
    disc[..., 3] = 1.0
    disc = _over(disc, face)
    dy, dx = np.mgrid[0:side, 0:side].astype(np.float32)
    disc[..., 3] *= np.clip(DISC_R - np.hypot(dx - DISC_R + 0.5, dy - DISC_R + 0.5) + 1.0, 0, 1)
    full = np.zeros_like(layer)
    full[CY - DISC_R:CY + DISC_R, CX - DISC_R:CX + DISC_R] = disc
    layer = _over(layer, full)
    # thin static accent ring hugging the disc
    d = np.hypot(xx - CX, yy - CY)
    ring = np.zeros_like(layer)
    ring[..., :3] = ACCENT / 255
    ring[..., 3] = np.clip(1 - np.abs(d - (DISC_R + 6)) / 4.0, 0, 1)
    layer = _over(layer, ring)
    texts = [(title, 66, "white", 580, 222)]
    if subtitle:
        texts.append((subtitle, 50, "0xFFD400", 580, 330))
    return _over(layer, _text_layer(texts, work))


def _envelope(wav: Path, n_frames: int) -> np.ndarray:
    """Per-frame loudness 0..1 (fast attack, ~0.15 s release) from the narration."""
    from ..media.audioio import SR, read_audio
    x = read_audio(wav, sr=SR)
    hop = SR // FPS
    frames = np.zeros(n_frames, np.float32)
    for i in range(n_frames):
        seg = x[i * hop:(i + 1) * hop]
        frames[i] = np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
    ref = np.percentile(frames[frames > 1e-4], 95) if np.any(frames > 1e-4) else 1.0
    lvl = np.clip(frames / max(ref, 1e-6), 0, 1)
    out, cur, rel = np.zeros_like(lvl), 0.0, np.exp(-1 / (0.15 * FPS))
    for i, v in enumerate(lvl):
        cur = v if v > cur else cur * rel + v * (1 - rel)
        out[i] = cur
    return out


def render_presenter(cutout: Path, narration_wav: Path, duration: float, out: Path, *, title: str,
                     subtitle: str = "", crop: tuple[int, int, int] | None = None) -> Path:
    """ProRes 4444 alpha clip (LAYER_W x LAYER_H, 60 fps, `duration` s) of the anchor card."""
    work = out.parent / "presenter_work"
    work.mkdir(parents=True, exist_ok=True)
    static = _static_layer(cutout, title, subtitle, crop, work)
    n = int(round(duration * FPS))
    env = _envelope(narration_wav, n)
    # ring region only (the rest of the frame is the static layer)
    R = DISC_R + 70
    y0, y1, x0, x1 = CY - R, CY + R, CX - R, CX + R
    ry, rx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    d = np.hypot(rx - CX, ry - CY)
    static_u8 = (static * 255).astype(np.uint8)
    region_static = static[y0:y1, x0:x1]
    proc = subprocess.Popen([SETTINGS.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                             "-s", f"{LAYER_W}x{LAYER_H}", "-r", str(FPS), "-i", "-", "-c:v", "prores_ks",
                             "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", str(out)],
                            stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / FPS
            e = float(env[i])
            r = DISC_R + 16 + 26 * e
            glow = np.exp(-((d - r) / (8 + 14 * e)) ** 2) * (0.25 + 0.75 * e)
            ring = np.zeros(region_static.shape, np.float32)
            ring[..., :3] = ACCENT / 255
            ring[..., 3] = glow
            frame = static_u8.copy()
            region = _over(ring, region_static)
            # enter (0.6 s fade + rise) / exit (0.4 s fade): applied to the whole card's alpha
            fade = min(1.0, t / 0.6) * min(1.0, max(0.0, (duration - t) / 0.4))
            frame[y0:y1, x0:x1] = (region * 255).astype(np.uint8)
            if fade < 1.0:
                frame = frame.copy()
                frame[..., 3] = (frame[..., 3].astype(np.float32) * fade).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("presenter encode failed")
    return out
