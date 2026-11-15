"""Pronunciation QA for generated voice: hear every line back, regenerate words that come out wrong.

Loop per line: synthesize (seed k, respelled text) -> whisper transcribes the clip -> the heard words are
aligned to the SCRIPT words (what the viewer reads in the captions). Any script word missing, misheard
(e.g. "AI" heard as "I"/"eye") or repeated ("look look") is a problem; the line is regenerated with a new
seed and, for flagged words that have one, a phonetic respelling. The take with the fewest problems wins.
Captions always keep the real spelling; respellings only change what the TTS reads.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from ..config import SETTINGS
from ..util.textnorm import words as norm_words

_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
          "sixteen seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}


_IRAQI = str.maketrans({"گ": "ق", "چ": "ك", "ڤ": "ف", "پ": "ب", "ژ": "ز"})   # dialect letters -> whisper's spelling


def _prep(raw: str) -> str:
    raw = raw.translate(_IRAQI)
    t = re.sub(r"(?<=\d),(?=\d{3})", "", raw)          # 8,000 -> 8000
    return re.sub(r"(?<=\d)[.:](?=\d)", "p", t)          # 1.5 / 3:54 -> one token on both sides


# ── Arabic: number words -> digits, brand names in either alphabet, prefixes و/ب/ال/ل ────────────────
def _ar_number_table() -> dict[str, tuple[int, str]]:
    from ..util.textnorm import normalize
    t: dict[str, tuple[int, str]] = {}
    units = {"واحد": 1, "وحده": 1, "اثنين": 2, "اثنان": 2, "ثنين": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4,
             "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8, "ثمانيه": 8, "ثمن": 8,
             "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10}
    tens = {"عشرين": 20, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60, "سبعين": 70, "ثمانين": 80,
            "تسعين": 90}
    for w, v in {**units, **tens}.items():
        t[normalize(w)] = (v, "add")
    for w in ("مية", "مئة", "مائة", "ميه"):
        t[normalize(w)] = (100, "add")
    for w in ("ميتين", "مئتين", "مائتين"):
        t[normalize(w)] = (200, "add")
    for stem, v in (("ثلاث", 3), ("اربع", 4), ("خمس", 5), ("ست", 6), ("سبع", 7), ("ثمان", 8), ("ثمن", 8), ("تسع", 9)):
        for h in ("مية", "مئة", "مائة"):
            t[normalize(stem + h)] = (v * 100, "add")
    for w in ("الف", "آلاف", "الاف"):
        t[normalize(w)] = (1000, "mult")
    t[normalize("ألفين")] = (2000, "add")
    return t


_AR_NUM: dict[str, tuple[int, str]] | None = None
_AR_PREFIXES = ("وبال", "وال", "بال", "لل", "وب", "ال", "و", "ب", "ل")


def _ar_strip(tok: str, table, fuzzy: int = 0) -> str | None:
    """Table key for `tok` (with or without a و/ب/ال prefix); `fuzzy` > 0 also accepts close spellings
    (brand names: whisper writes "تمز" for "تيمز")."""
    cands = [tok] + [tok[len(pre):] for pre in _AR_PREFIXES if tok.startswith(pre) and len(tok) > len(pre) + 1]
    for c in cands:
        if c in table:
            return c
    if fuzzy:
        for c in cands:
            if len(c) >= 3:
                best = max(table, key=lambda k: fuzz.ratio(c, k), default=None)
                if best is not None and fuzz.ratio(c, best) >= fuzzy:
                    return best
    return None


def _canon_ar(toks: list[list], stops: set[int] = frozenset(), settings=SETTINGS) -> list[list]:
    """Arabic number words -> one digit token; brand names (Arabic spelling, any prefix) -> Latin."""
    global _AR_NUM
    from ..util.textnorm import normalize
    if _AR_NUM is None:
        _AR_NUM = _ar_number_table()
    aliases = {normalize(a): b for a, b in settings.pronounce.aliases}
    out, k = [], 0
    while k < len(toks):
        tk, i = toks[k][0], toks[k][1]
        num = _ar_strip(tk, _AR_NUM)
        starts_digit = tk.isdigit() and k + 1 < len(toks) and _ar_strip(toks[k + 1][0], _AR_NUM) \
            and _AR_NUM[_ar_strip(toks[k + 1][0], _AR_NUM)][1] == "mult"
        if num or starts_digit:
            total = cur = 0
            while k < len(toks):
                w = toks[k][0]
                n = _ar_strip(w, _AR_NUM)
                if n is None and not (w.isdigit() and cur == 0 and total == 0):
                    break
                if n is not None and _AR_NUM[n][0] >= 1000 and _AR_NUM[n][1] == "add" and (total or cur):
                    break                       # "ألفين" after a finished number starts a new one
                if n is None:
                    cur += int(w)
                else:
                    v, kind = _AR_NUM[n]
                    if kind == "mult":
                        total += (cur or 1) * 1000
                        cur = 0
                    else:
                        cur += v
                k += 1
                if toks[k - 1][1] in stops:     # punctuation after the raw word ends the number
                    break
            out.append([str(total + cur), i, False])
            continue
        a = _ar_strip(tk, aliases, fuzzy=80)
        out.append([aliases[a] if a else tk, i, toks[k][2]])
        k += 1
    return out


def _alias(tok: str, settings=SETTINGS) -> str:
    """Brand-name canonical form of a (possibly prefixed) Arabic token; the token itself otherwise."""
    from ..util.textnorm import normalize
    aliases = {normalize(a): b for a, b in settings.pronounce.aliases}
    a = _ar_strip(tok, aliases, fuzzy=80)
    return aliases[a] if a else tok


def tokens(raws: list[str]) -> list[tuple[str, int]]:
    """Comparable tokens with the index of the raw word they came from.
    Spelled-out letters are joined (F. A. A. -> faa), number words become digits (twenty one -> 21)."""
    out: list[list] = []   # [token, raw index, is_letter_run]
    dollar = None          # "$50 billion" is heard/said as "50 billion dollars"
    for i, r in enumerate(raws):
        if dollar is not None and r.lower().strip(".,!?;:") not in ("thousand", "million", "billion", "trillion"):
            out.append(["dollars", dollar, False])
            dollar = None
        if r.lstrip("\"'(").startswith("$"):
            dollar = i
        for tk in norm_words(_prep(r)):
            letter = len(tk) == 1 and tk.isalpha()
            if letter and out and out[-1][2]:      # previous token is a run of spelled letters
                out[-1][0] += tk
            else:
                out.append([tk, i, letter])
    if dollar is not None:
        out.append(["dollars", dollar, False])
    if any("\u0600" <= c <= "\u06ff" for t in out for c in t[0]):
        out = _canon_ar(out, {i for i, r in enumerate(raws) if re.search(r"[،,.!?؟:;]$", r)})
        # bare clitics ("و مايكروسوفت", "وبـ 2023"): whisper writes them apart or joined at random
        out = [t for t in out if t[0] not in ("و", "ب", "ل", "ف", "وب", "ول", "فب")]
    merged: list[tuple[str, int]] = []
    k = 0
    while k < len(out):
        tk, i = out[k][0], out[k][1]
        if tk in _TENS:
            val = _TENS[tk]
            if k + 1 < len(out) and out[k + 1][0] in _UNITS[1:10]:
                val += _UNITS.index(out[k + 1][0])
                k += 1
            tk = str(val)
        elif tk in _UNITS:
            tk = str(_UNITS.index(tk))
        merged.append((tk, i))
        k += 1
    return merged


def _is_ar(tok: str) -> bool:
    return any("\u0600" <= c <= "\u06ff" for c in tok)


def _same(a: str, b: str, fuzz_min: int) -> bool:
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or len(a) <= 2 or len(b) <= 2:
        return False
    return fuzz.ratio(a, b) >= fuzz_min


@dataclass
class Problem:
    kind: str                 # missing | misheard | repeat | extra
    word: str                 # script word (raw) or heard word for repeat/extra
    heard: str = ""
    at: float | None = None   # seconds in the clip / file (heard side)

    def __str__(self) -> str:
        t = f" @{self.at:.1f}s" if self.at is not None else ""
        return {"missing": f'"{self.word}" not heard{t}', "misheard": f'"{self.word}" heard as "{self.heard}"{t}',
                "repeat": f'"{self.word}" said twice{t}', "extra": f'extra speech "{self.heard}"{t}'}[self.kind]


@dataclass
class Check:
    heard_text: str
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(script_text: str, heard_words, settings=SETTINGS) -> Check:
    """Align heard words (objects with .text/.start) to the script text; list what came out wrong."""
    spec = settings.pronounce
    raws = script_text.split()
    exp = tokens(raws)
    heard_raw, keep = [], []
    for w in heard_words:   # whisper splits "10,000" into "10" + ",000": glue the pieces back
        t = w.text.strip()
        if heard_raw and re.match(r"^[,.]\d", t) and re.search(r"\d$", heard_raw[-1]):
            heard_raw[-1] += t
            continue
        heard_raw.append(t)
        keep.append(w)
    heard_words = keep
    got = tokens(heard_raw)
    # compounds written differently: script "cyberattack" vs heard "cyber attack" (and the reverse)
    exp_set = {t for t, _ in exp}
    joined, k = [], 0
    while k < len(got):
        pair = _alias(got[k][0] + got[k + 1][0], settings) if k + 1 < len(got) else None
        if pair and pair in exp_set and got[k][0] not in exp_set:
            joined.append((pair, got[k][1]))
            k += 2
            continue
        joined.append(got[k])
        k += 1
    got = joined
    got_set = {t for t, _ in got}
    merged_exp, k = [], 0
    while k < len(exp):
        if k + 1 < len(exp) and exp[k][0] + exp[k + 1][0] in got_set and exp[k][0] not in got_set:
            merged_exp.append((exp[k][0] + exp[k + 1][0], exp[k][1]))
            k += 2
            continue
        merged_exp.append(exp[k])
        k += 1
    exp = merged_exp
    starts = [getattr(w, "start", None) for w in heard_words]
    n, m = len(exp), len(got)
    # edit-distance DP with fuzzy word equality
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = i
    for j in range(1, m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j - 1] + (0 if _same(exp[i - 1][0], got[j - 1][0], spec.fuzz_min) else 1),
                          D[i - 1][j] + 1, D[i][j - 1] + 1)
    ops = []
    i, j = n, m
    while i or j:
        if i and j and D[i][j] == D[i - 1][j - 1] + (0 if _same(exp[i - 1][0], got[j - 1][0], spec.fuzz_min) else 1):
            ops.append(("eq" if _same(exp[i - 1][0], got[j - 1][0], spec.fuzz_min) else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and D[i][j] == D[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    ops.reverse()
    probs: list[Problem] = []
    flagged: set[int] = set()
    run: list[int] = []
    prev_heard = None

    def flush(edge: bool = False):
        # TTS invents sounds at the edges of a line ("Sad, the communication..."): any extra word before the
        # first or after the last script word is a defect; mid-line, only runs (single words = ASR noise)
        if run and (edge or len(run) > spec.extra_run_max):
            probs.append(Problem("extra", "", " ".join(got[k][0] for k in run), starts[got[run[0]][1]]))
        run.clear()

    first_ref = next((k for k, o in enumerate(ops) if o[0] != "ins"), len(ops))

    for n_op, (op, ei, gj) in enumerate(ops):
        if op != "ins" and n_op == first_ref and run:
            flush(edge=True)
        if op == "ins":
            tk = got[gj][0]
            nxt = got[gj + 1][0] if gj + 1 < m else None
            if any(o is not None and _same(tk, o, spec.fuzz_min) for o in (prev_heard, nxt)):
                probs.append(Problem("repeat", heard_raw[got[gj][1]], tk, starts[got[gj][1]]))
            else:
                run.append(gj)
            prev_heard = tk
            continue
        flush()
        dialect_particle = op in ("del", "sub") and _is_ar(exp[ei][0]) and len(exp[ei][0]) <= 3
        if op in ("del", "sub") and exp[ei][1] not in flagged and not dialect_particle:
            flagged.add(exp[ei][1])
            if op == "del":
                probs.append(Problem("missing", raws[exp[ei][1]]))
            else:
                probs.append(Problem("misheard", raws[exp[ei][1]], got[gj][0], starts[got[gj][1]]))
        if gj is not None:
            prev_heard = got[gj][0]
    flush(edge=True)
    return Check(heard_text=" ".join(heard_raw), problems=probs)


def respell(text: str, level: int = 0, fix: set[str] | None = None, settings=SETTINGS) -> str:
    """What the TTS reads. level 0: house respellings; level >= 2: alternates for words flagged last time."""
    base, alt = dict(settings.pronounce.respell), dict(settings.pronounce.respell_alt)
    out = []
    for raw in text.split():
        m = re.match(r"^([\"'(]*)(.*?)([\"'),.!?;:]*)$", raw)
        pre, core, post = m.groups() if m else ("", raw, "")
        key = core.lower()
        if level >= 2 and fix and key in alt and key in fix:
            core = alt[key]
        elif key in base:
            core = base[key]
            if core.endswith(".") and post.startswith("."):
                post = post[1:]
        out.append(pre + core + post)
    return " ".join(out)


def synth_checked(tts, text: str, *, lang: str, voice: str, out: Path, transcriber, say: str | None = None,
                  project=None, store=None, settings=SETTINGS, log=None):
    """Synthesize `text` until whisper hears every script word right (or tries run out).
    Returns (wav path, heard words of the chosen take, report dict)."""
    from ..asr.transcribe import transcribe_array
    from ..media.audioio import SR, read_audio
    spec = settings.pronounce
    tries, best = [], None
    fix: set[str] = set()
    for k in range(spec.max_tries):
        seed = 7 + 1009 * k
        spoken = say if (say and k == 0) else respell(say or text, level=k, fix=fix, settings=settings)
        cand = out.with_name(f"{out.stem}.t{k + 1}.wav")
        kw = {"seed": seed} if getattr(tts, "name", "") != "say" else {}
        tts.synthesize(spoken, lang=lang, voice=voice, out=cand, project=project, store=store, **kw)
        heard = transcribe_array(read_audio(cand, sr=SR), language=lang, transcriber=transcriber)
        res = check(text, heard, settings)
        tries.append({"try": k + 1, "seed": seed, "spoken": spoken, "heard": res.heard_text,
                      "problems": [str(p) for p in res.problems]})
        if log:
            log(f'  try {k + 1}: ' + ("clean" if res.ok else "; ".join(str(p) for p in res.problems)))
        if best is None or len(res.problems) < len(best[2].problems):
            best = (cand, heard, res, k + 1)
        if res.ok:
            break
        fix |= {p.word.lower().strip(".,!?;:\"'()") for p in res.problems if p.kind in ("missing", "misheard")}
    cand, heard, res, k = best
    shutil.copyfile(cand, out)
    return out, heard, {"text": text, "chosen_try": k, "ok": res.ok, "problems": [str(p) for p in res.problems],
                        "tries": tries}


def write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
