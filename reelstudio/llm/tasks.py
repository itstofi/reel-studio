"""Claude tasks for the planner, the script drafter and caption proofreading.

``ClaudeTasks`` satisfies ``edit.planner.PlannerLLM``. Every prompt states the creator's
rules that matter for the task (they are the spec: .claude/skills/reel-standard/SKILL.md),
every answer is structured JSON validated by pydantic, and every result is checked
again here (counts, ids, English-only, Jweed spelling) before it reaches the plan.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel

from ..asr.fixes import apply_fixes
from ..config import SETTINGS
from ..util.textnorm import is_arabic
from . import claude
from .claude import ClaudeError

TEMPLATES = ("title", "list", "stat", "screenshot", "nametag", "logo", "quote")

# Rules every task shares (from the reel-standard skill).
SYSTEM = """You are the editor inside Reel Studio, the tool that cuts the creator Jweed's talking-head \
Instagram Reels about AI news, AI tools and AI tutorials. He records in Arabic (Modern Standard Arabic) \
or English.

House rules that always apply:
- His name is spelled exactly "Jweed" (J-w-e-e-d). Speech recognition writes "Juaid", "Juwaid", \
"Jwaid" or "جويد": always write "Jweed" in English text.
- Captions are ALWAYS ENGLISH, even on the Arabic cut: a natural English rendering of what he says, \
never an Arabic transcript or transliteration.
- Keep names of people, companies, products, models and versions exactly as spoken \
(Claude, Anthropic, ChatGPT, OpenAI, GPT-5, Gemini ...); numbers stay as digits.
- Never invent facts, numbers or claims that he did not say.
- Answer only with the requested JSON."""


def _numbered(items: list[str]) -> str:
    return json.dumps([{"i": i, "text": t} for i, t in enumerate(items)], ensure_ascii=False, indent=1)


def _fix(text: str) -> str:
    return apply_fixes(re.sub(r"\s+", " ", text or "").strip())


# ── Schemas ──────────────────────────────────────────────────────────────────
class _Indexed(BaseModel):
    i: int
    text: str


class _IndexedList(BaseModel):
    lines: list[_Indexed]


class _UnscriptedBeat(BaseModel):
    block_ids: list[str]
    caption_en: str
    reason: str


class _UnscriptedPlan(BaseModel):
    beats: list[_UnscriptedBeat]


class GraphicParams(BaseModel):
    """Union of the template parameters (unused ones are null). See GRAPHIC_PARAMS."""
    text: str | None = None          # title / quote main text
    subtitle: str | None = None      # title
    title: str | None = None         # list heading
    items: list[str] | None = None   # list rows
    value: str | None = None         # stat number exactly as spoken ("95", "3x", "20")
    prefix: str | None = None        # stat ("$")
    suffix: str | None = None        # stat ("%", "x", " hours")
    label: str | None = None         # stat label
    source: str | None = None        # screenshot: product/page to capture; logo: brand
    highlight: str | None = None     # screenshot: line/element to highlight
    name: str | None = None          # nametag / quote author
    role: str | None = None          # nametag


GRAPHIC_PARAMS = {
    "title": "text (<= 5 words), optional subtitle",
    "list": "title, items (2-4 short rows)",
    "stat": "value (the number exactly as he says it), prefix/suffix, label",
    "screenshot": "source (the real product or page to capture), highlight (what to reveal on it)",
    "nametag": "name, role",
    "logo": "source (the brand/product name)",
    "quote": "text (a short quote he reads), name (who said it)",
}


class _Graphic(BaseModel):
    beat_id: str
    template: Literal["title", "list", "stat", "screenshot", "nametag", "logo", "quote"]
    params: GraphicParams
    beat_offset: float
    duration: float
    motion: str  # enter / internal motion / exit, in a few words


class _Graphics(BaseModel):
    graphics: list[_Graphic]


class _ScriptLine(BaseModel):
    ar: str
    en: str
    onscreen_ar: str | None = None
    onscreen_en: str | None = None


class _Script(BaseModel):
    title: str
    hook_formula: Literal["shock_number", "contrarian", "curiosity_gap", "direct_question", "news_flash"]
    lines: list[_ScriptLine]
    caption: str


# ── Script lengths (reel-script-arabic house style) ─────────────────────────
_LENGTHS = {
    "15-30s": ("Quick alert", "35-60"),
    "30-60s": ("Standard", "70-130"),
    "60-90s": ("Deep dive", "140-200"),
}


def _length_band(length: str | int | float | None) -> str:
    if isinstance(length, (int, float)):
        return "15-30s" if length <= 30 else "30-60s" if length <= 60 else "60-90s"
    text = str(length or "").strip().lower()
    if text in _LENGTHS:
        return text
    nums = [int(n) for n in re.findall(r"\d+", text)]
    if nums:
        return _length_band(max(nums))
    return {"short": "15-30s", "long": "60-90s"}.get(text, "30-60s")


ARABIC_STYLE = """Arabic house style (reel-script-arabic):
- Modern Standard Arabic, "Fusha-light", confident news-anchor delivery. No dialect, no slang.
- Line 1 is the HOOK (first 3 seconds) using ONE formula: shock/number ("٩٥٪ من الناس لا يعرفون أن…"), \
contrarian ("توقّف عن استخدام ChatGPT بهذه الطريقة."), curiosity gap ("هذه الأداة ستغيّر طريقة عملك… ولن تصدّق \
أنها مجانية."), direct question ("هل تعلم أن الذكاء الاصطناعي يستطيع الآن…؟"), news flash ("خبر عاجل من عالم \
الذكاء الاصطناعي:…"). Specific, never generic.
- Each line is ONE breath: max 12-15 words. Active voice ("أطلقت Google أداة جديدة", not "تم إطلاق أداة").
- No filler ("في الحقيقة", "بشكل عام", "كما تعلمون"). Accessible words, not heavy classical vocabulary.
- Tool names, model names and numbers stay in English/digits ("ChatGPT-5", "10 دقائق", "$20").
- Body for news: what happened -> why it matters to the viewer -> what to do next. For tutorials: the \
viewer's problem -> the tool -> 2-3 quick steps or 3 key features -> the payoff.
- The LAST line is the CTA: one specific action (save, comment a word, share), never "تابعونا". \
End with confidence, never hedge or apologise.
- On-screen text is a keyword that reinforces the line (a tool name, a number, "1️⃣ القوالب"), never a sentence; \
only on lines that benefit.

