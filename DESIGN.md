# Reel Studio — design spec

Local app that turns Jweed's raw talking-head takes into a finished Instagram Reel:
2160x3840, 60 fps, HEVC `hvc1`, Rec.709 SDR `.mov`, with English word-synced captions,
HyperFrames motion graphics kept off his face, optional generated voice, and an
adversarial QA gate on the delivered file.

The creator's rules are the spec. They live in `.claude/skills/reel-standard/SKILL.md`
and every threshold is in `reelstudio/config.py`. Shared types are in
`reelstudio/models.py`; persistence/paths in `reelstudio/store.py`. **Do not change
those three files' public names without updating every caller.**

## Architecture

```
web UI (vanilla JS)  ─┐
reel CLI             ─┼─> reelstudio.pipeline (stages) ──> media / asr / edit / motion / voice / qa
Claude Code (skills, ─┘          │                              │
  .mcp.json tools)               └─ jobs (1 worker, progress → SSE)   └─ ffmpeg, mlx-whisper, YuNet,
                                                                         HyperFrames (node), claude -p,
                                                                         ElevenLabs, Chatterbox server
```

- Python 3.12 venv managed by `uv` (`uv run reel ...`). Heavy optional engines run in
  their own environments: Chatterbox TTS server (torch) in `services/chatterbox/.venv`,
  HyperFrames in the `render/` Node project, kinocut via `uvx` (MCP).
- Everything is local; the web server binds 127.0.0.1 only.
- Originals are never modified: takes are symlinked into the project.

## Stages (pipeline.py)

