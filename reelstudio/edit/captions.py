"""Word-synced captions — ALWAYS ENGLISH, even on the Arabic cut (a translation of the
line, never an Arabic transcript). Same styling on both cuts.

en cut: the script's English words, timed by the matched transcript words (the display
        text keeps the script's spelling; "Jweed" is fixed upstream).
ar cut: the beat's English caption spread over the beat's SPEECH in output time
        (weight = len(word) + 2), so the text paces with his voice and holds in pauses.

Cues hold <= caption_max_words / caption_max_chars, break at punctuation and pauses,
never overlap, and stay inside [0, timeline duration]. Re-cutting invalidates all of
this: captions are always rebuilt from the current timeline.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import FONTS_DIR, SETTINGS, LayoutSpec
from ..models import (BlockTranscript, CaptionCue, CaptionWord, Layout, Plan, SpeechMap,
                      Timeline)
from ..util.textnorm import words as norm_words
from .align import WORD_MATCH, _align
from .edl import beat_spans, src_to_out

PAUSE_BREAK_S = 0.35
MIN_CUE_S = 0.5
HOLD_S = 0.25
CAPTION_FONT = "Montserrat ExtraBold"
HIGHLIGHT = "#FFD400"


# ── timing ───────────────────────────────────────────────────────────────────
def _speech_intervals(beat_id: str, tl: Timeline, speech_maps: dict[str, SpeechMap]) -> list[tuple[float, float]]:
    """Output-time intervals where he is speaking inside this beat."""
    out: list[tuple[float, float]] = []
    for e in tl.entries:
        if e.beat_id != beat_id:
            continue
        a, b = e.src_in_f / tl.fps, e.src_out_f / tl.fps
        o = e.out_in_f / tl.fps
        sm = speech_maps.get(e.take_id)
        for r in (sm.runs if sm else []):
            lo, hi = max(a, r.start), min(b, r.end)
            if hi > lo:
                out.append((round(o + lo - a, 4), round(o + hi - a, 4)))
    return out


def distribute(tokens: list[str], intervals: list[tuple[float, float]]) -> list[CaptionWord]:
    """Spread tokens over the speech intervals (weight len+2), skipping the pauses."""
    if not tokens or not intervals:
        return []
    total = sum(b - a for a, b in intervals)
    weights = [len(t) + 2 for t in tokens]
    scale = total / sum(weights)

    def clock_to_time(c: float, leftmost: bool) -> float:
        acc = 0.0
        for a, b in intervals:
            d = b - a
            if c < acc + d or (not leftmost and c <= acc + d):
                return a + (c - acc)
            acc += d
        return intervals[-1][1]

    words, c = [], 0.0
    for tok, w in zip(tokens, weights):
        d = w * scale
        start = clock_to_time(c, leftmost=True)
        end = clock_to_time(c + d, leftmost=False)
        words.append(CaptionWord(text=tok, start=round(start, 3), end=round(max(end, start + 0.05), 3)))
        c += d
    return words


def _time_by_transcript(tokens: list[str], beat_id: str, tl: Timeline,
                        transcripts: list[BlockTranscript], intervals) -> list[CaptionWord]:
    """en cut: script tokens take the times of their matched transcript words."""
    spoken: list[tuple[str, float, float]] = []
    for e in (x for x in tl.entries if x.beat_id == beat_id):
        a, b = e.src_in_f / tl.fps, e.src_out_f / tl.fps
        for tr in transcripts:
            if tr.take_id != e.take_id:
                continue
            for w in tr.words:
                mid = (w.start + w.end) / 2
                if a <= mid < b:
                    s = src_to_out(tl, e.take_id, max(a, w.start))
                    t = src_to_out(tl, e.take_id, min(b - 1e-6, w.end))
                    if s is not None and t is not None:
                        for tok in norm_words(w.text) or [w.text]:
                            spoken.append((tok, s, t))
    spoken.sort(key=lambda x: x[1])
    norm_tokens = [(norm_words(t) or [t.lower()])[0] for t in tokens]
    match = _align(norm_tokens, [s[0] for s in spoken]) if spoken else None
    if not match or len(match.pairs) < max(1, len(tokens) // 2):
        return distribute(tokens, intervals)  # transcript too different: pace by speech
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    for li, wj, sim in match.pairs:
        if sim >= WORD_MATCH:
            times[li] = (spoken[wj][1], spoken[wj][2])
    # interpolate unmatched tokens between their matched neighbours
    known = [i for i, t in enumerate(times) if t is not None]
    for i, t in enumerate(times):
        if t is not None:
            continue
        left = max((k for k in known if k < i), default=None)
        right = min((k for k in known if k > i), default=None)
        lo = times[left][1] if left is not None else (intervals[0][0] if intervals else 0.0)
        hi = times[right][0] if right is not None else (intervals[-1][1] if intervals else lo + 0.3)
        span_tokens = (right if right is not None else len(tokens)) - (left if left is not None else -1) - 1
        pos = i - (left if left is not None else -1)
        step = max(hi - lo, 0.05) / (span_tokens + 1)
        start = lo + step * (pos - 1) + step * 0.5 if span_tokens else lo
        times[i] = (start, start + max(step * 0.9, 0.05))
    out = [CaptionWord(text=tok, start=round(t[0], 3), end=round(max(t[1], t[0] + 0.05), 3))
           for tok, t in zip(tokens, times)]
    for a, b in zip(out, out[1:]):  # keep word order monotonic
        if b.start < a.start:
            b.start = a.start
            b.end = max(b.end, b.start + 0.05)
    return out


# ── chunking ─────────────────────────────────────────────────────────────────
_BREAK_AFTER = re.compile(r"[.,!?;:…،؛؟]$")


def chunk(words: list[CaptionWord], beat_id: str, spec: LayoutSpec, start_index: int = 0) -> list[CaptionCue]:
    cues: list[CaptionCue] = []
    cur: list[CaptionWord] = []

    def flush():
        if cur:
            cues.append(CaptionCue(id=f"c{start_index + len(cues) + 1:03d}", beat_id=beat_id,
                                   start=cur[0].start, end=cur[-1].end, words=list(cur)))
            cur.clear()

    for w in words:
        if cur:
            chars = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            if (len(cur) >= spec.caption_max_words or chars > spec.caption_max_chars
                    or _BREAK_AFTER.search(cur[-1].text) or w.start - cur[-1].end > PAUSE_BREAK_S):
                flush()
        cur.append(w)
    flush()
    return cues


def _finalize(cues: list[CaptionCue], duration: float) -> list[CaptionCue]:
    cues = sorted((c for c in cues if c.words), key=lambda c: c.start)
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else duration
        c.start = max(0.0, c.start)
        want = max(c.end + HOLD_S, c.start + MIN_CUE_S)
        c.end = round(min(want, nxt, duration), 3)
        for w in c.words:
            w.start = max(c.start, min(w.start, c.end))
            w.end = max(w.start, min(w.end, c.end))
    return [c for c in cues if c.end > c.start]


def build_cues(plan: Plan, timeline: Timeline, transcripts: list[BlockTranscript],
               speech_maps: dict[str, SpeechMap], *, lang: str,
               spec: LayoutSpec | None = None) -> list[CaptionCue]:
    spec = spec or SETTINGS.layout
    spans = beat_spans(timeline)
    cues: list[CaptionCue] = []
    for beat in plan.beats:
        if not beat.enabled or beat.id not in spans or not beat.caption_en.strip():
            continue
        intervals = _speech_intervals(beat.id, timeline, speech_maps) or [spans[beat.id]]
        tokens = beat.caption_en.split()
        if lang == "en":
            timed = _time_by_transcript(tokens, beat.id, timeline, transcripts, intervals)
        else:
            timed = distribute(tokens, intervals)
        cues.extend(chunk(timed, beat.id, spec, start_index=len(cues)))
    return _finalize(cues, timeline.duration)


# ── ASS output ───────────────────────────────────────────────────────────────
def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def write_ass(cues: list[CaptionCue], layout: Layout, out: Path, *, font: str = CAPTION_FONT,
              font_px: int | None = None, highlight: str = HIGHLIGHT, color: str = "#FFFFFF") -> Path:
    """One Dialogue per active-word slice; the active word is highlighted."""
    font_px = font_px or SETTINGS.layout.caption_font_px
    cx = (layout.safe_x0 + layout.safe_x1) // 2
    cy = int((layout.caption_band.start + layout.caption_band.end) / 2)
    margin_l = layout.safe_x0
    margin_r = layout.canvas_w - layout.safe_x1
    hl = _ass_color(highlight)
    base = _ass_color(color)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout.canvas_w}",
        f"PlayResY: {layout.canvas_h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{font_px},{base},{hl},&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,"
        f"{max(4, font_px // 16)},{max(2, font_px // 40)},5,{margin_l},{margin_r},0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        ws = cue.words
        for k, w in enumerate(ws):
            start = cue.start if k == 0 else w.start
            end = ws[k + 1].start if k + 1 < len(ws) else cue.end
            if end <= start:
                continue
            parts = []
            for j, x in enumerate(ws):
                t = _escape(x.text)
                parts.append(f"{{\\c{hl}&}}{t}{{\\c{base}&}}" if j == k else t)
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,"
                         f"{{\\an5\\pos({cx},{cy})}}" + " ".join(parts))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def reshape_arabic(text: str) -> str:
    """Optional Arabic-caption path: pre-shape for libass (lam-alef tofu bug). reshape()
    ONLY — adding bidi.get_display double-reverses the glyphs."""
    import arabic_reshaper
    return arabic_reshaper.reshape(text)


def fonts_dir() -> Path:
    return FONTS_DIR