Example (news flash, 20 s):
خبر عاجل: OpenAI أطلقت للتو نموذجًا أقوى من GPT-4 بثلاث مرات.
الاسم الجديد: GPT-5. [GPT-5]
أسرع، أدق، ويفهم الصور والصوت في الوقت نفسه.
السعر بقي $20 شهريًا، لكن القدرات تضاعفت. [$20]
هذا يعني أن أدوات الذكاء الاصطناعي التي تستخدمها اليوم… ستتغيّر غدًا.
احفظ المنشور لتعود إليه عند إطلاق التحديث في بلدك."""


# ── Tasks ────────────────────────────────────────────────────────────────────
class ClaudeTasks:
    """Claude-backed implementation of ``PlannerLLM`` (+ script drafting, proofreading)."""

    def __init__(self, *, effort: str | None = None):
        self.effort = effort or SETTINGS.llm.effort

    def _ask(self, prompt: str, schema, *, effort: str | None = None):
        return claude.ask_json(prompt, schema, system=SYSTEM, effort=effort or self.effort)

    def _indexed(self, prompt: str, n: int, what: str) -> list[str]:
        """Ask for one output per input index; retry once when the set of indices is wrong."""
        for attempt in range(2):
            out = self._ask(prompt, _IndexedList)
            by_i = {row.i: row.text for row in out.lines}
            if set(by_i) == set(range(n)) and len(out.lines) == n:
                return [_fix(by_i[i]) for i in range(n)]
            prompt += (f"\n\nYour previous answer had {len(out.lines)} items with indices {sorted(by_i)}; "
                       f"return exactly {n} items, i = 0..{n - 1}.")
        raise ClaudeError(f"{what}: Claude did not return exactly {n} lines")

    # PlannerLLM ---------------------------------------------------------------
    def translate_lines(self, lines: list[str], *, source_lang: str) -> list[str]:
        """English caption text for each line, same count and order."""
        if not lines:
            return []
        src = {"ar": "Arabic", "en": "English"}.get(source_lang, source_lang)
        prompt = f"""Translate these {len(lines)} {src} lines from Jweed's reel script into English captions.

Rules:
- Natural spoken English, as he would say it on camera: short, clear, confident. Not literal word-for-word.
- Exactly one English line per input line, same index, same order. Never merge, split, drop or add lines.
- Keep every name, brand, product, model and number exactly ("Claude", "Anthropic", "GPT-5", "$20", "95%").
- His name is "Jweed" (جويد -> Jweed).
- Text inside [brackets] is on-screen text, not speech: leave it out of the caption.
- English only: no Arabic script, no transliteration.

