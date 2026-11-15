"""On-screen text corrections. His name is Jweed (j-w-e-e-d); whisper writes
"Juaid"/"Juwaid" — fixed in every caption and card without being asked."""
from __future__ import annotations

import re

from ..config import SETTINGS

_PATTERNS = [(re.compile(p, re.IGNORECASE), repl) for p, repl in SETTINGS.asr.name_fixes]


def apply_fixes(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
