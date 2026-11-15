"""Voice generation engines (Arabic + English), all producing 48 kHz WAV.

PRODUCT RULE: generated voice must NEVER be laid over his visible mouth (no lip-sync).
Use clips for graphics-only inserts, intro/outro cards, narrated (faceless) reels, or an
explicitly requested dubbed version.

- elevenlabs : cloud, best Arabic; Instant Voice Clones "Jweed AR" / "Jweed EN" made from
               his own takes (cloned per language: v4 replaces the accent across languages).
- chatterbox : local Chatterbox Multilingual V3 sidecar (services/voice, port 8004);
               zero-shot clone from a reference clip of his voice.
- say        : macOS `say` (Majed / Samantha) — offline fallback, not his voice.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Protocol

from ..config import SETTINGS
from ..util.proc import run

MODELS = ("eleven_v4", "eleven_multilingual_v2")   # v4 on /text-to-speech is UNVERIFIED -> fallback


class TTS(Protocol):
    name: str

    def synthesize(self, text: str, *, lang: str, voice: str, out: Path, project=None, store=None,
                   seed: int | None = None) -> Path: ...


def _to_wav48(src: Path, out: Path, *, raw_pcm_rate: int | None = None) -> Path:
    args = [SETTINGS.ffmpeg, "-v", "error", "-y"]
    if raw_pcm_rate:
        args += ["-f", "s16le", "-ar", str(raw_pcm_rate), "-ac", "1"]
    args += ["-i", str(src), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out)]
    run(args)
    return out


class SayTTS:
    name = "say"
    VOICES = {"ar": "Majed", "en": "Samantha"}

    def available(self) -> tuple[bool, str]:
        try:
            out = run(["say", "-v", "?"]).stdout.decode()
            return "Majed" in out, "macOS say (Majed ar / Samantha en) — not his voice"
        except Exception as exc:
            return False, str(exc)

    def synthesize(self, text, *, lang, voice="default", out: Path, project=None, store=None, seed=None) -> Path:
        aiff = out.with_suffix(".aiff")
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["say", "-v", self.VOICES.get(lang, "Samantha"), "-o", str(aiff), text])
        _to_wav48(aiff, out)
        aiff.unlink(missing_ok=True)
        return out


class ChatterboxTTS:
    name = "chatterbox"

    def __init__(self, url: str | None = None):
        self.url = (url or SETTINGS.chatterbox_url).rstrip("/")

    def available(self) -> tuple[bool, str]:
        import httpx
        try:
            r = httpx.get(f"{self.url}/health", timeout=2.0)
            return r.status_code == 200, f"sidecar at {self.url}"
        except Exception:
            return False, f"sidecar not running at {self.url} — scripts/voice.sh start"

    def synthesize(self, text, *, lang, voice="jweed", out: Path, project=None, store=None, seed=None) -> Path:
        import httpx
        ref = None
        if voice == "jweed" and project is not None and store is not None:
            from .reference import pick_reference
            ref = str(pick_reference(project, store, lang))
        raw = out.with_suffix(".24k.wav")
        r = httpx.post(f"{self.url}/tts", json={"text": text, "lang": lang, "ref_wav": ref,
                                               "out_wav": str(raw.resolve()), "seed": 7 if seed is None else seed},
                       timeout=600)
        if r.status_code != 200:
            raise RuntimeError(f"chatterbox: {r.status_code} {r.text[:300]}")
        _to_wav48(raw, out)
        raw.unlink(missing_ok=True)
        return out


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, client=None):
        self._client = client

    def available(self) -> tuple[bool, str]:
        return (bool(SETTINGS.elevenlabs_key), "ELEVENLABS_API_KEY set" if SETTINGS.elevenlabs_key
                else "add ELEVENLABS_API_KEY to .env")

    @property
    def client(self):
        if self._client is None:
            from elevenlabs.client import ElevenLabs
            if not SETTINGS.elevenlabs_key:
                raise RuntimeError("ELEVENLABS_API_KEY is not set")
            self._client = ElevenLabs(api_key=SETTINGS.elevenlabs_key)
        return self._client

    def ensure_clone(self, project, store, lang: str) -> str:
        """Instant Voice Clone per language from his cleanest takes; id cached in voice/clones.json."""
        from .reference import pick_reference
        cache = store.paths(project.id).voice_dir / "clones.json"
        clones = json.loads(cache.read_text()) if cache.exists() else {}
        if lang in clones:
            return clones[lang]
        ref = pick_reference(project, store, lang, seconds=90)
        mp3 = ref.with_suffix(".mp3")
        run([SETTINGS.ffmpeg, "-v", "error", "-y", "-i", str(ref), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)])
        with mp3.open("rb") as fh:
            v = self.client.voices.ivc.create(name=f"Jweed {lang.upper()}", files=[fh], remove_background_noise=False,
                                              description="Jweed — AI news narration", labels={"language": lang})
        clones[lang] = v.voice_id
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(clones, indent=2))
        return v.voice_id

    def synthesize(self, text, *, lang, voice="jweed", out: Path, project=None, store=None, seed=None) -> Path:
        seed = 1234 if seed is None else seed
        voice_id = self.ensure_clone(project, store, lang) if voice == "jweed" else voice
        out.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{text}|{voice_id}|{lang}|{seed}".encode()).hexdigest()[:12]
        cached = out.parent / f".el_{key}.wav"
        if cached.exists():
            out.write_bytes(cached.read_bytes())
            return out
        last_exc = None
        for model in MODELS:
            try:
                kw = dict(text=text, model_id=model, output_format="pcm_48000", seed=seed)
                if model != "eleven_multilingual_v2":
                    kw["language_code"] = lang
                r = self.client.text_to_speech.convert_with_timestamps(voice_id, **kw)
                pcm = out.with_suffix(".pcm")
                pcm.write_bytes(base64.b64decode(r.audio_base_64))
                _to_wav48(pcm, out, raw_pcm_rate=48000)
                pcm.unlink(missing_ok=True)
                if r.alignment is not None:   # character timings -> word timings for captions
                    out.with_suffix(".align.json").write_text(json.dumps({
                        "characters": r.alignment.characters,
                        "start": r.alignment.character_start_times_seconds,
                        "end": r.alignment.character_end_times_seconds}))
                cached.write_bytes(out.read_bytes())
                return out
            except Exception as exc:  # try the next model
                last_exc = exc
        raise RuntimeError(f"ElevenLabs failed: {last_exc}")


ENGINES = {"elevenlabs": ElevenLabsTTS, "chatterbox": ChatterboxTTS, "say": SayTTS}


def get_tts(name: str):
    if name not in ENGINES:
        raise ValueError(f"unknown voice engine {name!r} (choose: {', '.join(ENGINES)})")
    return ENGINES[name]()


def list_engines() -> list[dict]:
    out = []
    for name, cls in ENGINES.items():
        ok, detail = cls().available()
        out.append({"name": name, "available": ok, "detail": detail, "langs": ["ar", "en"],
                    "clone": name != "say"})
    return out
