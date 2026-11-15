"""Reel Studio voice sidecar: Chatterbox Multilingual V3 (Arabic + English, zero-shot clone)
on Apple MPS behind a 127.0.0.1 FastAPI. Separate venv so torch never enters the main app.

POST /tts {text, lang, ref_wav, out_wav, seed} -> {out_wav, duration}
GET /health, GET /voices, POST /unload
"""
from __future__ import annotations

import gc
import os
import re
import threading
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.3")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]            # project root
HF_HOME = Path(os.environ.get("HF_HOME", ROOT / "models" / "hf"))
os.environ["HF_HOME"] = str(HF_HOME)
REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
FILES = ["ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt",
         "Cangjie5_TC.json"]

app = FastAPI(title="reel-voice")
_lock = threading.Lock()
_model = None


class TTSRequest(BaseModel):
    text: str
    lang: str = "ar"
    ref_wav: str | None = None
    out_wav: str
    seed: int = 7
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


def _load():
    global _model
    if _model is None:
        from huggingface_hub import snapshot_download
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        ckpt = snapshot_download("ResembleAI/chatterbox", revision=REVISION, allow_patterns=FILES)
        _model = ChatterboxMultilingualTTS.from_local(ckpt, "mps", t3_model="v3")
    return _model


def _sentences(text: str, lang: str) -> list[str]:
    """Split ourselves: Chatterbox misses Arabic '؟' / '،' boundaries and caps ~40 s per call."""
    parts = re.split(r"(?<=[.!?؟。])\s+", text.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # keep a final '؟' instead of letting punc_norm append '.'
        out.append(p if re.search(r"[.!?؟]$", p) else p + ("." if lang != "ar" else "."))
    return out or [text]


@app.get("/health")
def health():
    return {"ok": True, "loaded": _model is not None, "engine": "chatterbox-multilingual-v3", "device": "mps"}


@app.get("/voices")
def voices():
    return {"languages": ["ar", "en"], "clone": "zero-shot from ref_wav"}


@app.post("/tts")
def tts(req: TTSRequest):
    import soundfile as sf
    import torch
    if req.lang not in ("ar", "en"):
        raise HTTPException(400, "lang must be ar or en")
    if req.ref_wav and not Path(req.ref_wav).exists():
        raise HTTPException(400, f"reference not found: {req.ref_wav}")
    with _lock:  # generate() mutates model state: one job at a time
        m = _load()
        torch.manual_seed(req.seed)
        chunks = []
        for i, sent in enumerate(_sentences(req.text, req.lang)):
            wav = m.generate(sent, language_id=req.lang, audio_prompt_path=req.ref_wav if i == 0 else None,
                             exaggeration=req.exaggeration, cfg_weight=req.cfg_weight)
            chunks.append(wav.squeeze(0).detach().cpu().numpy())
            chunks.append(np.zeros(int(m.sr * 0.18), dtype=np.float32))
        audio = np.concatenate(chunks[:-1]) if chunks else np.zeros(1, np.float32)
        out = Path(req.out_wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), audio, m.sr)
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        return {"out_wav": str(out), "duration": len(audio) / m.sr, "sr": m.sr}


@app.post("/unload")
def unload():
    global _model
    with _lock:
        _model = None
        gc.collect()
        try:
            import torch
            torch.mps.empty_cache()
        except Exception:
            pass
    return {"ok": True}


def main():
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("REEL_VOICE_PORT", "8004")), log_level="info")


if __name__ == "__main__":
    main()
