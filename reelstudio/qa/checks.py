"""Adversarial QA checks on the DELIVERED file.

Every check here replaced a step that "looked like verification but could not see the
failure" and shipped a broken reel: overlapped audio played twice, one wide transcription
smoothed a stutter away, a re-cut left captions 0.75 s late, a -38 dB floor deleted
"engineers". Each check returns CheckResult(status pass|warn|fail|skip).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ..config import SETTINGS
from ..models import (BlockTranscript, CaptionCue, CheckResult, Layout, Plan, Project, Script, Timeline)
from ..util.proc import run
from ..util.textnorm import words as norm_words


def _r(name, ok: bool, summary: str, warn: bool = False, **details) -> CheckResult:
    status = "pass" if ok else ("warn" if warn else "fail")
    return CheckResult(name=name, status=status, summary=summary, details=details)


# ── container / stream ───────────────────────────────────────────────────────
def delivery_spec(final: Path, settings=SETTINGS) -> CheckResult:
    d = settings.delivery
    data = json.loads(run([settings.ffprobe, "-v", "error", "-show_entries",
                           "stream=codec_type,codec_name,codec_tag_string,width,height,pix_fmt,r_frame_rate,"
                           "avg_frame_rate,color_range,color_space,color_transfer,color_primaries,sample_rate:"
                           "stream_side_data_list:format=format_name", "-of", "json", str(final)]).stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
    want = {"codec_name": d.vcodec, "codec_tag_string": d.vtag, "width": d.width, "height": d.height,
            "r_frame_rate": f"{d.fps}/1", "pix_fmt": d.pix_fmt, "color_range": d.color_range,
            "color_space": d.color_space, "color_transfer": d.color_trc, "color_primaries": d.color_primaries}
    bad = {k: (v.get(k), w) for k, w in want.items() if v.get(k) != w}
    if "mov" not in data.get("format", {}).get("format_name", ""):
        bad["container"] = (data.get("format", {}).get("format_name"), "mov")
    hdr_side = [s.get("side_data_type") for s in v.get("side_data_list", []) or []
                if re.search(r"dovi|mastering|light level", str(s.get("side_data_type", "")), re.I)]
    if hdr_side:
        bad["hdr_side_data"] = (hdr_side, [])
    if a.get("codec_name") != d.audio_codec or int(a.get("sample_rate", 0) or 0) != d.audio_rate:
        bad["audio"] = ((a.get("codec_name"), a.get("sample_rate")), (d.audio_codec, d.audio_rate))
    return _r("delivery_spec", not bad, "2160x3840 60p HEVC hvc1 Rec.709 tv .mov" if not bad
              else "delivery spec mismatch: " + ", ".join(f"{k}={got} (want {w})" for k, (got, w) in bad.items()),
              mismatches={k: [str(x) for x in t] for k, t in bad.items()})


def _durations(final: Path, settings=SETTINGS) -> tuple[float, float]:
    data = json.loads(run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration",
                           "-of", "json", str(final)]).stdout)
    dv = next(float(s["duration"]) for s in data["streams"] if s["codec_type"] == "video")
    da = next(float(s["duration"]) for s in data["streams"] if s["codec_type"] == "audio")
    return dv, da


def av_duration(final: Path, timeline: Timeline, settings=SETTINGS) -> CheckResult:
    tol = settings.qa.av_tolerance_frames / timeline.fps + 0.024  # + one AAC frame
    dv, da = _durations(final, settings)
    ok = abs(dv - timeline.duration) <= tol and abs(da - dv) <= tol
    return _r("av_duration", ok, f"video {dv:.3f}s, audio {da:.3f}s, timeline {timeline.duration:.3f}s"
              + ("" if ok else " — project and cut have diverged"), video=dv, audio=da, timeline=timeline.duration)


def timeline_invariants(timeline: Timeline, plan: Plan, cues: list[CaptionCue], inserts: list[dict]) -> CheckResult:
    from ..edit.edl import beat_spans, validate_timeline
    problems = validate_timeline(timeline)
    dur = timeline.duration + 1e-6
    spans = beat_spans(timeline)
    problems += [f"caption {c.id} ends at {c.end:.2f}s after the cut ({dur:.2f}s)" for c in cues if c.end > dur]
    problems += [f"caption {a.id} overlaps {b.id}" for a, b in zip(cues, cues[1:]) if b.start < a.end - 1e-6]
    for ins in inserts:
        if ins["start"] + ins.get("duration", 0) > dur:
            problems.append(f"graphic {ins.get('graphic_id')} runs past the end")
    for beat in plan.beats:
        if beat.enabled and beat.segments and beat.id not in spans:
            problems.append(f"{beat.id} has segments but no place in the timeline")
    return _r("timeline_invariants", not problems, "segments never overlap; everything ends inside the cut"
              if not problems else f"{len(problems)} problem(s)", problems=problems)


# ── speech ───────────────────────────────────────────────────────────────────
def _tokens(words) -> list[str]:
    return [t for w in words for t in norm_words(w.text)]


def stutter_scan(samples: np.ndarray, lang: str, transcriber, settings=SETTINGS) -> CheckResult:
    """3.5 s windows stepping 1.75 s: a stutter straddling one window's edge is obvious to the next."""
    from ..asr.transcribe import transcribe_array
    from ..media.audioio import SR, slice_s
    win, step = settings.qa.stutter_window_s, settings.qa.stutter_step_s
    total = len(samples) / SR
    hits, doubles, windows = [], [], []
    t = 0.0
    while t < total - 0.5:
        toks = _tokens(transcribe_array(slice_s(samples, SR, t, t + win), language=lang, transcriber=transcriber))
        windows.append({"t": round(t, 2), "text": " ".join(toks)})
        for i in range(len(toks) - 3):
            if toks[i:i + 2] == toks[i + 2:i + 4]:
                hits.append(f"{t:.2f}s: '{' '.join(toks[i:i + 4])}'")
        for i in range(len(toks) - 1):
            if toks[i] == toks[i + 1] and len(toks[i]) > 2:
                doubles.append(f"{t:.2f}s: '{toks[i]} {toks[i]}'")
        t += step
    if hits:
        return _r("stutter_scan", False, f"repeated word pair: {hits[0]}", hits=hits, windows=windows)
    if doubles:
        return _r("stutter_scan", False, f"doubled word: {doubles[0]}", warn=True, doubles=doubles, windows=windows)
    return _r("stutter_scan", True, f"{len(windows)} windows clean", windows=windows)


