"""Stage orchestration — the CLI, the web API and Claude Code all call these functions.

ingest -> analyze -> plan -> (creator approves) -> render -> qa
Every stage records running/done/failed on the project and marks later stages stale:
re-cutting invalidates every downstream timing, so nothing downstream is trusted.
"""
from __future__ import annotations

import json
import os
import re
import traceback
from contextlib import contextmanager
from pathlib import Path

from .config import SETTINGS
from .models import (STAGES, Alignment, Block, BlockTranscript, CaptionCue, FaceInfo, Plan, Project, QAReport,
                     Script, ScriptLine, SpeechMap, StageStatus, Take, Timeline, VoiceClip, now_iso)
from .store import ProjectStore, read_json, write_json
from .util.proc import ProgressFn
from .util.textnorm import strip_brackets

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".mts", ".hevc"}


class StageError(RuntimeError):
    pass


# ── project setup ────────────────────────────────────────────────────────────
def create_project(store: ProjectStore, name: str, *, lang: str = "ar", mode: str = "talking_head") -> Project:
    p = store.create(name, mode=mode)
    p.settings.lang = lang  # type: ignore[assignment]
    store.save(p)
    return p


def import_takes(store: ProjectStore, pid: str, paths: list[str], *, lang: str | None = None,
                 mirror: bool = True) -> Project:
    """Symlink originals into takes/ (never copied, never modified). Ids follow filename order."""
    pp = store.paths(pid)
    pp.takes_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for raw in paths:
        src = Path(raw).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"not a file: {raw}")
        if src.suffix.lower() not in VIDEO_EXT:
            raise ValueError(f"not a video file: {src.name}")
        files.append(src)

    def add(p: Project):
        known = {t.source_path for t in p.takes}
        for src in sorted(files, key=lambda f: f.name.lower()):
            if str(src) in known:
                continue
            n = len(p.takes) + 1
            tid = f"t{n:02d}"
            link = pp.takes_dir / f"{tid}_{src.name}"
            if not link.exists():
                link.symlink_to(src)
            p.takes.append(Take(id=tid, filename=src.name, source_path=str(src), order=n,
                                lang=lang, mirror=mirror))  # type: ignore[arg-type]
        _stale_from(p, "ingest")
    return store.update(pid, add)


def remove_take(store: ProjectStore, pid: str, take_id: str) -> Project:
    def rm(p: Project):
        p.takes = [t for t in p.takes if t.id != take_id]
        _stale_from(p, "analyze")
    return store.update(pid, rm)


def parse_script(ar_text: str | None, en_text: str | None) -> Script:
    ar = [l.strip() for l in (ar_text or "").splitlines() if l.strip()]
    en = [l.strip() for l in (en_text or "").splitlines() if l.strip()]
    if ar and en and len(ar) != len(en):
        raise ValueError(f"Arabic script has {len(ar)} lines but English has {len(en)} — they must pair line by line")
    lines = []
    for i in range(max(len(ar), len(en))):
        a = ar[i] if i < len(ar) else None
        e = en[i] if i < len(en) else None
        onscreen = None
        for raw in (e, a):  # prefer the English on-screen text (cards are English like captions)
            if raw and strip_brackets(raw)[1]:
                onscreen = strip_brackets(raw)[1]
                break
        lines.append(ScriptLine(id=f"L{i + 1:02d}", ar=a, en=e, onscreen=onscreen))
    return Script(lines=lines)


def set_script(store: ProjectStore, pid: str, *, ar_text: str | None = None, en_text: str | None = None) -> Project:
    script = parse_script(ar_text, en_text)

    def put(p: Project):
        p.script = script
        _stale_from(p, "plan")
    return store.update(pid, put)


# ── stage bookkeeping ────────────────────────────────────────────────────────
def _stale_from(p: Project, stage: str) -> None:
    for s in STAGES[STAGES.index(stage):]:
        st = p.stages.setdefault(s, StageStatus())
        if st.state == "done":
            st.state = "stale"


