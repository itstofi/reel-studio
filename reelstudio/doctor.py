"""`reel doctor`: is every tool, model and integration ready? (never prints secrets)"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from .config import FONTS_DIR, RENDER_DIR, SETTINGS, VENDOR_DIR, YUNET_MODEL

YUNET_SHA = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def _c(name, ok, detail, fix=""):
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix}


def _out(cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def run_checks() -> list[dict]:
    checks = []
    ff = shutil.which(SETTINGS.ffmpeg) or SETTINGS.ffmpeg
    ver = _out([ff, "-hide_banner", "-version"]).splitlines()[:1]
    checks.append(_c("ffmpeg", bool(ver), ver[0] if ver else "missing", "brew install ffmpeg"))
    enc = _out([ff, "-hide_banner", "-encoders"])
    flt = _out([ff, "-hide_banner", "-filters"])
    for need in ("hevc_videotoolbox", "prores_videotoolbox", "h264_videotoolbox"):
        checks.append(_c(f"encoder {need}", need in enc, "ok" if need in enc else "missing",
                         "use an ffmpeg build with VideoToolbox"))
    for need in ("zscale", "tonemap", "ass", "loudnorm", "ebur128"):
        checks.append(_c(f"filter {need}", f" {need} " in flt, "ok" if f" {need} " in flt else "missing",
                         "use an ffmpeg build with libzimg + libass"))
    sha = hashlib.sha256(YUNET_MODEL.read_bytes()).hexdigest() if YUNET_MODEL.exists() else ""
    checks.append(_c("YuNet face model", sha == YUNET_SHA, str(YUNET_MODEL) if sha else "missing",
                     "scripts/setup.sh"))
    fonts = sorted(p.name for p in FONTS_DIR.glob("*.ttf"))
    checks.append(_c("fonts", "Montserrat-ExtraBold.ttf" in fonts, ", ".join(fonts) or "missing", "scripts/setup.sh"))
    hf = Path.home() / ".cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx"
    checks.append(_c("whisper large-v3 (mlx)", hf.exists(), "cached" if hf.exists() else "not downloaded",
                     "scripts/setup.sh (downloads ~3 GB)"))
    claude = SETTINGS.claude_bin
    checks.append(_c("Claude CLI", bool(claude), claude or "not found",
                     "install Claude Code or set CLAUDE_BIN; or set ANTHROPIC_API_KEY"))
    checks.append(_c("ANTHROPIC_API_KEY", True, "set" if SETTINGS.anthropic_key_set else "not set (CLI login is used)"))
    checks.append(_c("ELEVENLABS_API_KEY", bool(SETTINGS.elevenlabs_key), "set" if SETTINGS.elevenlabs_key
                     else "not set — ElevenLabs voice/clones disabled", "add it to .env"))
    from .voice.engines import ChatterboxTTS
    ok, why = ChatterboxTTS().available()
    checks.append(_c("Chatterbox voice sidecar", ok, why, "scripts/voice.sh start"))
    hfb = RENDER_DIR / "node_modules/.bin/hyperframes"
    checks.append(_c("HyperFrames", hfb.exists(), str(hfb) if hfb.exists() else "not installed",
                     "cd render && npm ci"))
    checks.append(_c("uvx (kinocut MCP)", bool(shutil.which("uvx")), shutil.which("uvx") or "missing",
                     "install uv"))
    vendored = [d.name for d in VENDOR_DIR.iterdir() if d.is_dir()] if VENDOR_DIR.exists() else []
    checks.append(_c("vendor repos", len(vendored) >= 11, f"{len(vendored)}/11", "scripts/setup.sh"))
    return checks