def closed_gaps_ab(project: Project, plan: Plan, timeline: Timeline, delivered: np.ndarray, transcriber,
                   settings=SETTINGS) -> CheckResult:
    """Every closed gap: words of the uncut source == words of the delivered audio around the cut."""
    from ..asr.transcribe import transcribe_array
    from ..edit.edl import src_to_out
    from ..media.audioio import SR, load_cached, slice_s
    pad, ctx = settings.speech.pad_s, 1.5
    lang = project.settings.lang
    gaps = [(b, g) for b in plan.beats if b.enabled for g in b.gaps if g.action == "close"]
    if not gaps:
        return CheckResult(name="closed_gaps_ab", status="pass", summary="no closed gaps")
    diffs, checked = [], []
    for beat, g in gaps:
        o = src_to_out(timeline, g.take_id, g.start + pad - 0.01)
        if o is None:
            continue
        src = load_cached(project.take(g.take_id).audio_path, SR)
        from ..edit.gaps import compare_ab
        lo = g.start + pad - ctx
        wa = transcribe_array(slice_s(src, SR, lo, g.end - pad + ctx), language=lang, transcriber=transcriber)
        wb = transcribe_array(slice_s(delivered, SR, o + 0.01 - ctx, o + 0.01 + ctx), language=lang,
                              transcriber=transcriber)
        ok, why = compare_ab(wa, wb, ctx, ctx)  # cut point sits ctx seconds into both windows
        checked.append({"beat": beat.id, "gap": [g.start, g.end], "result": why})
        if not ok:
            diffs.append(f"{beat.id} gap {g.start:.2f}s: {why}")
    return _r("closed_gaps_ab", not diffs, f"{len(checked)} closed gap(s) keep every word" if not diffs
              else diffs[0], diffs=diffs, checked=checked)


def final_gap_scan(samples: np.ndarray, timeline: Timeline, plan: Plan, settings=SETTINGS) -> CheckResult:
    from ..edit.edl import beat_spans, src_to_out
    from ..media.audioio import SR
    from ..media.speech import speech_map_array
    runs = speech_map_array(samples, SR)
    spans = beat_spans(timeline)
    kept = [src_to_out(timeline, g.take_id, (g.start + g.end) / 2) for b in plan.beats for g in b.gaps
            if g.action == "keep"]
    long_gaps, kept_hits = [], []
    for a, b in zip(runs, runs[1:]):
        dur = b.start - a.end
        if dur <= settings.qa.final_gap_max_s:
            continue
        inside = any(s + 0.3 < a.end and b.start < e - 0.3 for s, e in spans.values())
        if not inside:
            continue
        if any(k is not None and a.end - 0.2 <= k <= b.start + 0.2 for k in kept):
            kept_hits.append(f"{a.end:.2f}-{b.start:.2f}s ({dur:.2f}s, kept after A/B)")
        else:
            long_gaps.append(f"{a.end:.2f}-{b.start:.2f}s ({dur:.2f}s)")
    if long_gaps:
        return _r("final_gap_scan", False, f"long gap inside a phrase at {long_gaps[0]}", gaps=long_gaps)
    if kept_hits:
        return _r("final_gap_scan", False, f"pause kept to protect a word at {kept_hits[0]}", warn=True, kept=kept_hits)
    return _r("final_gap_scan", True, "no long mid-phrase gaps in the delivered audio")


