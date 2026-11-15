# Reel Studio — project rules for Claude Code

Local editor for Jweed's (@jweed.m) Arabic/English talking-head Instagram Reels.
**Before editing any reel, load the `reel-standard` skill** — the creator's hard rules are the spec.

## Hard rules (short form — full reasoning in .claude/skills/reel-standard)
- Show the beat-by-beat plan (`reel plan`) and wait for approval before rendering.
- Mirror (hflip) every take of his footage; never mirror a finished video.
- Never put any graphic pixel (shadows included) on or near his face/head. No punch-ins / camera moves on him.
- Captions always, and always ENGLISH (the Arabic cut gets the English line). His name is **Jweed**.
- Last clean repeat of a line wins; every script line appears, in order.
- Deliver 2160x3840, 60 fps, HEVC `hvc1`, .mov, Rec.709 SDR tv range. Never downscale.
- Speech floor -52 dB, 0.25 s pads, A/B every closed gap. QA the delivered file (`reel qa`) — must pass.
- Generated voice never over his visible mouth (no lip-sync).

## How to work
- Drive the app through the `reel` CLI (`.venv/bin/reel`): doctor, new, import, script, run (ingest+analyze+plan),
  approve, render [--preview], qa, voice, narrate, record (creator reads the script: teleprompter dashboard,
  `--build` makes the reel from his takes), serve (web UI on http://127.0.0.1:8765).
- Thresholds live only in `reelstudio/config.py`; shared types in `models.py`; contracts in `DESIGN.md`.
- Tests: `.venv/bin/python -m pytest -q` (fast) and `-m slow` (real media, whisper, HyperFrames; ~3 min).
  Synthetic takes: `scripts/make_test_takes.py tests/media`.
- After a reel ships with a defect: add the check to `reelstudio/qa/` AND to the reel-standard skill.

## Tools available here
- Skills: `reel-standard`, `reel-motion` (authoring graphics), HyperFrames (`hyperframes`, `hyperframes-cli`,
  `hyperframes-registry`, `media-use`, `captions-overlay`, `motion-doctrine`, `gsap`), `style-library`
  (herk card library — 1920x1080 references, re-layout for 2160x3840 and re-token the AIS blue), `elevenlabs`,
  `acestep` (music). Global: `reel-script-arabic` for drafting scripts.
- MCP (.mcp.json): **kinocut** = inspection only (info, frames, quality checks) — never trim/merge/export with it
  (its edits are H.264-only); **elevenlabs** hosted MCP (run `/mcp` to log in); **higgsfield** (generation).
- Not linked on purpose: herk-edit-kit's cut-*/edit-video/short-form-*/video-storytelling/motion-showreel skills —
  they prescribe punch-ins and bypass this pipeline.
- How each vendored repo is used: `docs/INTEGRATIONS.md`; pinned commits: `vendor/README.md`.