@contextmanager
def _stage(store: ProjectStore, pid: str, stage: str, progress: ProgressFn | None):
    def mark(**kw):
        def f(p: Project):
            st = p.stages.setdefault(stage, StageStatus())
            for k, v in kw.items():
                setattr(st, k, v)
        store.update(pid, f)

    mark(state="running", message="", progress=0.0, started_at=now_iso(), finished_at=None)

    def report(frac: float, msg: str):
        if progress:
            progress(frac, msg)

    try:
        yield report
    except BaseException as exc:
        mark(state="failed", message=str(exc)[:500], finished_at=now_iso())
        raise
    else:
        def done(p: Project):
            p.stages[stage] = StageStatus(state="done", progress=1.0, started_at=p.stages[stage].started_at,
                                          finished_at=now_iso())
            later = STAGES[STAGES.index(stage) + 1:] if stage in STAGES else []
            for s in later:
                if p.stages[s].state == "done":
                    p.stages[s].state = "stale"
        store.update(pid, done)


def _sub(report, lo: float, hi: float):
    return lambda f, m: report(lo + (hi - lo) * f, m)


# ── stages ───────────────────────────────────────────────────────────────────
def ingest(store: ProjectStore, pid: str, progress: ProgressFn | None = None) -> Project:
    from .media.conform import extract_audio, make_proxy
    from .media.probe import probe
    with _stage(store, pid, "ingest", progress) as report:
        p = store.load(pid)
        if not p.takes:
            raise StageError("no takes imported")
        pp = store.paths(pid)
        for i, take in enumerate(p.takes):
            span = (i / len(p.takes), (i + 1) / len(p.takes))
            report(span[0], f"{take.id}: probe")
            take.info = probe(take.source_path)
            report(span[0], f"{take.id}: audio")
            take.audio_path = str(extract_audio(take, pp.audio(take.id)))
            take.proxy_path = str(make_proxy(take, pp.proxy(take.id), progress=_sub(report, *span)))

        def save(q: Project):
            q.takes = p.takes
        return store.update(pid, save)


def _take_lang(p: Project, take: Take) -> str:
    return take.lang or p.settings.lang


def analyze(store: ProjectStore, pid: str, progress: ProgressFn | None = None, transcriber=None) -> Project:
    from .asr.engines import get_transcriber
    from .asr.transcribe import transcribe_blocks
    from .media.face import measure_face
    from .media.speech import blocks_from_map, speech_map
    with _stage(store, pid, "analyze", progress) as report:
        p = store.load(pid)
        pp = store.paths(pid)
        if any(not t.audio_path for t in p.takes):
            raise StageError("run ingest first")
        transcriber = transcriber or get_transcriber(SETTINGS)
        n = len(p.takes)
        for i, take in enumerate(p.takes):
            lo, hi = i / n, (i + 1) / n
            report(lo, f"{take.id}: speech map")
            sm = speech_map(take.id, Path(take.audio_path))
            blocks = blocks_from_map(sm)
            write_json(pp.speech(take.id), sm)
            write_json(pp.blocks(take.id), blocks)
            trs = transcribe_blocks(take, blocks, language=_take_lang(p, take), transcriber=transcriber,
                                    progress=_sub(report, lo, lo + (hi - lo) * 0.85))
            write_json(pp.transcripts(take.id), trs)
            report(lo + (hi - lo) * 0.9, f"{take.id}: face box")
            face = measure_face(take, Path(take.proxy_path)) if take.proxy_path else \
                FaceInfo(take_id=take.id, canvas_w=2160, canvas_h=3840, samples=0, detections=0)
            write_json(pp.face(take.id), face)
        return store.load(pid)


def _load_analysis(store: ProjectStore, p: Project):
    pp = store.paths(p.id)
    sms, blocks, trs = {}, [], []
    for t in p.takes:
        if not pp.speech(t.id).exists():
            raise StageError(f"{t.id} not analyzed — run analyze")
        sms[t.id] = read_json(pp.speech(t.id), SpeechMap)
        blocks += read_json(pp.blocks(t.id), list[Block])
        trs += read_json(pp.transcripts(t.id), list[BlockTranscript])
    return sms, blocks, trs


def _llm():
    if os.environ.get("REEL_NO_LLM") or os.environ.get("REEL_E2E_NO_LLM"):
        return None
    try:
        from .llm.claude import available
        from .llm.tasks import ClaudeTasks
        ok, _ = available()
        return ClaudeTasks() if ok else None
    except Exception:
        return None