def script_coverage(samples: np.ndarray, script: Script | None, lang: str, transcriber, takes) -> CheckResult:
    if not script or not script.lines:
        return CheckResult(name="script_coverage", status="skip", summary="no script")
    from ..asr.transcribe import transcribe_array
    from ..edit.align import align_script
    words = transcribe_array(samples, language=lang, transcriber=transcriber)
    tr = BlockTranscript(block_id="final", take_id=takes[0].id if takes else "final", words=words)
    al = align_script(script, lang, [tr], takes[:1] or [])
    missing = [la.line_id for la in al.lines if la.chosen is None]
    order = [la.attempts[la.chosen].start for la in al.lines if la.chosen is not None]
    in_order = all(x <= y for x, y in zip(order, order[1:]))
    weak = [la.line_id for la in al.lines if la.status == "weak"]
    ok = not missing and in_order
    summary = ("every script line is in the reel, in order" if ok else
               (f"missing: {', '.join(missing)}" if missing else "lines out of script order"))
    return _r("script_coverage", ok, summary + (f" (weak: {', '.join(weak)})" if ok and weak else ""),
              missing=missing, weak=weak, transcript=" ".join(w.text for w in words))


# ── picture ──────────────────────────────────────────────────────────────────
def face_occlusion(clips: list[dict], layout: Layout) -> CheckResult:
    """Graphics alpha (shadows included) must never touch his head region."""
    from ..motion.hyperframes import alpha_bbox, overlay_alpha_in_box
    if layout.head is None or not clips:
        return CheckResult(name="face_occlusion", status="pass" if clips == [] else "skip",
                           summary="no graphics" if not clips else "no head box")
    hits = []
    for c in clips:
        bb = alpha_bbox(Path(c["path"]))
        h = layout.head
        if bb and bb.x0 < h.x1 and bb.x1 > h.x0 and bb.y0 < h.y1 and bb.y1 > h.y0:
            frac = overlay_alpha_in_box(Path(c["path"]), h)
            if frac > SETTINGS.qa.face_occlusion_max:
                hits.append(f"{c.get('graphic_id')}: {frac:.1%} of the head region covered (bbox {bb})")
    return _r("face_occlusion", not hits, "no graphic pixel (or shadow) on his head" if not hits else hits[0],
              hits=hits)


def captions_check(cues: list[CaptionCue], ass_path: Path | None, layout: Layout | None, duration: float,
                   settings=SETTINGS) -> CheckResult:
    problems = []
    if not cues:
        problems.append("no captions (captions are always expected)")
    text = " ".join(c.text for c in cues)
    if any("\u0600" <= ch <= "\u06ff" for ch in text):
        problems.append("Arabic letters in captions — captions are ALWAYS English")
    for pat, _ in settings.asr.name_fixes:
        m = re.search(pat, text, re.I)
        if m:
            problems.append(f"name misspelled on screen: '{m.group(0)}' (it is Jweed)")
    problems += [f"{a.id}/{b.id} overlap" for a, b in zip(cues, cues[1:]) if b.start < a.end - 1e-6]
    problems += [f"{c.id} outside the reel" for c in cues if c.start < 0 or c.end > duration + 1e-6]
    if ass_path and ass_path.exists() and layout is not None:
        for m in re.finditer(r"\\pos\((\d+),(\d+)\)", ass_path.read_text(encoding="utf-8")):
            y = int(m.group(2))
            if not (layout.caption_band.start <= y <= layout.caption_band.end):
                problems.append(f"caption at y={y} outside the caption band")
                break
            if layout.head and layout.head.y0 <= y <= layout.head.y1:
                problems.append(f"caption at y={y} is on his head")
                break
    return _r("captions_check", not problems, f"{len(cues)} English cues, inside the band" if not problems
              else problems[0], problems=problems)


