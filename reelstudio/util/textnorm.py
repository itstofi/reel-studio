"""Text normalisation for matching transcripts to the script (Arabic + English).

Matching must survive whisper's spelling noise: Arabic diacritics, alef/hamza
variants, ta marbuta, Arabic-Indic digits, punctuation and case. Normalised text
is for COMPARISON only — never show it to the viewer.
"""
from __future__ import annotations

import re
import unicodedata

_AR_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # incl. tatweel
_AR_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
})
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACE = re.compile(r"\s+")
_ARABIC_CHARS = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_SENTENCE_END = re.compile(r"[.!?؟。…]\s*$|[.!?؟]\"?\s*$")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _AR_DIACRITICS.sub("", text)
    text = text.translate(_AR_MAP)
    text = text.replace("’", "'").replace("'", "")
    text = _PUNCT.sub(" ", text.lower())
    return _SPACE.sub(" ", text).strip()


def words(text: str) -> list[str]:
    norm = normalize(text)
    return norm.split(" ") if norm else []


def is_arabic(text: str) -> bool:
    """True when most letters are Arabic script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for c in letters if _ARABIC_CHARS.match(c))
    return arabic / len(letters) > 0.5


def ends_sentence(word: str) -> bool:
    """Raw (un-normalised) token ends a sentence: '.', '!', '?', '؟', '…'."""
    return bool(_SENTENCE_END.search(word.strip()))


def strip_brackets(line: str) -> tuple[str, str | None]:
    """Split a script line into (spoken text, on-screen text from [brackets])."""
    found = re.findall(r"\[([^\]]+)\]", line)
    spoken = _SPACE.sub(" ", re.sub(r"\[[^\]]*\]", " ", line)).strip()
    return spoken, (" / ".join(f.strip() for f in found) if found else None)