def plan(store: ProjectStore, pid: str, progress: ProgressFn | None = None, *, verify_gaps: bool = True,
         transcriber=None) -> Plan:
    from .asr.engines import get_transcriber
    from .edit.align import align_script
    from .edit.planner import build_plan
    with _stage(store, pid, "plan", progress) as report:
        p = store.load(pid)
        pp = store.paths(pid)
        sms, blocks, trs = _load_analysis(store, p)
        cut_takes = [t for t in p.takes if _take_lang(p, t) == p.settings.lang] or p.takes
        ids = {t.id for t in cut_takes}
        trs = [t for t in trs if t.take_id in ids]
        blocks = [b for b in blocks if b.take_id in ids]
        alignment = None
        if p.script and p.script.lines:
            report(0.05, "aligning takes to the script")
            alignment = align_script(p.script, p.settings.lang, trs, cut_takes)
            write_json(pp.alignment, alignment)
        transcriber = transcriber or (get_transcriber(SETTINGS) if verify_gaps else None)
        result = build_plan(p, alignment, trs, sms, blocks=blocks, verify_gaps=verify_gaps,
                            transcriber=transcriber, llm=_llm(), progress=_sub(report, 0.1, 0.95))
        write_json(pp.plan, result)
        return result


def get_plan(store: ProjectStore, pid: str) -> Plan:
    pp = store.paths(pid)
    if not pp.plan.exists():
        raise FileNotFoundError("no plan yet — run plan")
    return read_json(pp.plan, Plan)


def update_plan(store: ProjectStore, pid: str, new: Plan) -> Plan:
    new.approved, new.approved_at = False, None
    write_json(store.paths(pid).plan, new)
    store.update(pid, lambda p: _stale_from(p, "render"))
    return new


def choose_attempt(store: ProjectStore, pid: str, beat_id: str, attempt_index: int) -> Plan:
    from .edit.planner import apply_choice
    p = store.load(pid)
    pp = store.paths(pid)
    alignment = read_json(pp.alignment, Alignment)
    sms, _, trs = _load_analysis(store, p)
    result = apply_choice(get_plan(store, pid), beat_id, attempt_index, p, alignment, trs, sms)
    write_json(pp.alignment, alignment)
    return update_plan(store, pid, result)


def approve(store: ProjectStore, pid: str) -> Plan:
    pl = get_plan(store, pid)
    if not any(b.enabled and b.segments for b in pl.beats):
        raise StageError("the plan has no beats with footage")
    pl.approved, pl.approved_at = True, now_iso()
    write_json(store.paths(pid).plan, pl)
    return pl


def render(store: ProjectStore, pid: str, progress: ProgressFn | None = None, *, preview: bool = False) -> Path:
    from .edit.captions import build_cues, write_ass
    from .edit.edl import build_timeline
    from .media.face import compute_layout
    from .media.render import concat_segments, final_encode, render_segments
    from .motion import hyperframes as hf

    pl = get_plan(store, pid)
    if not pl.approved:
        raise StageError("the plan is not approved — show the beat-by-beat plan and approve it first")
    with _stage(store, pid, "render", progress) as report:
        p = store.load(pid)
        pp = store.paths(pid)
        sms, _, trs = _load_analysis(store, p)
        d = SETTINGS.delivery
        timeline = build_timeline(pl, d.fps)
        if not timeline.entries:
            raise StageError("the approved plan produced an empty timeline")
        write_json(pp.timeline, timeline)

        used = sorted({e.take_id for e in timeline.entries})
        faces = [read_json(pp.face(t), FaceInfo) for t in used if pp.face(t).exists()]
        layout, layout_warn = compute_layout(faces)
        write_json(pp.layout, layout)
        warnings = list(layout_warn)

        report(0.02, "captions")
        cues = build_cues(pl, timeline, trs, sms, lang=p.settings.lang) if p.settings.captions else []
        write_json(pp.captions_json, cues)
        ass = write_ass(cues, layout, pp.captions_ass) if cues else None

        clips: list[tuple[Path, float]] = []
        clip_meta: list[dict] = []
        if p.settings.graphics and any(b.graphics for b in pl.beats if b.enabled):
            ok, why = hf.available()
            if ok:
                report(0.05, "graphics")
                inserts, w = hf.build_inserts(pl, timeline, layout, pp.inserts_dir, fps=d.fps)
                warnings += w
                clips = hf.render_inserts(inserts, pp.render_dir / "graphics", fps=d.fps, preview=preview,
                                          progress=_sub(report, 0.05, 0.3))
                clip_meta = [{"graphic_id": i.graphic_id, "path": str(c), "start": s, "duration": i.duration}
                             for i, (c, s) in zip(inserts, clips)]
            else:
                warnings.append(f"graphics skipped: {why}")
        (pp.render_dir).mkdir(parents=True, exist_ok=True)
        (pp.render_dir / "inserts.json").write_text(json.dumps(clip_meta, indent=2), encoding="utf-8")

        report(0.3, "cutting segments")
        segs = render_segments(p, timeline, pp.segments_dir, progress=_sub(report, 0.3, 0.6))
        cut = concat_segments(segs, pp.cut)
        out = pp.preview if preview else pp.final
        report(0.6, "final encode" if not preview else "preview encode")
        final_encode(cut, out, captions_ass=ass, overlays=clips,
                     music=Path(p.settings.music_path) if p.settings.music_path else None,
                     music_gain_db=p.settings.music_gain_db, preview=preview, progress=_sub(report, 0.6, 1.0))
        (pp.render_dir / "render_warnings.json").write_text(json.dumps(warnings, indent=2), encoding="utf-8")

        def setp(q: Project):
            if preview:
                q.preview_path = str(out)
            else:
                q.final_path = str(out)
        store.update(pid, setp)
        return out