Lines (JSON):
{_numbered(lines)}

Return {{"lines": [{{"i": <index>, "text": "<English caption>"}}, ...]}}."""
        out = self._indexed(prompt, len(lines), "translate_lines")
        if any(is_arabic(t) for t in out):
            raise ClaudeError("translate_lines: Claude returned Arabic text for an English caption")
        return out

    def plan_unscripted(self, blocks: list[dict]) -> list[dict]:
        """Group repeated attempts, keep the LAST clean one, order the beats (hook first)."""
        if not blocks:
            return []
        prompt = f"""Jweed recorded a reel without a script. Below are his speech blocks in RECORDING order \
(take by take, then by time), each isolate-transcribed (the transcript may contain recognition errors).

He repeats a line until it is right, so several blocks are often attempts of the same thought.
Build the edit:
1. Group blocks that are attempts of the same thought.
2. For each thought keep the LAST clean attempt (complete, no stumble, no restart, no trailing-off). \
"Last repeat wins": only pick an earlier attempt when every later one is broken. A thought may need \
2-3 consecutive blocks of the same take when he paused mid-sentence; never mix takes in one beat.
3. Drop false starts, off-topic chatter, "again", counting, and anything he abandoned.
4. Order the beats for a reel: the strongest hook first (the news / the surprising claim), then the body \
in logical order, then his call to action last. Keep his own order when it already works.
5. caption_en: a natural English caption of exactly what the kept block(s) say (translate Arabic; \
fix recognition errors only when obvious; "Jweed" spelling). Do not add claims he did not make.
6. reason: one short line, e.g. "attempt 3 of 3; earlier ones restarted".

Each block_id may be used at most once. Only use block_ids from the list.

Blocks (JSON):
{json.dumps(blocks, ensure_ascii=False, indent=1)}

Return {{"beats": [{{"block_ids": [...], "caption_en": "...", "reason": "..."}}, ...]}} in reel order."""
        out = self._ask(prompt, _UnscriptedPlan, effort="high")
        known = {b.get("block_id") for b in blocks}
        used: set[str] = set()
        beats = []
        for beat in out.beats:
            ids = [i for i in beat.block_ids if i in known and i not in used]
            if not ids:
                continue
            used.update(ids)
            beats.append({"block_ids": ids, "caption_en": _fix(beat.caption_en), "reason": beat.reason.strip()})
        return beats

    def suggest_graphics(self, beats: list[dict]) -> list[dict]:
        """Sparse motion-graphic suggestions; only beats that benefit, only claims he made."""
        rows = [b for b in beats if b.get("beat_id")]
        if not rows:
            return []
        cap = max(1, math.ceil(len(rows) / 2))
        templates = "\n".join(f"- {name}: {desc}" for name, desc in GRAPHIC_PARAMS.items())
        prompt = f"""Suggest motion graphics (HyperFrames inserts) for Jweed's reel. Beats with their English captions:
{json.dumps(rows, ensure_ascii=False, indent=1)}