| Stage | Does | Writes |
|---|---|---|
| ingest | probe every take (HDR kind, rotation, range), extract 48 kHz mono float WAV, make a conformed 1080x1920 proxy (mirrored + SDR + tv range) | takes[].info, audio.wav, proxy.mp4 |
| analyze | speech map (10 ms RMS, -52 dB), blocks, isolate-transcribe every block, face box per take | speech.json, blocks.json, transcripts.json, face.json |
| plan | align blocks to the script (last clean repeat wins, script order), gap audit + A/B verify, beats with English captions + suggested graphics | alignment.json, plan.json (approved=false) |
| (approve) | creator reviews the beat-by-beat plan; may pick another attempt, edit caption text, toggle graphics | plan.json approved=true |
| render | timeline (frame-exact), segments → cut.mov (ProRes 422), captions.ass, HyperFrames overlay (alpha), loudness, final HEVC encode; `--preview` = 1080x1920/30 H.264 | timeline.json, captions.*, layout.json, render/* |
| qa | adversarial checks on the delivered file | qa/report.json |

Any change to the plan marks render + qa `stale`. Render refuses an unapproved plan.

## Module contracts

All functions take/return models from `reelstudio.models`. `settings` defaults to
`reelstudio.config.SETTINGS`. `progress` is `Callable[[float, str], None] | None`.

### util
- `util/proc.py` — `run`, `run_json`, `run_ffmpeg(args, total_s=, progress=, log_file=)`, `CommandError`.
- `util/textnorm.py` — `normalize`, `words`, `is_arabic`, `ends_sentence`, `strip_brackets`.
- `util/timecode.py` — `to_frames(t, fps) (round)`, `floor_frames`, `ceil_frames`, `frames_to_s`.

### media/probe.py
- `probe(path: Path, settings=SETTINGS) -> MediaInfo` — ffprobe JSON; display size after
  rotation (side_data displaymatrix / tags.rotate); HDR kind: `arib-std-b67` → hlg,
  `smpte2084` → pq, DOVI side data with HLG-compatible base → hlg, otherwise dolby_vision;
  bit depth from pix_fmt; fps from avg_frame_rate.

### media/conform.py
- `video_chain(info: MediaInfo, *, mirror: bool, width: int, height: int, fps: int, out_pix_fmt: str) -> str`
  ffmpeg -vf chain: `fps` → `hflip` (if mirror) → HDR→SDR (zscale+tonemap for hlg/pq/dv;
  verified chain in INTEGRATIONS.md) or range fix for full-range SDR → scale/crop to the
  canvas (cover, centered, lanczos) → `format`. Output is Rec.709, tv range.
- `extract_audio(take: Take, out: Path, settings) -> Path` — 48 kHz mono `pcm_f32le` WAV.
- `make_proxy(take: Take, out: Path, settings, progress) -> Path` — 1080x1920, 30 fps,
  `h264_videotoolbox`, same conform chain (so face boxes are measured on the mirrored image).

### media/audioio.py (DONE — use it, do not duplicate)
- `read_audio(path, sr=48000, start=None, end=None) -> np.ndarray` float32 mono via ffmpeg;
  `load_cached(path, sr)` whole-file decode cached per mtime; `slice_s(samples, sr, a, b)`;
  `to_asr(samples, sr) -> 16 kHz float32`; `write_wav(path, samples, sr)` 16-bit PCM;
  `duration_of(path)`; constants `SR = 48000`, `ASR_SR = 16000`.

### media/speech.py (DONE — tests in tests/test_speech.py)
- `speech_map(take_id: str, wav: Path, spec=SETTINGS.speech) -> SpeechMap`
  10 ms frames; RMS dB = 20·log10(rms+1e-12); speech where dB > floor (-52); merge runs
  closer than `merge_run_gap_s`; drop runs < `min_run_s`; digital_zero_ratio = frames with
  peak == 0; noise_floor_db = 10th percentile of non-zero non-speech frames.
- `blocks_from_map(sm: SpeechMap, spec) -> list[Block]` — merge runs with gaps <
  `block_gap_s`; drop blocks whose total speech < `min_block_speech_s`; ids `t01_b001`.
- `speech_map_array(samples: np.ndarray, sr, spec) -> list[Span]` — same detector for any
  audio array (QA reuses it on the delivered file).
- `gaps_between(runs, start, end) -> list[Span]`; `run_containing(runs, t, tol) -> int | None`;
  `frame_levels`, `runs_from_mask`, `merge_spans`.

### asr/
- `engines.py` — `class Transcriber(Protocol): name; model; def transcribe(self, audio: np.ndarray (16 kHz mono float32), *, language: str | None, prompt: str | None) -> tuple[list[Word] (relative seconds), str | None (language), float | None (prob)]`.
  Implementations: `MlxWhisper` (default; `mlx-community/whisper-large-v3-mlx`),
  `FasterWhisper` (CPU int8), `ElevenLabsScribe` (cloud, needs key).
  `get_transcriber(settings) -> Transcriber` (cached singleton per engine/model).
  Always: word timestamps on, VAD off, condition_on_previous_text off.
- `transcribe.py` — `transcribe_blocks(take: Take, blocks: list[Block], *, language: str | None, transcriber, progress) -> list[BlockTranscript]`
  Isolate each block: slice `[start - block_pad, end + block_pad]` from audio.wav, resample
  to 16 kHz, transcribe, offset word times to absolute source time, clamp into the slice.
  Language: if `language` given, force it. Else detect on the 5 longest blocks, take the
  majority, then force it for all blocks (auto-detect can silently TRANSLATE: Arabic text
  for English speech). Apply `fixes.apply_fixes` to every word.
  `transcribe_span(wav: Path, start, end, *, language, transcriber) -> list[Word]` — for QA/A-B.
- `fixes.py` (DONE) — `apply_fixes(text: str) -> str` (name fixes from `ASRSpec.name_fixes`).

### edit/align.py — last clean repeat wins (DONE — tests/test_align.py)
- `align_script(script: Script, lang: Lang, transcripts: list[BlockTranscript], takes: list[Take]) -> Alignment`
  1. Word stream W in recording order (take.order, then time), keeping take/block ids.
  2. For each script line (text in `lang`, brackets stripped) find every approximate
     occurrence in W **within one take**: candidate windows anchored where W[i] fuzzy-matches
     one of the line's first 3 words; align with an edit-distance DP using fuzzy word equality
     (rapidfuzz ratio ≥ 75 on normalized words); keep windows with coverage ≥ 0.5;
     non-max-suppress overlapping windows (keep best score).
  3. clean = coverage ≥ 0.85 and inserted words ≤ 15% of the line. `chosen` = the LAST
     clean attempt (status ok); else the best-coverage attempt, latest on ties (status weak);
     none → missing.
- `attempt_segment(attempt: Attempt, sm: SpeechMap, pad_s) -> tuple[float, float]`
  Cut points come from the SPEECH MAP, not whisper times: start = start of the speech run
  containing the first word (or the first run ending after it), end = end of the run
  containing the last word; pad by `pad_s` but never into a neighbouring run that is not
  part of the attempt (clamp to neighbour edge − 0.02 s; warn if the pad drops below 0.10 s).

### edit/gaps.py — mid-phrase dropouts (DONE — tests/test_gaps.py)
- `audit_gaps(take_id, seg: tuple[float, float], sm: SpeechMap, words: list[Word], spec) -> list[GapIssue]`
  Internal silences between speech runs inside the segment with dur ≥ `long_gap_s`.
  kind = `sentence` if the transcript word before the gap ends a sentence, else `mid_phrase`.
- `split_segment(seg, gaps, pad_s) -> list[tuple[float, float]]` — close each `close` gap to
  `pad_s` on each side.
- `ab_verify(gap: GapIssue, take: Take, sm, *, language, transcriber) -> tuple[bool, str]`
  Window = gap ± 1.5 s widened to speech-run edges. Transcribe uncut window and the cut
  version (two slices concatenated); compare normalized word lists. Different → (False,
  "uncut: … | cut: …") and the caller sets action=keep. Prefer a long pause over a lost word.

### edit/planner.py (DONE — tests/test_planner.py; `plan_summary(plan)` prints the beat-by-beat plan; LLM injected via the `PlannerLLM` protocol: translate_lines(lines, source_lang=) / plan_unscripted(rows) / suggest_graphics(rows))
- `build_plan(project: Project, alignment: Alignment | None, transcripts, speech_maps: dict, *, verify_gaps: bool, transcriber, llm) -> Plan`
  Scripted: one beat per script line in script order; segment(s) from the chosen attempt;
  gaps audited/closed/A-B verified; `caption_en` = the line's English text (script `en`),
  else Claude translation of the Arabic line (en takes: the line's own text). Lines that are
  missing produce a beat with no segments and a plan warning (full coverage is a rule).
  Unscripted (no script): Claude groups blocks into attempts, picks the last clean one per
  line of thought, orders them, and writes English captions (structured JSON, see llm).
  Graphics: script `onscreen` text → a `title` graphic on that beat; Claude may suggest
  more (`suggest_graphics`). Apply name fixes to all caption text.
- `apply_choice(plan, beat_id, attempt_index, alignment, speech_maps) -> Plan` — creator
  picks another attempt in the UI.

### edit/edl.py — frame-exact timeline (DONE — tests in tests/test_edl.py)
- `build_timeline(plan: Plan, fps: int) -> Timeline` — enabled beats in order; frames:
  src_in_f = floor(src_in·fps), src_out_f = ceil(src_out·fps); a segment overlapping the
  previous segment of the same take continues from its end (never replays audio); a contained
  segment is dropped; any other overlap within a take raises `TimelineError`.
- `validate_timeline(tl: Timeline) -> list[str]` — problems (empty = ok).
- `src_to_out(tl, take_id, t) -> float | None`; `entry_for_out(tl, t) -> TimelineEntry | None`;
  `beat_spans(tl) -> dict[beat_id, (out_start, out_end)]`.

### edit/captions.py — ENGLISH, word-synced (DONE — tests/test_captions.py; signature: build_cues(plan, timeline, transcripts, speech_maps, *, lang, spec=None); write_ass(cues, layout, out, *, font='Montserrat ExtraBold', font_px=None, highlight='#FFD400'))
- `build_cues(plan, timeline, transcripts, *, lang: Lang, layout_spec) -> list[CaptionCue]`
  en cut: display words = script EN words timed by their matched transcript words (mapped to
  output time; unmatched words interpolated). ar cut: the beat's `caption_en` words spread
  over the beat's speech intervals in output time, weight ∝ len(word)+2. Chunk into cues of
  ≤ `caption_max_words` / `caption_max_chars`, breaking at punctuation and pauses > 0.35 s;
  min cue 0.5 s; cues never overlap; all inside [0, timeline.duration].
- `write_ass(cues, layout: Layout, out: Path, *, font: str, highlight: str) -> Path`
  PlayRes = canvas; one Dialogue per word slice with the active word highlighted;
  `\an5\pos(cx, caption_band_center)`; outline + soft shadow; ASCII-safe escaping.
- `reshape_arabic(text) -> str` — `arabic_reshaper.reshape` only (no bidi) for the optional
  Arabic-caption path (libass lam-alef tofu bug).

### media/face.py + layout (DONE — tests/test_face.py; compute_layout returns (Layout, warnings))
- `measure_face(take: Take, proxy: Path, spec=SETTINGS.layout, canvas=(2160, 3840)) -> FaceInfo`
  Sample a frame every `face_sample_every_s` from the (mirrored) proxy; YuNet (score ≥ 0.7,
  largest face per frame); union box scaled to the canvas; head = face + clearance (top 45%
  of face height, bottom 25%, sides 30%) + `shadow_budget_px`.
- `compute_layout(faces: list[FaceInfo], spec, canvas) -> Layout` — union head over the takes
  used; IG UI margins; caption band = lowest free band above the bottom UI; graphics bands =
  remaining free y-ranges ≥ 280 px. No face found → conservative head = top 60% + warning.

### motion/hyperframes.py
- `build_inserts(plan, timeline, layout, out_dir, *, canvas, fps) -> Path | None` — one
  sub-composition per GraphicSpec from `motion/templates/*.html`, placed in a graphics band,
  explicit exit fade, internal duration == data-duration.
- `render_overlay(project_dir, out: Path, *, fps, quality, progress) -> Path` — HyperFrames
  CLI (render/ node project) → alpha video; `probe=True` renders at 6 fps first.
- `overlay_alpha_in_box(overlay: Path, box: Box, fps_sample=6) -> float` — max fraction of
  head-box pixels with alpha > threshold (face-occlusion QA).
- Fallback when HyperFrames is unavailable: `title` graphics as ASS cards (still animated
  with \fad + \move), warning in the report.

### media/render.py
- `render_segments(project, timeline, out_dir, settings, progress) -> list[Path]` — per
  entry: `-ss` (frame-exact) `-i take` `-frames:v N`, conform chain at delivery size/fps,
  audio trimmed/padded to exactly N/fps s with 4 ms fades, ProRes 422 (`prores_videotoolbox`
  profile 2, p210le; fallback `prores_ks`), PCM 48 kHz.
- `concat_segments(paths, out: Path) -> Path` — concat demuxer, stream copy.
- `final_encode(cut, out, *, captions_ass, overlay, music, settings, preview: bool, progress) -> Path`
  two-pass loudnorm (I -14, TP -1, LRA 11), optional music under voice with sidechain
  ducking, overlay then captions, `hevc_videotoolbox` main 8-bit, `-tag:v hvc1`, bt709 tags
  + `hevc_metadata` bsf to force the VUI, AAC 320k 48 kHz, `+faststart`. Preview:
  1080x1920/30 H.264.

### llm/claude.py
- `ask_json(prompt: str, schema: type[BaseModel], *, system: str | None = None, effort: str | None = None) -> BaseModel`
  Primary: `<claude_bin> -p <prompt> --output-format json --json-schema '<schema json>'
  --model claude-opus-5-5 --no-session-persistence --tools "" --strict-mcp-config`, run with
  cwd = a temp dir. VERIFIED envelope (2026-11-13, CLI 2.1.323): one JSON object
  `{"type":"result","subtype":"success","is_error":false,"result":"<json text>",
  "structured_output":{...},"total_cost_usd":0.04,...}` — read `structured_output`
  (fall back to `json.loads(result)`); error when `is_error` or `subtype != "success"`.
  ~5 s per call. `claude_bin` = `SETTINGS.claude_bin` (VS Code bundle path when not on PATH).
  Fallback when `ANTHROPIC_API_KEY` is set: Anthropic SDK `client.beta.messages.create(
  model="claude-opus-5-5", betas=["server-side-fallback-2026-07-01"], fallbacks="default",
  output_config={"effort": ..., "format": {"type": "json_schema", "schema": strict_schema}})`,
  check `stop_reason == "refusal"`, validate with the pydantic model.
- `available() -> bool`. Tasks (`llm/tasks.py`): `translate_lines`, `plan_unscripted`,
  `suggest_graphics`, `proofread_captions`.

### voice/
- `engines.py` — `class TTS(Protocol): name; def synthesize(self, text, *, lang, voice, out: Path) -> Path`
  `ElevenLabsTTS` (needs ELEVENLABS_API_KEY; Instant Voice Clone from reference clips),
  `ChatterboxTTS` (HTTP to the local Chatterbox server; zero-shot clone from a reference
  WAV; Arabic + English), `SayTTS` (macOS `say`, offline fallback: Majed ar / Samantha en).
- `reference.py` — `pick_reference(project, seconds=12) -> Path` — the cleanest continuous
  speech of his voice from the chosen attempts (no gaps, steady level), mirrored nothing
  (audio only).

### qa/
- `checks.py` — one function per check, each returns `CheckResult`:
  delivery_spec, av_duration, timeline_invariants, stutter_scan (3.5 s / 1.75 s windows,
  repeated word pairs), closed_gaps_ab, final_gap_scan, script_coverage, face_occlusion,
  captions_check (English only, Jweed spelling, inside band, no overlap), loudness,
  black_freeze, mirror_rule.
- `run.py` — `run_qa(project_id, store, settings, progress) -> QAReport` (writes report.json).

### Surfaces
- `cli.py` — `reel doctor|new|import|script|ingest|analyze|plan|approve|render|qa|voice|serve|run|list|show`.
- `api.py` — FastAPI REST + SSE (`/api/events`) + static `web/`; media served with Range support.
- `web/` — vanilla JS ES modules (functional style), no build step.
- `.claude/skills/reel-standard` (procedure), `.mcp.json` (kinocut, ElevenLabs hosted MCP,
  Higgsfield), vendored herk-edit-kit + HyperFrames skills linked for Claude Code.

## pipeline.py — the one place stages are orchestrated (CLI + API + Claude Code call these)

```python
store = ProjectStore()
create_project(store, name, *, lang="ar", mode="talking_head") -> Project
import_takes(store, pid, paths: list[str], *, lang=None, mirror=True) -> Project   # symlink into takes/, order by filename/mtime
set_script(store, pid, *, ar_text=None, en_text=None) -> Project                  # parse_script()
parse_script(ar_text, en_text) -> Script   # non-empty lines; ar[i] pairs with en[i]; [..] -> onscreen
ingest(store, pid, progress=None) -> Project
analyze(store, pid, progress=None) -> Project
plan(store, pid, progress=None, *, verify_gaps=True) -> Plan          # writes alignment.json + plan.json
choose_attempt(store, pid, beat_id, attempt_index) -> Plan
update_plan(store, pid, plan: Plan) -> Plan                           # creator edits; approved=False
approve(store, pid) -> Plan
render(store, pid, progress=None, *, preview=False) -> Path           # refuses unapproved plans
qa(store, pid, progress=None) -> QAReport
run_until_approval(store, pid, progress=None) -> Plan                 # ingest+analyze+plan
generate_voice(store, pid, *, text, lang, engine, voice="jweed") -> VoiceClip
```
Each stage sets `project.stages[stage]` running → done/failed (message = error) and marks
every later stage `stale`. Exceptions propagate (jobs.py records them).

## Web API (api.py) — JSON unless noted, all under /api, bound to 127.0.0.1

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | /health | | `{ok, version}` |
| GET | /doctor | | `{checks:[{name, ok, detail, fix}]}` |
| GET | /projects | | `[Project]` (newest first) |
| POST | /projects | `{name, lang, mode}` | `Project` |
| GET | /projects/{pid} | | `Project` |
| PATCH | /projects/{pid} | `{name?, settings?}` | `Project` |
| POST | /projects/{pid}/takes/import | `{paths:[str], lang?, mirror?}` | `Project` |
| POST | /projects/{pid}/takes/upload | multipart `files` | `Project` |
| PATCH | /projects/{pid}/takes/{tid} | `{lang?, mirror?}` | `Project` |
| DELETE | /projects/{pid}/takes/{tid} | | `Project` |
| PUT | /projects/{pid}/script | `{ar_text?, en_text?}` | `Project` |
| POST | /projects/{pid}/run/{stage} | stage ∈ ingest, analyze, plan, render, preview, qa, prepare (= run_until_approval) | `{job_id}` |
| GET | /jobs | | `[Job]` |
| GET | /jobs/{jid} | | `Job` |
| GET | /events | | SSE: `event: job` + `data: Job` on every change |
| GET | /projects/{pid}/analysis | | `{takes:[{take_id, speech:{runs,duration,digital_zero_ratio,noise_floor_db}, blocks:[{...Block, text, language}], face: FaceInfo|null}]}` |
| GET | /projects/{pid}/alignment | | `Alignment` or 404 |
| GET | /projects/{pid}/plan | | `{plan: Plan, summary: str}` or 404 |
| PUT | /projects/{pid}/plan | `Plan` | `Plan` |
| POST | /projects/{pid}/plan/choose | `{beat_id, attempt_index}` | `Plan` |
| POST | /projects/{pid}/plan/approve | | `Plan` |
| GET | /projects/{pid}/captions | | `[CaptionCue]` |
| GET | /projects/{pid}/qa | | `QAReport` (+ `passed`) or 404 |
| GET | /projects/{pid}/files/{relpath} | | file with HTTP Range (video seeking); path must stay inside the project |
| POST | /projects/{pid}/reveal | `{relpath}` | `{ok}` (macOS `open -R`) |
| GET | /voice/engines | | `[{name, available, detail, langs, clone}]` |
| POST | /projects/{pid}/voice | `{text, lang, engine, voice}` | `{job_id}` |
| GET | /projects/{pid}/voice | | `[VoiceClip]` |
| POST | /script/draft | `{topic, length}` | `{ar, en}` (Claude, reel-script-arabic house style) |

`Job = {id, project_id, kind, state: queued|running|done|failed, progress, message, error, result, created_at, started_at, finished_at}`.
One worker thread runs heavy jobs in order; progress callbacks update the Job and push SSE.
Static UI served at `/` from `web/`.

## Implementation notes (as built, 2026-11-14 — these override the contracts above where they differ)
- `motion.hyperframes.build_inserts(...) -> (list[InsertSpec], warnings)`; `render_inserts(...) -> [(clip, start)]`;
  screenshots without a real local image are skipped with a warning (never invented). Templates: `motion/templates.py`.
- `media.render.final_encode(cut, out, *, captions_ass, overlays=[(clip, start)], music, music_gain_db, settings,
  preview, progress)` runs ffmpeg in `render/` with relative `captions.ass` + `fonts/` (no filter-path quoting).
- `edit.gaps.compare_ab(w_uncut, w_cut, t_cut_a, t_cut_b)`: a word difference touching the cut (±1 word) keeps the
  pause / fails QA; differences away from the cut on identical audio are whisper re-decoding noise (reported).
- `voice.engines.TTS.synthesize(text, *, lang, voice, out, project=None, store=None)`; `voice="jweed"` clones him.
- Render writes `render/inserts.json` (graphic clips + start) and `render/render_warnings.json`; QA reads them.
- `REEL_NO_LLM=1` disables Claude calls (tests, offline).
- Layout: card box = graphics band minus 80 px margin and half the shadow budget; captions 150 px Montserrat
  ExtraBold, highlight #FFD400.
