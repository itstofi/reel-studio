"""Speech-to-text engines behind one protocol.

Every engine takes 16 kHz mono float32 audio and returns
``(words with times relative to the clip, language, language probability)``.
House rules for every engine: word timestamps on, VAD off, no conditioning on
previous text, deterministic (greedy, temperature 0) decoding.

- ``MlxWhisper``      default; whisper large-v3 on the Apple GPU via mlx-whisper.
- ``FasterWhisper``   optional extra (``faster-whisper``), CPU int8 — cross-check.
- ``ElevenLabsScribe`` cloud (needs ELEVENLABS_API_KEY) — second opinion.
"""
from __future__ import annotations

import io
import logging
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

import numpy as np

from ..config import SETTINGS, Settings
from ..models import Word

log = logging.getLogger("reelstudio.asr")

ASR_SR = 16000
MIN_SAMPLES = ASR_SR // 10          # < 100 ms of audio: nothing to transcribe

# Short model names -> mlx-community repos. ALWAYS pass a repo: the PyPI default is tiny.
MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
SCRIBE_DEFAULT = "scribe_v3"

# ISO-639-3 / locale codes Scribe may return -> the 2-letter codes whisper uses.
_LANG3 = {"ara": "ar", "eng": "en", "arb": "ar"}


@runtime_checkable
class Transcriber(Protocol):
    name: str
    model: str

    def transcribe(self, audio: np.ndarray, *, language: str | None,
                   prompt: str | None) -> tuple[list[Word], str | None, float | None]: ...


def norm_lang(code: str | None) -> str | None:
    """'ara' / 'ar-SA' / 'AR' -> 'ar'."""
    if not code:
        return None
    code = code.strip().lower().replace("_", "-").split("-")[0]
    return _LANG3.get(code, code)


def _as_audio(audio: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))


def _word(text: str, start: float | None, end: float | None, prob: float | None) -> Word | None:
    text = (text or "").strip()
    if not text or start is None or end is None:
        return None
    start, end = float(start), float(end)
    return Word(text=text, start=start, end=max(start, end),
                prob=None if prob is None else float(prob))


# ── mlx-whisper ──────────────────────────────────────────────────────────────
# MLX streams are thread-local: a model loaded on one thread raises "There is no
# Stream(gpu, N) in current thread" when used from another (verified, mlx 0.32).
# mlx-whisper also keeps ONE global model (ModelHolder). So every MLX call — load,
# detect, transcribe — runs on this single dedicated thread, which also serialises them.
_MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-asr")


def resolve_mlx_repo(model: str) -> str:
    return MLX_MODELS.get(model, model)


def _local_snapshot(repo: str) -> str:
    """Local snapshot dir of an HF repo (cache first, so no network when present)."""
    from pathlib import Path

    if Path(repo).exists():
        return repo
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo_id=repo, local_files_only=True)
    except Exception:  # not cached yet -> one-time download
        log.info("downloading %s", repo)
        return snapshot_download(repo_id=repo)


class MlxWhisper:
    name = "mlx"

    def __init__(self, model: str = "large-v3"):
        self.model = model
        self.repo = resolve_mlx_repo(model)
        self._path: str | None = None
        self._lock = threading.Lock()

    def _run(self, fn, *args, **kwargs):
        with self._lock:
            return _MLX_EXECUTOR.submit(fn, *args, **kwargs).result()

    def _model(self):
        # runs on the MLX thread
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        if self._path is None:
            self._path = _local_snapshot(self.repo)
        return ModelHolder.get_model(self._path, mx.float16)

    def _detect(self, audio: np.ndarray) -> dict[str, float]:
        # transcribe() discards the probabilities, so they are recomputed here.
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim

        m = self._model()
        if not m.is_multilingual:
            return {"en": 1.0}
        mel = log_mel_spectrogram(audio, n_mels=m.dims.n_mels, padding=N_SAMPLES)
        _, probs = m.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
        return {k: float(v) for k, v in probs.items()}

    def language_probs(self, audio: np.ndarray) -> dict[str, float]:
        """Whisper's language distribution for the first 30 s of ``audio``."""
        audio = _as_audio(audio)
        if len(audio) < MIN_SAMPLES:
            return {}
        return self._run(self._detect, audio)

    def detect_language(self, audio: np.ndarray, *,
                        candidates: tuple[str, ...] | None = None) -> tuple[str | None, float | None]:
        """Most likely language (optionally restricted to ``candidates``) and its probability."""
        probs = self.language_probs(audio)
        if candidates:
            probs = {k: v for k, v in probs.items() if k in candidates}
        if not probs:
            return None, None
        lang = max(probs, key=probs.get)
        return lang, probs[lang]

    def _transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None):
        import mlx_whisper

        prob = None
        if language is None:
            probs = self._detect(audio)
            language = max(probs, key=probs.get)
            prob = probs[language]
        self._model()  # resolve the local path before handing it to mlx-whisper
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._path,
            word_timestamps=True,
            condition_on_previous_text=False,
            temperature=0.0,                 # greedy, deterministic (A/B diffs compare runs)
            initial_prompt=prompt,
            language=language,
            task="transcribe",
            verbose=None,
        )
        words = [w for seg in result.get("segments", []) for raw in seg.get("words", [])
                 if (w := _word(raw.get("word", ""), raw.get("start"), raw.get("end"),
                                raw.get("probability"))) is not None]
        return words, result.get("language") or language, prob

    def transcribe(self, audio: np.ndarray, *, language: str | None = None,
                   prompt: str | None = None) -> tuple[list[Word], str | None, float | None]:
        audio = _as_audio(audio)
        if len(audio) < MIN_SAMPLES:
            return [], language, None
        return self._run(self._transcribe, audio, norm_lang(language), prompt)