def qa(store: ProjectStore, pid: str, progress: ProgressFn | None = None, transcriber=None) -> QAReport:
    from .qa.run import run_qa
    with _stage(store, pid, "qa", progress) as report:
        rep = run_qa(pid, store, SETTINGS, progress=report, transcriber=transcriber)
        if not rep.passed:
            failed = [c.name for c in rep.checks if c.status == "fail"]
            store.update(pid, lambda p: None)
            report(1.0, "FAILED: " + ", ".join(failed))
        return rep


def run_until_approval(store: ProjectStore, pid: str, progress: ProgressFn | None = None) -> Plan:
    report = progress or (lambda f, m: None)
    ingest(store, pid, _sub(report, 0.0, 0.3))
    analyze(store, pid, _sub(report, 0.3, 0.8))
    return plan(store, pid, _sub(report, 0.8, 1.0))


# ── voice ────────────────────────────────────────────────────────────────────
def generate_voice(store: ProjectStore, pid: str, *, text: str, lang: str, engine: str,
                   voice: str = "jweed", progress: ProgressFn | None = None) -> VoiceClip:
    from .media.audioio import duration_of
    from .voice.engines import get_tts
    pp = store.paths(pid)
    pp.voice_dir.mkdir(parents=True, exist_ok=True)
    clips_file = pp.voice_dir / "clips.json"
    clips = read_json(clips_file, list[VoiceClip]) if clips_file.exists() else []
    cid = f"v{len(clips) + 1:03d}"
    out = pp.voice_dir / f"{cid}_{engine}_{lang}.wav"
    tts = get_tts(engine)
    path = tts.synthesize(text, lang=lang, voice=voice, out=out, project=store.load(pid), store=store)
    clip = VoiceClip(id=cid, text=text, lang=lang, engine=engine, voice=voice, path=str(path),  # type: ignore[arg-type]
                     duration=duration_of(path))
    clips.append(clip)
    write_json(clips_file, clips)
    return clip


def list_voice(store: ProjectStore, pid: str) -> list[VoiceClip]:
    f = store.paths(pid).voice_dir / "clips.json"
    return read_json(f, list[VoiceClip]) if f.exists() else []


# ── read helpers for the UI/CLI ──────────────────────────────────────────────
def analysis_view(store: ProjectStore, pid: str) -> dict:
    p = store.load(pid)
    pp = store.paths(pid)
    out = []
    for t in p.takes:
        if not pp.speech(t.id).exists():
            continue
        sm = read_json(pp.speech(t.id), SpeechMap)
        blocks = read_json(pp.blocks(t.id), list[Block])
        trs = {tr.block_id: tr for tr in read_json(pp.transcripts(t.id), list[BlockTranscript])} \
            if pp.transcripts(t.id).exists() else {}
        face = read_json(pp.face(t.id), FaceInfo) if pp.face(t.id).exists() else None
        out.append({"take_id": t.id, "speech": {"runs": [r.model_dump() for r in sm.runs], "duration": sm.duration,
                                                "digital_zero_ratio": sm.digital_zero_ratio,
                                                "noise_floor_db": sm.noise_floor_db},
                    "blocks": [{**b.model_dump(), "text": trs[b.id].text if b.id in trs else "",
                                "language": trs[b.id].language if b.id in trs else None} for b in blocks],
                    "face": face.model_dump() if face else None})
    return {"takes": out}
