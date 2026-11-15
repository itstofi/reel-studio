"""Build the beat-by-beat plan the creator approves BEFORE anything renders.

Scripted (the normal case): one beat per script line, in script order, using the LAST
clean attempt of that line; cut points from the speech map; internal dropouts closed
and A/B verified; English caption text from the script (Arabic cut: the English line,
or a Claude translation when the English line is missing). Missing lines stay in the
plan as empty beats with a warning — full script coverage is a rule, never skipped.

Unscripted: Claude groups the blocks into attempts, keeps the last clean one of each
thought, orders them and writes English captions (llm.tasks.plan_unscripted).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from ..asr.fixes import apply_fixes
from ..config import SETTINGS
from ..models import (Alignment, Beat, Block, BlockTranscript, GapIssue, GraphicSpec, Plan,
                      Project, Segment, SpeechMap)
from ..util.proc import ProgressFn
from .align import attempt_segment, line_text
from .gaps import ab_verify, audit_gaps, split_segment


class PlannerLLM(Protocol):
    """What the planner needs from llm.tasks (injected so tests can fake it)."""

    def translate_lines(self, lines: list[str], *, source_lang: str) -> list[str]: ...

    def plan_unscripted(self, blocks: list[dict]) -> list[dict]: ...

    def suggest_graphics(self, beats: list[dict]) -> list[dict]: ...


def _take_words(transcripts: list[BlockTranscript], take_id: str) -> list[tuple[float, float]]:
    return sorted((w.start, w.end) for tr in transcripts if tr.take_id == take_id for w in tr.words)


def _words_in(transcripts: list[BlockTranscript], take_id: str, a: float, b: float):
    return sorted((w for tr in transcripts if tr.take_id == take_id for w in tr.words
                   if a <= (w.start + w.end) / 2 < b), key=lambda w: w.start)


def _segments_for(beat_id: str, line_ids: list[str], take_id: str, span: tuple[float, float],
                  gaps: list[GapIssue], pad_s: float) -> list[Segment]:
    pieces = split_segment(span, gaps, pad_s)
    return [Segment(id=f"{beat_id}s{k + 1}", take_id=take_id, src_in=a, src_out=b, line_ids=line_ids)
            for k, (a, b) in enumerate(pieces)]


def _check_gaps(gaps: list[GapIssue], project: Project, wav: Path, sm: SpeechMap, transcriber,
                verify: bool) -> None:
    """A/B every closure; a word difference means keep the pause (never lose a word)."""
    for g in gaps:
        if not project.settings.close_gaps:
            g.action, g.reason = "keep", g.reason + "; gap closing disabled"
            continue
        if not verify or transcriber is None:
            g.verified = None
            continue
        ok, why = ab_verify(g, wav, sm, language=project.settings.lang, transcriber=transcriber)
        g.verified = ok
        if not ok:
            g.action = "keep"
            g.reason += f"; A/B word difference, pause kept ({why})"


def build_plan(project: Project, alignment: Alignment | None, transcripts: list[BlockTranscript],
               speech_maps: dict[str, SpeechMap], *, blocks: list[Block] | None = None,
               verify_gaps: bool = True, transcriber=None, llm: PlannerLLM | None = None,
               progress: ProgressFn | None = None) -> Plan:
    if project.script and project.script.lines and alignment is not None:
        plan = _scripted(project, alignment, transcripts, speech_maps, verify_gaps, transcriber, llm, progress)
    else:
        if llm is None:
            raise RuntimeError("No script and no Claude access: add a script or set up `claude` / ANTHROPIC_API_KEY")
        plan = _unscripted(project, transcripts, speech_maps, blocks or [], verify_gaps, transcriber, llm, progress)
    if llm is not None and project.settings.graphics:
        _add_suggested_graphics(plan, llm)
    for beat in plan.beats:
        beat.caption_en = apply_fixes(beat.caption_en)
    return plan


def _scripted(project, alignment, transcripts, speech_maps, verify, transcriber, llm, progress) -> Plan:
    lang = project.settings.lang
    pad = SETTINGS.speech.pad_s
    plan = Plan(created_by="aligner")
    lines = project.script.lines
    by_id = {la.line_id: la for la in alignment.lines}
    for idx, line in enumerate(lines):
        if progress:
            progress(idx / max(1, len(lines)), f"planning {line.id}")
        beat = Beat(id=f"B{idx + 1:02d}", line_ids=[line.id])
        if line.onscreen and project.settings.graphics:
            beat.graphics.append(GraphicSpec(id=f"{beat.id}g1", template="title",
                                             params={"text": line.onscreen}, beat_offset=0.2,
                                             duration=2.8))
        la = by_id.get(line.id)
        if la is None or la.chosen is None:
            beat.note = "MISSING — this line was never recorded"
            plan.warnings.append(f"{line.id} is missing from every take (full script coverage is required)")
            plan.beats.append(beat)
            continue
        att = la.attempts[la.chosen]
        sm = speech_maps[att.take_id]
        a, b, warns = attempt_segment(att, sm, pad, _take_words(transcripts, att.take_id))
        plan.warnings.extend(f"{line.id}: {w}" for w in warns)
        if la.status == "weak":
            plan.warnings.append(f"{line.id}: no clean attempt — using the best one "
                                 f"({att.coverage:.0%} of the line). Re-record if possible.")
        words = _words_in(transcripts, att.take_id, a, b)
        gaps = audit_gaps(att.take_id, (a, b), sm, words)
        wav = Path(project.take(att.take_id).audio_path or "")
        _check_gaps(gaps, project, wav, sm, transcriber, verify)
        beat.gaps = gaps
        beat.segments = _segments_for(beat.id, [line.id], att.take_id, (a, b), gaps, pad)
        beat.note = (f"take {att.take_id} attempt {la.chosen + 1}/{len(la.attempts)} "
                     f"@ {att.start:.2f}s, coverage {att.coverage:.0%}")
        plan.beats.append(beat)

    # English captions: always English, even on the Arabic cut.
    if lang == "en":
        for beat, line in zip(plan.beats, lines):
            beat.caption_en = line_text(line, "en")
    else:
        need = [i for i, line in enumerate(lines) if not (line.en or "").strip()]
        for i, line in enumerate(lines):
            if line.en and line.en.strip():
                plan.beats[i].caption_en = line_text(line, "en")
        if need:
            if llm is None:
                plan.warnings.append(f"{len(need)} line(s) have no English text and Claude is unavailable — "
                                     "add the English script")
            else:
                translated = llm.translate_lines([line_text(lines[i], "ar") for i in need], source_lang="ar")
                for i, en in zip(need, translated):
                    plan.beats[i].caption_en = en
    return plan


def _unscripted(project, transcripts, speech_maps, blocks, verify, transcriber, llm, progress) -> Plan:
    pad = SETTINGS.speech.pad_s
    by_block = {tr.block_id: tr for tr in transcripts}
    rows = [{"block_id": b.id, "take_id": b.take_id, "start": round(b.start, 2), "end": round(b.end, 2),
             "text": by_block[b.id].text if b.id in by_block else ""}
            for b in sorted(blocks, key=lambda b: (project.take(b.take_id).order, b.start))]
    if progress:
        progress(0.1, "asking Claude to order the takes")
    order = llm.plan_unscripted(rows)
    plan = Plan(created_by="claude")
    block_map = {b.id: b for b in blocks}
    for n, item in enumerate(order):
        ids = [i for i in item.get("block_ids", []) if i in block_map]
        if not ids:
            continue
        chosen = [block_map[i] for i in ids]
        take_id = chosen[0].take_id
        if any(b.take_id != take_id for b in chosen):
            plan.warnings.append(f"beat {n + 1}: blocks span takes; using {take_id} only")
            chosen = [b for b in chosen if b.take_id == take_id]
        sm = speech_maps[take_id]
        a = max(0.0, min(b.start for b in chosen) - pad)
        b_ = min(sm.duration, max(b.end for b in chosen) + pad)
        beat = Beat(id=f"B{n + 1:02d}", caption_en=item.get("caption_en", ""), note=item.get("reason", ""))
        words = _words_in(transcripts, take_id, a, b_)
        gaps = audit_gaps(take_id, (a, b_), sm, words)
        _check_gaps(gaps, project, Path(project.take(take_id).audio_path or ""), sm, transcriber, verify)
        beat.gaps = gaps
        beat.segments = _segments_for(beat.id, [], take_id, (a, b_), gaps, pad)
        plan.beats.append(beat)
    if not plan.beats:
        plan.warnings.append("Claude returned no usable beats")
    return plan


_ALLOWED_TEMPLATES = {"title", "list", "stat", "screenshot", "nametag", "logo", "quote"}


def _add_suggested_graphics(plan: Plan, llm: PlannerLLM) -> None:
    """Ask Claude for motion-graphic beats; keep only well-formed ones (best effort)."""
    rows = [{"beat_id": b.id, "caption_en": b.caption_en, "has_graphic": bool(b.graphics)}
            for b in plan.beats if b.segments]
    if not rows:
        return
    try:
        suggestions = llm.suggest_graphics(rows)
    except Exception as exc:  # suggestions are optional; never block the plan
        plan.warnings.append(f"graphics suggestions skipped: {exc}")
        return
    beats = {b.id: b for b in plan.beats}
    for s in suggestions:
        beat = beats.get(s.get("beat_id", ""))
        tpl = s.get("template")
        if beat is None or tpl not in _ALLOWED_TEMPLATES or beat.graphics:
            continue
        beat.graphics.append(GraphicSpec(
            id=f"{beat.id}g{len(beat.graphics) + 1}", template=tpl, params=s.get("params") or {},
            beat_offset=float(s.get("beat_offset", 0.2)), duration=float(s.get("duration", 3.0))))


def apply_choice(plan: Plan, beat_id: str, attempt_index: int, project: Project, alignment: Alignment,
                 transcripts: list[BlockTranscript], speech_maps: dict[str, SpeechMap]) -> Plan:
    """The creator picked another attempt for a beat: rebuild that beat's segments."""
    beat = next(b for b in plan.beats if b.id == beat_id)
    la = next(la for la in alignment.lines if la.line_id in beat.line_ids)
    att = la.attempts[attempt_index]
    la.chosen = attempt_index
    sm = speech_maps[att.take_id]
    pad = SETTINGS.speech.pad_s
    a, b, warns = attempt_segment(att, sm, pad, _take_words(transcripts, att.take_id))
    gaps = audit_gaps(att.take_id, (a, b), sm, _words_in(transcripts, att.take_id, a, b))
    for g in gaps:
        g.verified = None  # re-verified at render/QA time
    beat.gaps = gaps
    beat.segments = _segments_for(beat.id, beat.line_ids, att.take_id, (a, b), gaps, pad)
    beat.note = f"take {att.take_id} attempt {attempt_index + 1}/{len(la.attempts)} (picked by creator)"
    plan.approved, plan.approved_at = False, None
    plan.warnings = [w for w in plan.warnings if not w.startswith(f"{la.line_id}:")] + \
        [f"{la.line_id}: {w}" for w in warns]
    return plan


def plan_summary(plan: Plan) -> str:
    """Human-readable beat-by-beat plan (CLI + Claude Code show this before executing)."""
    out = [f"PLAN ({len(plan.beats)} beats, {'APPROVED' if plan.approved else 'NOT approved'})"]
    for b in plan.beats:
        dur = sum(s.src_out - s.src_in for s in b.segments)
        state = "" if b.enabled else " [disabled]"
        out.append(f"{b.id}{state}  {dur:5.2f}s  {b.note}")
        out.append(f"      caption: {b.caption_en}")
        for g in b.gaps:
            out.append(f"      gap {g.start:.2f}-{g.end:.2f} ({g.dur:.2f}s, {g.kind}) -> {g.action}"
                       + ("" if g.verified is None else (" A/B ok" if g.verified else " A/B MISMATCH")))
        for gr in b.graphics:
            out.append(f"      graphic {gr.template}: {re.sub(chr(10), ' ', str(gr.params))[:80]}")
    if plan.warnings:
        out.append("WARNINGS:")
        out.extend(f"  - {w}" for w in plan.warnings)
    return "\n".join(out)