def loudness(final: Path, settings=SETTINGS) -> CheckResult:
    from ..media.loudness import measure
    m = measure(final, settings)
    i, tp = m.get("I"), m.get("TP")
    d, q = settings.delivery, settings.qa
    ok = i is not None and tp is not None and abs(i - d.loudness_i) <= q.loudness_tol_lu and tp <= q.true_peak_max_db
    return _r("loudness", ok, f"{i} LUFS, true peak {tp} dBTP (target {d.loudness_i} LUFS, <= {q.true_peak_max_db} dBTP)",
              **m)


def black_freeze(final: Path, settings=SETTINGS) -> CheckResult:
    proc = run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(final), "-vf",
                "fps=10,scale=270:480,blackdetect=d=0.1:pix_th=0.10,freezedetect=n=0.003:d=2", "-an", "-f", "null", "-"])
    log = proc.stderr.decode("utf-8", "replace")
    black = re.findall(r"black_start:([\d.]+)", log)
    frozen = re.findall(r"freeze_start: ([\d.]+)", log)
    if black:
        return _r("black_freeze", False, f"black frames at {black[0]}s", black=black)
    if frozen:
        return _r("black_freeze", False, f"frozen picture for 2 s+ at {frozen[0]}s", warn=True, frozen=frozen)
    return _r("black_freeze", True, "no black or frozen frames")


def mirror_rule(project: Project) -> CheckResult:
    off = [t.id for t in project.takes if not t.mirror]
    return _r("mirror_rule", not off, "every take mirrored (hflip)" if not off
              else f"NOT mirrored: {', '.join(off)} — his camera records him mirrored", warn=True, takes=off)


# ── narrated (generated-voice) reels ─────────────────────────────────────────
def pronunciation_lines(voice_dir: Path, lines: list[str], lang: str, transcriber, settings=SETTINGS) -> CheckResult:
    """Re-listen to every generated line clip (narr_XX.wav) and match it to its script line — QA does not
    trust the build's own report. The build's regeneration count is reported alongside."""
    from ..asr.transcribe import transcribe_array
    from ..media.audioio import SR, read_audio
    from ..voice.pronounce import check
    bad, redone = [], 0
    rep = voice_dir / "pronunciation.json"
    if rep.exists():
        redone = sum(1 for r in json.loads(rep.read_text()) if r.get("chosen_try", 1) > 1 or len(r.get("tries", [])) > 1)
    for i, text in enumerate(lines):
        wav = voice_dir / f"narr_{i + 1:02d}.wav"
        if not wav.exists():
            bad.append(f"line {i + 1}: no voice clip")
            continue
        res = check(text, transcribe_array(read_audio(wav, sr=SR), language=lang, transcriber=transcriber), settings)
        if not res.ok:
            bad.append(f"line {i + 1}: " + "; ".join(str(p) for p in res.problems))
    return _r("pronunciation_lines", not bad, f"{len(lines)} lines heard back clean ({redone} regenerated in the build)"
              if not bad else bad[0], problems=bad)


def pronunciation_final(samples: np.ndarray, lines: list[str], cues: list[CaptionCue], lang: str, transcriber,
                        settings=SETTINGS) -> CheckResult:
    """Listen to the DELIVERED audio line by line (windows from the caption word times, cut at the midpoint of
    each pause): every script word heard, nothing misheard, doubled or invented. Short windows on purpose —
    long-form whisper drifts and invents doubles ("air air") that are not in the audio."""
    from ..asr.transcribe import transcribe_array
    from ..media.audioio import SR
    from ..voice.pronounce import check
    spans = []
    for i in range(len(lines)):
        ws = [w for c in cues if c.beat_id == f"B{i + 1:02d}" for w in c.words]
        spans.append((ws[0].start, ws[-1].end) if ws else None)
    if any(s is None for s in spans):
        return _r("pronunciation_final", False, "captions do not cover every script line")
    total = len(samples) / SR
    probs = []
    for i, (text, (a, b)) in enumerate(zip(lines, spans)):
        lo = 0.0 if i == 0 else (spans[i - 1][1] + a) / 2
        hi = total if i == len(lines) - 1 else (b + spans[i + 1][0]) / 2
        heard = transcribe_array(samples[int(lo * SR):int(hi * SR)], language=lang, transcriber=transcriber)
        for w in heard:
            w.start += lo
        res = check(text, heard, settings)
        probs += [f"line {i + 1}: {p}" for p in res.problems]
    n = sum(len(t.split()) for t in lines)
    return _r("pronunciation_final", not probs, f"all {n} script words heard clearly in the delivered audio"
              if not probs else f"{len(probs)} unclear: " + "; ".join(probs[:4]), problems=probs)