Rules (the creator's, non-negotiable):
- SPARSE: only beats where a graphic clearly helps (a product/company he names, a number, a list, a quote). \
At most {cap} graphics; zero is fine. Skip beats where has_graphic is true.
- Graphics never cover his face; the app places them in free bands, so do not describe positions.
- No camera moves on his face. ALL motion belongs to the graphics, and EVERY graphic moves: an enter \
(slow scale-up + fade, never a bounce), internal motion (rows reveal, a highlight travels, a number counts \
up) and an explicit exit. A screenshot is BUILT on screen, never a still. Describe it in "motion".
- Only claims he actually says in that beat's caption. A stat's value must be a number that appears in the \
caption. Never invent numbers, quotes or features.
- screenshot/logo must name the real product or page (it is fetched from the real source, not generated).
- beat_offset: seconds after the beat starts (0.1-1.5); duration: 1.5-6 s and short enough to end inside the beat.

Templates and their params (unused params null):
{templates}

Return {{"graphics": [{{"beat_id", "template", "params", "beat_offset", "duration", "motion"}}, ...]}}."""
        out = self._ask(prompt, _Graphics)
        by_id = {b["beat_id"]: b for b in rows}
        picked: list[dict] = []
        seen: set[str] = set()
        for g in out.graphics:
            beat = by_id.get(g.beat_id)
            if beat is None or beat.get("has_graphic") or g.beat_id in seen or not g.motion.strip():
                continue
            params = {k: (_fix(v) if isinstance(v, str) else [_fix(x) for x in v] if isinstance(v, list) else v)
                      for k, v in g.params.model_dump().items() if v not in (None, "", [])}
            if not _params_ok(g.template, params, beat.get("caption_en", "")):
                continue
            params["motion"] = g.motion.strip()
            seen.add(g.beat_id)
            picked.append({"beat_id": g.beat_id, "template": g.template, "params": params,
                           "beat_offset": round(min(max(g.beat_offset, 0.0), 1.5), 2),
                           "duration": round(min(max(g.duration, 1.5), 6.0), 2)})
        return picked[:cap]

    # Script + captions ----------------------------------------------------------
    def draft_script(self, topic: str, length: str | int | None = "30-60s") -> dict:
        """Line-aligned Arabic script in the house style + the English mirror of each line.

        Returns {"ar", "en", "title", "caption"}; ar/en are newline-joined, one spoken line each,
        with on-screen text in [brackets] (what ``pipeline.parse_script`` expects).
        """
        band = _length_band(length)
        kind, words = _LENGTHS[band]
        prompt = f"""Write a ready-to-record reel script for Jweed about:
{topic.strip()}

Length: {band} ({kind}), {words} Arabic words in total.

{ARABIC_STYLE}

Output, line by line:
- ar: the spoken Arabic line (no brackets in it).
- en: the English mirror of THAT line, natural spoken English (it becomes his caption), same meaning, \
same names and numbers. One en per ar, never merged or split.
- onscreen_ar / onscreen_en: optional on-screen keyword for that line (same idea in both languages; \
names/numbers stay in English), else null.
- Facts: only what is true and well known about the topic as given; if unsure of a number, do not use one.
- title: 4-8 Arabic words, curiosity-driven. caption: the Instagram caption in Arabic opening with a \
SECOND hook (not the video hook), 2-4 lines, then 15-20 hashtags mixing Arabic, English and niche tags."""
        out = self._ask(prompt, _Script, effort="high")
        lines = [ln for ln in out.lines if ln.ar.strip() and ln.en.strip()]
        if not lines:
            raise ClaudeError("draft_script: Claude returned no lines")
        if any(not is_arabic(ln.ar) or is_arabic(ln.en) for ln in lines):
            raise ClaudeError("draft_script: Arabic/English lines are swapped or mixed")

        def join(text: str, onscreen: str | None) -> str:
            text = _fix(text.replace("[", "(").replace("]", ")"))
            tag = _fix(onscreen or "").replace("[", "").replace("]", "")
            return f"{text} [{tag}]" if tag else text

        return {"ar": "\n".join(join(ln.ar, ln.onscreen_ar) for ln in lines),
                "en": "\n".join(join(ln.en, ln.onscreen_en) for ln in lines),
                "title": out.title.strip(), "caption": out.caption.strip()}

    def proofread_captions(self, captions: list[str]) -> list[str]:
        """Fix spelling/grammar/names in English captions; same count, same meaning."""
        if not captions:
            return []
        prompt = f"""Proofread these {len(captions)} English captions from Jweed's reel.

Fix only: spelling, grammar, punctuation, capitalisation, and names (always "Jweed"; brands and models \
exactly: Claude, Anthropic, ChatGPT, OpenAI, GPT-5 ...). Keep his wording, meaning and length; captions are \
word-synced to his speech, so do not rephrase, reorder, shorten or add content. If a caption is not English, \
translate it to English. Return every caption, even unchanged ones.

Captions (JSON):
{_numbered(captions)}

Return {{"lines": [{{"i": <index>, "text": "<caption>"}}, ...]}}."""
        return self._indexed(prompt, len(captions), "proofread_captions")


def _params_ok(template: str, params: dict, caption: str) -> bool:
    """Drop malformed suggestions and any stat whose number he never said."""
    if template == "title":
        return bool(params.get("text"))
    if template == "list":
        return len(params.get("items") or []) >= 2
    if template == "stat":
        value = str(params.get("value") or "")
        digits = re.sub(r"[^\d.]", "", value).strip(".")
        return bool(digits) and digits in re.sub(r"(?<=\d),(?=\d)", "", caption)
    if template in ("screenshot", "logo"):
        return bool(params.get("source"))
    if template == "nametag":
        return bool(params.get("name"))
    if template == "quote":
        return bool(params.get("text"))
    return False
