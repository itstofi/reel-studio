"""ASR: engines protocol, block isolation/offsets, forced majority language, hallucination
blanking, name fixes. Slow tests run real whisper large-v3 on macOS `say` speech."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from reelstudio.asr import engines
from reelstudio.asr.engines import (ElevenLabsScribe, FasterWhisper, MlxWhisper, Transcriber,
                                    get_transcriber, norm_lang, resolve_mlx_repo)
from reelstudio.asr.transcribe import (_majority, is_hallucination, transcribe_array,
                                       transcribe_blocks, transcribe_span)
from reelstudio.config import Settings
from reelstudio.media.audioio import SR, write_wav
from reelstudio.media.speech import blocks_from_map, speech_map
from reelstudio.models import Block, Span, Take, Word


# ── fakes ────────────────────────────────────────────────────────────────────
def _tone(seconds: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _take_wav(tmp_path, layout):
    """layout: list of (silence_s, tone_s) -> wav with tone bursts as 'speech'."""
    parts = []
    for silence, tone in layout:
        parts += [np.zeros(int(silence * SR), np.float32), _tone(tone)]
    parts.append(np.zeros(SR, np.float32))
    return write_wav(tmp_path / "audio.wav", np.concatenate(parts))


class FakeASR:
    """Words spread across the clip; language = per-call script or forced value."""
    name, model = "fake", "fake-1"

    def __init__(self, text="hello Juaid world", detect=None, words_fn=None):
        self.text = text
        self.detect = list(detect or [])
        self.calls: list[tuple[int, str | None]] = []
        self.words_fn = words_fn

    def transcribe(self, audio, *, language=None, prompt=None):
        self.calls.append((len(audio), language))
        dur = len(audio) / 16000
        if self.words_fn:
            return self.words_fn(dur), language, None
        toks = self.text.split()
        step = dur / max(1, len(toks))
        words = [Word(text=t, start=i * step, end=(i + 0.9) * step, prob=0.9)
                 for i, t in enumerate(toks)]
        lang = language or (self.detect.pop(0) if self.detect else "en")
        return words, lang, None if language else 0.8


class FakeDetectASR(FakeASR):
    def __init__(self, langs, **kw):
        super().__init__(**kw)
        self.langs = list(langs)
        self.detect_calls = 0

    def detect_language(self, audio, *, candidates=None):
        self.detect_calls += 1
        return self.langs.pop(0), 0.9


def _blocks(take_id, spans):
    return [Block(id=f"{take_id}_b{i + 1:03d}", take_id=take_id, start=a, end=b,
                  runs=[Span(start=a, end=b)]) for i, (a, b) in enumerate(spans)]


def _take(wav):
    return Take(id="t01", filename="x.mov", source_path="/x.mov", order=1, audio_path=str(wav))


# ── engines ──────────────────────────────────────────────────────────────────
def test_mlx_model_map_never_uses_pypi_default():
    assert resolve_mlx_repo("large-v3") == "mlx-community/whisper-large-v3-mlx"
    assert resolve_mlx_repo("turbo") == "mlx-community/whisper-large-v3-turbo"
    assert resolve_mlx_repo("someone/custom") == "someone/custom"


def test_get_transcriber_is_cached_singleton_per_engine_model():
    s = Settings()
    a = get_transcriber(s)
    assert a is get_transcriber(s) and isinstance(a, MlxWhisper) and isinstance(a, Transcriber)
    assert a.model == s.asr.model
    t = get_transcriber(s, model="turbo")
    assert t is not a and t.repo.endswith("whisper-large-v3-turbo")
    assert isinstance(get_transcriber(s, engine="faster"), FasterWhisper)
    with pytest.raises(ValueError):
        get_transcriber(s, engine="nope")


def test_norm_lang_handles_scribe_codes():
    assert [norm_lang(c) for c in ("ara", "eng", "ar-SA", "EN", None)] == ["ar", "en", "ar", "en", None]


def test_faster_whisper_missing_is_a_clear_error():
    pytest.importorskip  # noqa: B018 (keep pytest import used)
    try:
        import faster_whisper  # noqa: F401
        pytest.skip("faster-whisper installed")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="faster"):
        FasterWhisper().transcribe(np.zeros(16000, np.float32), language="en")


def test_scribe_keeps_only_words_and_normalises_language():
    W = lambda text, s, e, typ="word": SimpleNamespace(text=text, start=s, end=e, type=typ, logprob=-0.1)
    resp = SimpleNamespace(language_code="ara", language_probability=0.97, words=[
        W("مرحبا", 0.1, 0.5), W(" ", 0.5, 0.6, "spacing"), W("(laughs)", 0.6, 0.9, "audio_event"),
        W("Claude", 1.0, 1.4), W("x", None, None)])
    seen = {}

    def convert(**kw):
        seen.update(kw)
        return resp
    client = SimpleNamespace(speech_to_text=SimpleNamespace(convert=convert))
    words, lang, prob = ElevenLabsScribe(client=client).transcribe(np.zeros(16000, np.float32),
                                                                    language=None)
    assert [w.text for w in words] == ["مرحبا", "Claude"] and lang == "ar" and prob == 0.97
    assert seen["model_id"] == "scribe_v3" and seen["tag_audio_events"] is False
    assert seen["timestamps_granularity"] == "word" and "language_code" not in seen
    ElevenLabsScribe(client=client).transcribe(np.zeros(16000, np.float32), language="en")
    assert seen["language_code"] == "en"


def test_scribe_requires_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        ElevenLabsScribe()


def test_short_audio_returns_nothing_without_loading_a_model():
    m = MlxWhisper("large-v3")
    assert m.transcribe(np.zeros(100, np.float32), language="ar") == ([], "ar", None)


# ── transcribe.py ────────────────────────────────────────────────────────────
def test_blocks_offsets_clamp_and_fixes(tmp_path):
    wav = _take_wav(tmp_path, [(1.0, 1.0), (2.0, 1.5)])     # speech 1-2 s, 4-5.5 s
    blocks = _blocks("t01", [(1.0, 2.0), (4.0, 5.5)])
    asr = FakeASR()
    out = transcribe_blocks(_take(wav), blocks, language="en", transcriber=asr, progress=None)
    assert [t.block_id for t in out] == ["t01_b001", "t01_b002"]
    pad = Settings().asr.block_pad_s
    for t, b in zip(out, blocks):
        assert t.language == "en" and t.forced_language and t.engine == "fake"
        assert [w.text for w in t.words] == ["hello", "Jweed", "world"]   # name fix
        assert t.text == "hello Jweed world"
        assert all(b.start - pad - 1e-6 <= w.start <= w.end <= b.end + pad + 1e-6 for w in t.words)
        assert t.words[0].start == pytest.approx(b.start - pad, abs=1e-3)   # absolute time
    # every call forced the language; slices are block +- pad at 16 kHz
    assert all(lang == "en" for _, lang in asr.calls)
    assert asr.calls[0][0] == pytest.approx((1.0 + 2 * pad) * 16000, abs=2)


def test_words_past_the_slice_are_clamped(tmp_path):
    wav = _take_wav(tmp_path, [(1.0, 1.0)])
    asr = FakeASR(words_fn=lambda d: [Word(text="late", start=d - 0.1, end=d + 5.0)])
    (t,) = transcribe_blocks(_take(wav), _blocks("t01", [(1.0, 2.0)]), language="ar",
                             transcriber=asr, progress=None)
    assert t.words[0].end == pytest.approx(2.0 + Settings().asr.block_pad_s, abs=1e-3)


def test_language_majority_from_longest_blocks_then_forced(tmp_path):
    spans = [(1.0, 1.5), (3.0, 5.0), (6.0, 8.5), (9.5, 10.0), (11.0, 13.0), (14.0, 16.0),
             (17.0, 17.5)]
    wav = _take_wav(tmp_path, [(1.0, 0.5), (1.5, 2.0), (1.0, 2.5), (1.0, 0.5), (1.0, 2.0),
                               (1.0, 2.0), (1.0, 0.5)])
    # Engine without a detector: detection = full transcribe on the 5 longest blocks.
    asr = FakeASR(detect=["ar", "en", "ar", "ar", "en"])
    progress = []
    out = transcribe_blocks(_take(wav), _blocks("t01", spans), language=None, transcriber=asr,
                            progress=lambda f, m: progress.append(f))
    assert {t.language for t in out} == {"ar"} and all(t.forced_language for t in out)
    detect_calls = [c for c in asr.calls if c[1] is None]
    assert len(detect_calls) == 5
    # blocks detected as 'ar' are reused, the 2 'en' + 2 short blocks are re-run forced 'ar'
    assert len([c for c in asr.calls if c[1] == "ar"]) == 4
    assert progress[-1] == pytest.approx(1.0) and progress == sorted(progress)


def test_language_detector_is_preferred(tmp_path):
    wav = _take_wav(tmp_path, [(1.0, 1.0), (1.0, 2.0)])
    asr = FakeDetectASR(["en", "en"])
    out = transcribe_blocks(_take(wav), _blocks("t01", [(1.0, 2.0), (3.0, 5.0)]), language=None,
                            transcriber=asr, progress=None)
    assert asr.detect_calls == 2 and all(lang == "en" for _, lang in asr.calls)
    assert [t.language_prob for t in out] == [0.9, 0.9]


def test_majority_ignores_foreign_votes_and_breaks_ties_by_prob():
    assert _majority([("fa", 0.9), ("ar", 0.5), ("fa", 0.9)]) == "ar"
    assert _majority([("ar", 0.6), ("en", 0.9)]) == "en"
    assert _majority([("ur", 0.6)]) == "ur" and _majority([]) is None


def test_hallucination_rules():
    w = lambda text, p=0.9: [Word(text=t, start=0, end=0.1, prob=p) for t in text.split()]
    assert is_hallucination([], 2.0)
    assert is_hallucination(w("Thank you."), 0.2)              # near-silent boilerplate
    assert not is_hallucination(w("Thank you."), 1.0)          # he really said it
    assert is_hallucination(w("Thank you.", p=0.1), 1.0)       # whisper unsure
    assert is_hallucination(w("ترجمة نانسي قنقر"), 3.0)          # subtitle credits: always
    assert not is_hallucination(w("Anthropic released a new model"), 0.2)


def test_hallucinated_block_is_kept_blank(tmp_path):
    wav = _take_wav(tmp_path, [(1.0, 0.3), (2.0, 1.5)])
    out = transcribe_blocks(_take(wav), _blocks("t01", [(1.0, 1.3), (3.3, 4.8)]), language="en",
                            transcriber=FakeASR(text="Thank you."), progress=None)
    assert out[0].words == [] and out[0].text == ""            # 0.3 s of speech
    assert [w.text for w in out[1].words] == ["Thank", "you."]  # 1.5 s: real speech


def test_span_and_array(tmp_path):
    wav = _take_wav(tmp_path, [(1.0, 2.0)])
    words = transcribe_span(wav, 0.5, 3.5, language="en", transcriber=FakeASR())
    assert words[0].start == pytest.approx(0.5) and all(0.5 <= w.start <= w.end <= 3.5 for w in words)
    rel = transcribe_array(_tone(2.0), language="en", transcriber=FakeASR())
    assert rel[0].start == 0.0 and rel[-1].end <= 2.0 and rel[1].text == "Jweed"
    assert transcribe_span(wav, 5.0, 4.0, language="en", transcriber=FakeASR()) == []


def test_requires_audio_path():
    with pytest.raises(ValueError):
        transcribe_blocks(Take(id="t01", filename="x", source_path="/x", order=1),
                          _blocks("t01", [(0, 1)]), language="en", transcriber=FakeASR(),
                          progress=None)


# ── slow: real whisper large-v3 ──────────────────────────────────────────────
def _say(tmp_path, voice, text, name):
    if not shutil.which("say"):
        pytest.skip("macOS `say` not available")
    aiff = tmp_path / f"{name}.aiff"
    wav = tmp_path / f"{name}.wav"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", str(SR),
                    "-c:a", "pcm_f32le", str(wav)], check=True)
    return wav


EN_TEXT = "Hi friends, I am Jweed from Iraq. Anthropic released a new model today."
AR_TEXT = "مرحبا يا أصدقاء، أنا جويد من العراق. أطلقت أنثروبيك نموذجا جديدا اليوم."


def _check_words(words, lo, hi):
    assert len(words) >= 5
    inside = [lo - 1e-6 <= w.start <= w.end <= hi + 1e-6 for w in words]
    assert all(inside)
    starts = [w.start for w in words]
    mono = sum(b >= a for a, b in zip(starts, starts[1:])) / max(1, len(starts) - 1)
    assert mono >= 0.9


@pytest.mark.slow
def test_real_whisper_english_and_arabic(tmp_path):
    from reelstudio.media.audioio import load_cached

    asr = get_transcriber(Settings(), engine="mlx", model="large-v3")
    for voice, text, expect, key in (("Samantha", EN_TEXT, "en", "anthropic"),
                                     ("Majed", AR_TEXT, "ar", "العراق")):
        wav = _say(tmp_path, voice, text, voice)
        samples = load_cached(wav, SR)
        lang, prob = asr.detect_language(samples[::3].copy(), candidates=None) \
            if False else asr.detect_language(__import__("reelstudio.media.audioio", fromlist=["to_asr"]).to_asr(samples, SR))
        assert lang == expect and prob > 0.5, (voice, lang, prob)
        words, wlang, wprob = asr.transcribe(
            __import__("reelstudio.media.audioio", fromlist=["to_asr"]).to_asr(samples, SR),
            language=None, prompt=None)
        assert wlang == expect and wprob is not None
        rel = transcribe_array(samples, language=expect, transcriber=asr)
        _check_words(rel, 0.0, len(samples) / SR)
        joined = " ".join(w.text for w in rel).lower()
        assert key in joined, joined
        print(f"\n{voice}: {lang} p={prob:.3f} :: {' '.join(w.text for w in rel)}")


@pytest.mark.slow
def test_real_blocks_detect_language_and_force(tmp_path):
    """Two sentences with a pause -> two blocks; language detected, absolute word times."""
    asr = get_transcriber(Settings(), engine="mlx", model="large-v3")
    a = _say(tmp_path, "Samantha", "Breaking news from the world of AI.", "a")
    b = _say(tmp_path, "Samantha", "Follow me to learn more.", "b")
    from reelstudio.media.audioio import load_cached
    audio = np.concatenate([np.zeros(SR, np.float32), load_cached(a, SR),
                            np.zeros(2 * SR, np.float32), load_cached(b, SR),
                            np.zeros(SR, np.float32)])
    wav = write_wav(tmp_path / "audio.wav", audio)
    sm = speech_map("t01", wav)
    blocks = blocks_from_map(sm)
    assert len(blocks) == 2
    out = transcribe_blocks(_take(wav), blocks, language=None, transcriber=asr, progress=None)
    assert all(t.language == "en" and t.forced_language for t in out)
    pad = Settings().asr.block_pad_s
    for t, blk in zip(out, blocks):
        _check_words(t.words, blk.start - pad, blk.end + pad) if len(t.words) >= 5 else None
        assert t.words and all(blk.start - pad <= w.start <= blk.end + pad for w in t.words)
    assert "follow" in out[1].text.lower()


@pytest.mark.slow
def test_real_whisper_from_another_thread(tmp_path):
    """MLX streams are thread-local; the engine must work from the job worker thread."""
    asr = get_transcriber(Settings(), engine="mlx", model="large-v3")
    from reelstudio.media.audioio import load_cached
    samples = load_cached(_say(tmp_path, "Samantha", "Follow me to learn more.", "t"), SR)
    result = {}
    th = threading.Thread(target=lambda: result.update(
        w=transcribe_array(samples, language="en", transcriber=asr)))
    th.start()
    th.join()
    assert "follow" in " ".join(w.text for w in result["w"]).lower()


@pytest.mark.slow
def test_real_speed_large_v3(tmp_path):
    """Report seconds of compute per minute of audio (isolated ~6-8 s blocks)."""
    asr = get_transcriber(Settings(), engine="mlx", model="large-v3")
    from reelstudio.media.audioio import load_cached
    text = ("Artificial intelligence is changing how we work. Every week a new model arrives, "
            "and every week the tools get faster. Today I will show you what changed, "
            "why it matters, and how you can use it in your own projects.")
    samples = load_cached(_say(tmp_path, "Samantha", text, "speed"), SR)
    asr.transcribe(np.zeros(16000, np.float32), language="en")       # warm (model load)
    t0 = time.perf_counter()
    reps = 0
    audio_s = 0.0
    while audio_s < 60.0:
        transcribe_array(samples, language="en", transcriber=asr)
        audio_s += len(samples) / SR
        reps += 1
    spm = (time.perf_counter() - t0) / audio_s * 60.0
    print(f"\nlarge-v3 mlx: {spm:.1f} s per minute of audio ({reps} x {len(samples) / SR:.1f} s clips)")
    assert spm < 120
