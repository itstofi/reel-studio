"""Align isolate-transcribed blocks to the written script — the LAST clean repeat wins.

He re-records a line until it is right, then moves on, so the keeper for each line is
the last attempt that says the whole line without stumbling. Every script line must
appear, in script order: a missing line is reported, never silently skipped.

Whisper's word times only identify WHICH speech is an attempt. The cut points come
from the speech map (see attempt_segment): whisper stretches a word's end across a
mic dropout, so its boundaries drift against a pause-trimmed edit.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from ..media.speech import run_containing
from ..models import (Alignment, Attempt, BlockTranscript, Lang, LineAlignment, Script,
                      ScriptLine, SpeechMap, Take)
from ..util.textnorm import strip_brackets, words as norm_words

WORD_MATCH = 0.75          # fuzzy word equality (rapidfuzz ratio / 100)
MIN_COVERAGE = 0.5         # windows matching less of the line are not attempts
CLEAN_COVERAGE = 0.85      # a clean attempt says (almost) the whole line ...
CLEAN_INSERT_RATIO = 0.15  # ... and adds few extra words (stutters, restarts)
ANCHOR_WORDS = 3           # an attempt may start at any of the line's first 3 words

# DP scores: a fuzzy match earns its similarity; misses cost.
_SUB, _DEL, _INS = -0.6, -0.6, -0.4


@dataclass(frozen=True)
class StreamWord:
    norm: str
    raw: str
    start: float
    end: float
    take_id: str
    block_id: str


def line_text(line: ScriptLine, lang: Lang) -> str:
    raw = (line.ar if lang == "ar" else line.en) or ""
    return strip_brackets(raw)[0]


def build_stream(transcripts: list[BlockTranscript], takes: list[Take]) -> list[StreamWord]:
    """Every transcribed word in recording order (take order, then time)."""
    order = {t.id: t.order for t in takes}
    out: list[StreamWord] = []
    for tr in transcripts:
        for w in tr.words:
            for tok in norm_words(w.text):
                out.append(StreamWord(tok, w.text.strip(), w.start, w.end, tr.take_id, tr.block_id))
    out.sort(key=lambda s: (order.get(s.take_id, 0), s.start))  # stable: split tokens keep order
    return out


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b) / 100.0


@dataclass
class _Match:
    pairs: list[tuple[int, int, float]]  # (line index, window index, similarity)
    inserted: int                         # extra window words inside the matched region


def _align(line: list[str], win: list[str]) -> _Match:
    """Semi-global alignment: consumes the whole line, any contiguous region of the
    window (free window prefix/suffix)."""
    n, m = len(line), len(win)
    neg = -1e9
    dp = [[0.0] * (m + 1)] + [[neg] * (m + 1) for _ in range(n)]
    bt = [[0] * (m + 1) for _ in range(n + 1)]  # 1 diag, 2 skip line word, 3 extra window word
    sims = [[_sim(line[i], win[j]) for j in range(m)] for i in range(n)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + _DEL
        bt[i][0] = 2
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            s = sims[i - 1][j - 1]
            diag = prev[j - 1] + (s if s >= WORD_MATCH else _SUB)
            up = prev[j] + _DEL
            left = row[j - 1] + _INS
            if diag >= up and diag >= left:
                row[j], bt[i][j] = diag, 1
            elif up >= left:
                row[j], bt[i][j] = up, 2
            else:
                row[j], bt[i][j] = left, 3
    j_end = max(range(m + 1), key=lambda j: dp[n][j])
    i, j = n, j_end
    pairs: list[tuple[int, int, float]] = []
    extra_cols: list[int] = []
    while i > 0:
        move = bt[i][j]
        if move == 1:
            s = sims[i - 1][j - 1]
            if s >= WORD_MATCH:
                pairs.append((i - 1, j - 1, s))
            i, j = i - 1, j - 1
        elif move == 2:
            i -= 1
        else:
            extra_cols.append(j - 1)
            j -= 1
    pairs.reverse()
    if not pairs:
        return _Match([], 0)
    lo, hi = pairs[0][1], pairs[-1][1]
    return _Match(pairs, sum(1 for c in extra_cols if lo <= c <= hi))


@dataclass
class _Candidate:
    line_idx: int
    lo: int            # stream index of first matched word
    hi: int            # stream index of last matched word
    coverage: float
    inserted: int
    score: float


def _candidates(li: int, line: list[str], stream: list[StreamWord]) -> list[_Candidate]:
    n = len(line)
    if n == 0:
        return []
    width = int(n * 1.6) + 4
    anchors = set(line[:ANCHOR_WORDS])
    line_set = set(line)
    found: list[_Candidate] = []
    for k, sw in enumerate(stream):
        if not any(_sim(sw.norm, a) >= WORD_MATCH for a in anchors):
            continue
        end = k
        while end < len(stream) and end - k < width and stream[end].take_id == sw.take_id:
            end += 1
        window = [w.norm for w in stream[k:end]]
        # cheap prefilter before the DP
        if len(line_set.intersection(window)) < max(1, int(0.3 * n)) and n > 2:
            continue
        match = _align(line, window)
        if not match.pairs:
            continue
        coverage = len(match.pairs) / n
        if coverage < MIN_COVERAGE:
            continue
        lo, hi = k + match.pairs[0][1], k + match.pairs[-1][1]
        region = hi - lo + 1
        score = sum(p[2] for p in match.pairs) / max(n, region)
        found.append(_Candidate(li, lo, hi, coverage, match.inserted, score))
    return found


def _overlaps(a: _Candidate, b: _Candidate) -> int:
    return max(0, min(a.hi, b.hi) - max(a.lo, b.lo) + 1)


def _suppress(cands: list[_Candidate]) -> list[_Candidate]:
    """Non-max suppression: a stream region belongs to one attempt of one line."""
    kept: list[_Candidate] = []
    for c in sorted(cands, key=lambda c: (-c.score, -c.coverage, c.lo)):
        clash = False
        for k in kept:
            ov = _overlaps(c, k)
            if not ov:
                continue
            smaller = min(c.hi - c.lo + 1, k.hi - k.lo + 1)
            if k.line_idx == c.line_idx or ov > 0.5 * smaller:
                clash = True
                break
        if not clash:
            kept.append(c)
    return kept


def _is_clean(c: _Candidate, n: int) -> bool:
    return c.coverage >= CLEAN_COVERAGE and c.inserted <= max(1, int(CLEAN_INSERT_RATIO * n))


def align_script(script: Script, lang: Lang, transcripts: list[BlockTranscript],
                 takes: list[Take]) -> Alignment:
    stream = build_stream(transcripts, takes)
    lines = [norm_words(line_text(ln, lang)) for ln in script.lines]
    cands: list[_Candidate] = []
    for li, words in enumerate(lines):
        cands.extend(_candidates(li, words, stream))
    kept = _suppress(cands)

    used_blocks: set[str] = set()
    out: list[LineAlignment] = []
    for li, line in enumerate(script.lines):
        n = len(lines[li])
        mine = sorted((c for c in kept if c.line_idx == li), key=lambda c: c.lo)
        attempts: list[Attempt] = []
        for c in mine:
            seg = stream[c.lo:c.hi + 1]
            first, last = seg[0], seg[-1]
            raw: list[str] = []
            for w in seg:
                if not raw or raw[-1] != w.raw:
                    raw.append(w.raw)
            attempts.append(Attempt(
                line_ids=[line.id], take_id=first.take_id, start=first.start, end=last.end,
                first_mid=(first.start + first.end) / 2, last_mid=(last.start + last.end) / 2,
                block_ids=sorted({w.block_id for w in seg}), coverage=round(c.coverage, 3),
                inserted=c.inserted, score=round(c.score, 3), clean=_is_clean(c, n),
                text=" ".join(raw)))
        chosen, status = None, "missing"
        clean_idx = [i for i, a in enumerate(attempts) if a.clean]
        if clean_idx:
            chosen, status = clean_idx[-1], "ok"          # LAST clean repeat wins
        elif attempts:
            best = max(a.coverage for a in attempts)
            chosen = max(i for i, a in enumerate(attempts) if a.coverage == best)
            status = "weak"
        if chosen is not None:
            used_blocks.update(attempts[chosen].block_ids)
        out.append(LineAlignment(line_id=line.id, attempts=attempts, chosen=chosen, status=status))

    all_blocks = {tr.block_id for tr in transcripts if tr.words}
    return Alignment(lang=lang, lines=out, unmatched_block_ids=sorted(all_blocks - used_blocks))


WORD_CUT_PAD = 0.12  # pad for the rare cut that must use word times (no pause available)


def attempt_segment(attempt: Attempt, sm: SpeechMap, pad_s: float,
                    take_words: list[tuple[float, float]] | None = None,
                    neighbour_margin: float = 0.02) -> tuple[float, float, list[str]]:
    """Cut points for an attempt from the SPEECH MAP, padded by pad_s, never padded into a
    neighbouring speech run that is not part of the attempt. Returns (start, end, warnings).

    ``take_words`` = (start, end) of every transcribed word in the take. When other words
    share the attempt's first speech run (a restart with no pause: "imagine you needed —
    imagine you needed a team…"), the run edge would keep the false start, so the head is
    cut on word times instead (warned). Same for trailing words in the last run.
    """
    runs = sm.runs
    warnings: list[str] = []
    if not runs:
        return max(0.0, attempt.start - pad_s), min(sm.duration, attempt.end + pad_s), ["no speech map runs"]
    probe_a = attempt.first_mid if attempt.first_mid is not None else attempt.start
    probe_b = attempt.last_mid if attempt.last_mid is not None else attempt.end
    i = run_containing(runs, probe_a, tol=0.05)
    if i is None:
        i = next((k for k, r in enumerate(runs) if r.end > probe_a), len(runs) - 1)
    j = run_containing(runs, probe_b, tol=0.05)
    if j is None:
        j = max((k for k, r in enumerate(runs) if r.start < probe_b), default=i)
    j = max(i, j)
    start = runs[i].start - pad_s
    end = runs[j].end + pad_s
    if i > 0:
        start = max(start, runs[i - 1].end + neighbour_margin)
    if j + 1 < len(runs):
        end = min(end, runs[j + 1].start - neighbour_margin)
    start, end = max(0.0, start), min(sm.duration, end)
    head_word_cut = tail_word_cut = False
    if take_words:
        before = [w for w in take_words
                  if w[0] >= runs[i].start - 0.05 and w[0] < attempt.start - 0.10]
        if before:
            prev_end = min(max(w[1] for w in before), attempt.start)
            start = max(start, min(attempt.start - 0.03, max(attempt.start - WORD_CUT_PAD,
                                                              (prev_end + attempt.start) / 2)))
            head_word_cut = True
            warnings.append(f"no pause before the keeper at {attempt.start:.2f}s: head cut on word times")
        after = [w for w in take_words if w[0] > attempt.end + 0.05 and w[0] <= runs[j].end]
        if after:
            next_start = min(w[0] for w in after)
            end = min(end, max(attempt.end + 0.03, min(attempt.end + WORD_CUT_PAD,
                                                       (attempt.end + next_start) / 2)))
            tail_word_cut = True
            warnings.append(f"speech continues after the keeper at {attempt.end:.2f}s: tail cut on word times")
    if not head_word_cut and runs[i].start - start < 0.10:
        warnings.append(f"tight head pad {runs[i].start - start:.2f}s at {runs[i].start:.2f}s")
    if not tail_word_cut and end - runs[j].end < 0.10:
        warnings.append(f"tight tail pad {end - runs[j].end:.2f}s at {runs[j].end:.2f}s")
    return round(start, 4), round(end, 4), warnings
