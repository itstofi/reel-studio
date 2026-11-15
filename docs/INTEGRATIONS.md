# Integrations — what each vendored project is used for

Pinned commits: `vendor/README.md`.

| Project (license) | Used for | How | Deliberately NOT used |
|---|---|---|---|
| **HyperFrames** 0.9.41 (Apache-2.0) | every motion graphic | `render/` npm pin; one project per graphic → ProRes 4444 alpha; skills linked for Claude Code | footage inside Chrome (slow, H.264-only SDR output) |
| **herk-edit-kit** (MIT + pipeline permission) | design reference; `gsap` + `style-library` skills | skills symlinked; card ideas re-laid out for 2160x3840 | its cutter/encoder (English-only, 1080p/30, H.264), its punch-in skills, AIS brand assets |
| **kinocut** 2.0.1 (Apache-2.0) | Claude Code inspection MCP (info, frames, quality) | `.mcp.json` via `uvx` | any render/trim/export (H.264-only, no VideoToolbox, 600 s timeouts) |
| **claude-code-video-toolkit** (MIT) | `elevenlabs` + `acestep` skills for Claude Code | skills symlinked | Qwen3-TTS (no Arabic; uploads voice samples to public hosts), Remotion templates, 30 fps H.264 outputs |
| **Chatterbox** (MIT) | local Arabic/English voice clone | `services/voice` sidecar (Python 3.11, V3 weights pinned, MPS) on :8004 | in-process torch in the main app |
| **Chatterbox-TTS-Server** (MIT) | researched | — | cannot load the V3 multilingual model |
| **SILMA TTS** (MIT) | researched as an Arabic A/B candidate | — | needs conda/pynini; MSA-only |
| **kokoro-tts-mcp** (Apache-2.0) | researched | — | no Arabic, no cloning |
| **ElevenLabs** (SDK MIT; hosted MCP) | cloud voice + clones (`voice/engines.py`), MCP in Claude Code | API key in `.env`; `/mcp` login for the MCP | lip-synced dubbing over his face |
| **Pireel** (AGPL-3.0) | evaluated as a manual "polish in a CapCut-like timeline" step | not wired yet — would need a document exporter + shell patch (~1 day) | its exporter (H.264/VP9 only, sRGB canvas, no HDR, no mirroring, no RTL) |
| **editor-pro-max** (MIT; Remotion license applies) | evaluated for a future faceless/narrated mode | — | duplicates HyperFrames for our use |
| **awesome-claude-video-skills** (CC0) | index used to find the above | — | — |

## Open follow-ups
- Calibrate the HLG tonemap curve (`TONEMAP`/`NPL` in `media/conform.py`) on one real iPhone HLG take.
- Pireel round-trip export if manual timeline polishing is wanted.
- Narrated (faceless) reel mode: voice clips + full-frame HyperFrames scenes (the `Project.mode` field exists).
