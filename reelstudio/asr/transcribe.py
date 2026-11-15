"""Isolate-transcribe speech-map blocks (never whole takes).

Each block is cut from audio.wav as ``[start - block_pad, end + block_pad]``,
resampled to 16 kHz and transcribed on its own; word times are shifted back to
absolute source seconds and clamped into the slice.

Language: forced when given. Otherwise detected on the 5 longest blocks, majority
vote, then FORCED for every block — per-block auto-detect can silently translate
(Arabic text for English speech, and vice versa).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from ..config import SETTINGS
from ..media.audioio import SR, load_cached, slice_s, to_asr
from ..media.speech import speech_map_array
from ..models import Block, BlockTranscript, Take, Word
from ..util.textnorm import normalize
from .fixes import apply_fixes

ProgressFn = Callable[[float, str], None]

DETECT_BLOCKS = 5                       # longest blocks used for language detection
LANG_CANDIDATES: tuple[str, ...] = ("ar", "en")  # the only languages he records in

# Whisper's well-known output on (near-)silence. Compared on normalize()d whole text.
# Subtitle credits from its training data are never spoken by him: always dropped.
_CREDITS = {
    "ترجمه نانسي قنقر", "نانسي قنقر", "اشتركوا في القناه", "لا تنسوا الاشتراك في القناه",
    "subtitles by the amara org community", "amara org", "please subscribe",
    "like and subscribe", "subscribe to my channel",
}
# Courtesy phrases he might really say: dropped only when the block is near-silent.
_BOILERPLATE = {
    "thank you", "thank you very much", "thanks", "thanks for watching",
    "thank you for watching", "you", "bye", "bye bye", "okay", "oh", "so",
    "شكرا", "شكرا لكم", "شكرا جزيلا", "شكرا للمشاهده", "شكرا على المشاهده",
    "شكرا لكم على المشاهده", "موسيقى", "music",
}
NEAR_SILENT_SPEECH_S = 0.5              # less real speech than this in the slice
LOW_PROB = 0.40                         # or whisper itself is unsure of every word


def is_hallucination(words: list[Word], speech_s: float) -> bool:
    """True when the words are whisper boilerplate rather than speech."""
    text = normalize(" ".join(w.text for w in words))
    if not text:
        return True
    if text in _CREDITS:
        return True
    if text not in _BOILERPLATE:
        return False
    probs = [w.prob for w in words if w.prob is not None]
    unsure = bool(probs) and max(probs) < LOW_PROB
    return speech_s < NEAR_SILENT_SPEECH_S or unsure


def _speech_s(samples48k: np.ndarray) -> float:
    return sum(r.dur for r in speech_map_array(samples48k, SR, SETTINGS.speech))


def _shift(words: list[Word], lo: float, hi: float) -> list[Word]:
    """Relative -> absolute seconds, clamped into [lo, hi], name fixes applied."""
    out = []
    for w in words:
        start = min(max(lo + w.start, lo), hi)
        end = min(max(lo + w.end, start), hi)
        text = apply_fixes(w.text).strip()
        if text:
            out.append(Word(text=text, start=round(start, 3), end=round(end, 3), prob=w.prob))
    return out


def _clean(words: list[Word], samples48k: np.ndarray) -> list[Word]:
    if words and is_hallucination(words, _speech_s(samples48k)):
        return []
    return words


# ── Public API ───────────────────────────────────────────────────────────────
def transcribe_array(samples48k: np.ndarray, *, language: str | None,
                     transcriber) -> list[Word]:
    """Transcribe a 48 kHz mono clip; word times relative to the clip start."""
    duration = len(samples48k) / SR
    words, _, _ = transcriber.transcribe(to_asr(samples48k, SR), language=language, prompt=None)
    return _clean(_shift(words, 0.0, duration), samples48k)


def transcribe_span(wav: Path, start: float, end: float, *, language: str | None,
                    transcriber) -> list[Word]:
    """Transcribe ``[start, end]`` of a 48 kHz WAV; word times absolute (source seconds)."""
    samples = load_cached(wav, SR)
    lo = max(0.0, start)
    hi = min(len(samples) / SR, end)
    if hi <= lo:
        return []
    clip = slice_s(samples, SR, lo, hi)
    words, _, _ = transcriber.transcribe(to_asr(clip, SR), language=language, prompt=None)
    return _clean(_shift(words, lo, hi), clip)


def _detect_one(transcriber, audio16k: np.ndarray):
    """(language, prob, words-or-None). Uses a cheap detector when the engine has one."""
    detect = getattr(transcriber, "detect_language", None)
    if callable(detect):
        lang, prob = detect(audio16k, candidates=LANG_CANDIDATES)
        return lang, prob, None
    words, lang, prob = transcriber.transcribe(audio16k, language=None, prompt=None)
    return lang, prob, words


def _majority(votes: list[tuple[str, float | None]]) -> str | None:
    """Most frequent language; ties broken by summed probability.
    Votes for languages he never records in count only when nothing else voted."""
    preferred = [v for v in votes if v[0] in LANG_CANDIDATES]
    votes = preferred or votes
    tally: dict[str, tuple[int, float]] = {}
    for lang, prob in votes:
        n, p = tally.get(lang, (0, 0.0))
        tally[lang] = (n + 1, p + (prob or 0.0))
    if not tally:
        return None
    return max(tally, key=lambda k: tally[k])


def transcribe_blocks(take: Take, blocks: list[Block], *, language: str | None,
                      transcriber, progress: ProgressFn | None = None) -> list[BlockTranscript]:
    """Isolate-transcribe every block of one take (output order == input order)."""
    if not take.audio_path:
        raise ValueError(f"take {take.id} has no audio_path (run ingest first)")
    if not blocks:
        return []
    samples = load_cached(Path(take.audio_path), SR)
    total = len(samples) / SR
    pad = SETTINGS.asr.block_pad_s
    report = progress or (lambda f, m: None)

    # Slice every block once: (lo, hi, 48 kHz clip, 16 kHz clip).
    clips = {}
    for b in blocks:
        lo, hi = max(0.0, b.start - pad), min(total, b.end + pad)
        clip = slice_s(samples, SR, lo, hi)
        clips[b.id] = (lo, hi, clip, to_asr(clip, SR))

    # Language: forced, or majority of the longest blocks, then forced for all.
    lang = language
    detected: dict[str, tuple[str | None, float | None, list[Word] | None]] = {}
    if lang is None:
        longest = sorted(blocks, key=lambda b: b.end - b.start, reverse=True)[:DETECT_BLOCKS]
        for i, b in enumerate(longest):
            detected[b.id] = _detect_one(transcriber, clips[b.id][3])
            report(0.1 * (i + 1) / len(longest), f"{take.id}: detecting language")
        lang = _majority([(l, p) for l, p, _ in detected.values() if l])

    out: list[BlockTranscript] = []
    for i, b in enumerate(blocks):
        lo, hi, clip48, clip16 = clips[b.id]
        d_lang, d_prob, d_words = detected.get(b.id, (None, None, None))
        if d_words is not None and d_lang == lang:
            words = d_words           # detection pass already transcribed it in `lang`
        else:
            words, _, _ = transcriber.transcribe(clip16, language=lang, prompt=None)
        words = _clean(_shift(words, lo, hi), clip48)
        out.append(BlockTranscript(
            block_id=b.id, take_id=take.id,
            language=lang, language_prob=d_prob if d_lang == lang else None,
            text=" ".join(w.text for w in words), words=words,
            engine=getattr(transcriber, "name", ""), model=getattr(transcriber, "model", ""),
            forced_language=lang is not None,
        ))
        report(0.1 + 0.9 * (i + 1) / len(blocks), f"{take.id}: transcribed {i + 1}/{len(blocks)}")
    return out