# ── faster-whisper (optional extra) ──────────────────────────────────────────
class FasterWhisper:
    name = "faster"

    def __init__(self, model: str = "large-v3"):
        self.model = model
        self._impl = None
        self._lock = threading.Lock()

    def _load(self):
        if self._impl is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # optional dependency
                raise RuntimeError(
                    "faster-whisper is not installed: `uv sync --extra faster`") from exc
            # Apple Silicon: CPU only, float16 falls back to float32 -> use int8.
            self._impl = WhisperModel(self.model, device="cpu", compute_type="int8")
        return self._impl

    def transcribe(self, audio: np.ndarray, *, language: str | None = None,
                   prompt: str | None = None) -> tuple[list[Word], str | None, float | None]:
        audio = _as_audio(audio)
        if len(audio) < MIN_SAMPLES:
            return [], language, None
        language = norm_lang(language)
        with self._lock:
            model = self._load()
            segments, info = model.transcribe(
                audio, language=language, task="transcribe",
                beam_size=1, best_of=1, temperature=0.0,
                word_timestamps=True, vad_filter=False,
                condition_on_previous_text=False, initial_prompt=prompt,
            )
            segments = list(segments)  # lazy generator: the work happens here
        words = [w for seg in segments for raw in (seg.words or [])
                 if (w := _word(raw.word, raw.start, raw.end, raw.probability)) is not None]
        # language_probability is 1.0 when the language was given — report None then.
        prob = None if language else getattr(info, "language_probability", None)
        return words, info.language or language, prob


# ── ElevenLabs Scribe (cloud) ────────────────────────────────────────────────
def wav_bytes(audio: np.ndarray, sr: int = ASR_SR) -> bytes:
    """16-bit PCM mono WAV in memory."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class ElevenLabsScribe:
    name = "elevenlabs"

    def __init__(self, model: str = SCRIBE_DEFAULT, *, api_key: str | None = None, client=None):
        self.model = model if model.startswith("scribe") else SCRIBE_DEFAULT
        self._key = api_key or SETTINGS.elevenlabs_key
        self._client = client
        self._lock = threading.Lock()
        if self._client is None and not self._key:
            raise RuntimeError("ElevenLabs Scribe needs ELEVENLABS_API_KEY (.env or environment)")

    def _get_client(self):
        if self._client is None:
            from elevenlabs import ElevenLabs
            self._client = ElevenLabs(api_key=self._key)
        return self._client

    def transcribe(self, audio: np.ndarray, *, language: str | None = None,
                   prompt: str | None = None) -> tuple[list[Word], str | None, float | None]:
        audio = _as_audio(audio)
        if len(audio) < MIN_SAMPLES:          # API minimum is 100 ms
            return [], language, None
        language = norm_lang(language)
        kwargs = dict(
            model_id=self.model,
            file=("block.wav", wav_bytes(audio), "audio/wav"),
            timestamps_granularity="word",
            tag_audio_events=False,           # default true adds 'audio_event' items
            diarize=False,
            temperature=0.0,
            seed=7,
        )
        if language:
            kwargs["language_code"] = language
        if self.model == "scribe_v3":
            # His Arabic reels carry English tech names; only v3 accepts this.
            kwargs["secondary_language_codes"] = ["en"] if language != "en" else ["ar"]
        with self._lock:
            resp = self._get_client().speech_to_text.convert(**kwargs)
        raw_words = getattr(resp, "words", None) or []
        words = [w for raw in raw_words if getattr(raw, "type", "word") == "word"
                 if (w := _word(raw.text, raw.start, raw.end, _logprob_to_p(raw))) is not None]
        lang = norm_lang(getattr(resp, "language_code", None)) or language
        prob = None if language else getattr(resp, "language_probability", None)
        return words, lang, prob


def _logprob_to_p(raw) -> float | None:
    lp = getattr(raw, "logprob", None)
    return None if lp is None else float(np.exp(min(0.0, float(lp))))


# ── Factory ──────────────────────────────────────────────────────────────────
_ENGINES = {"mlx": MlxWhisper, "faster": FasterWhisper, "elevenlabs": ElevenLabsScribe}
_DEFAULT_MODEL = {"mlx": "large-v3", "faster": "large-v3", "elevenlabs": SCRIBE_DEFAULT}
_CACHE: dict[tuple[str, str], Transcriber] = {}
_CACHE_LOCK = threading.Lock()


def get_transcriber(settings: Settings = SETTINGS, engine: str | None = None,
                    model: str | None = None) -> Transcriber:
    """Cached singleton per (engine, model). Model loading is lazy (first transcribe)."""
    engine = (engine or settings.asr.engine or "mlx").lower()
    if engine not in _ENGINES:
        raise ValueError(f"unknown ASR engine {engine!r} (mlx | faster | elevenlabs)")
    if model is None:
        model = settings.asr.model if engine == settings.asr.engine else _DEFAULT_MODEL[engine]
    with _CACHE_LOCK:
        key = (engine, model)
        if key not in _CACHE:
            _CACHE[key] = _ENGINES[engine](model)
        return _CACHE[key]
