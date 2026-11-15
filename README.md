# Reel Studio

**An AI video editor for vertical talking-head and explainer Reels, built with Claude Code.**
Give it an idea, a script or your raw takes; it gives you a finished, quality-checked 4K Reel.

Output: **2160×3840 · 60 fps · HEVC (hvc1) · .mov · Rec.709 SDR**, with English captions timed to each word,
motion graphics that never cover your face, a clean voice track at −14 LUFS, and an automatic QA pass on the
delivered file. Everything runs locally on a Mac; cloud services (ElevenLabs, Claude) are optional.

> Built by [@jweed.m](https://instagram.com/jweed.m). The explainer reel about this app was made with this app.

---

## What it can do

| Mode | You give it | You get |
|---|---|---|
| **Edit your takes** (`reel run`) | Raw phone takes (Arabic and/or English) + the script | One clean cut: last good take of every line, silences closed, mirrored, captions and graphics |
| **Narrated / faceless** (`reel narrate`) | A script JSON (lines + graphics) | Voice-over (ElevenLabs, local Chatterbox or macOS `say`), captions, photos, animated cards |
| **Record your own voice** (`reel record`) | Your voice, read from a teleprompter in the browser | Cleaned, trimmed narration on the same narrated reel |

Typical stories:
- **News**: ask about an event, Claude checks the facts and drafts a one-minute script, real licensed photos with
  on-screen credits, numbers that count up.
- **Lessons**: explain why something broke (servers falling like dominoes, timelines, comparisons, bar charts).
- **Your own videos**: it finds every take, keeps your last clean one, cuts the silence and finds your face so
  no graphic ever covers it.

## How it works

```
script / takes
   │
   ├─ Script ........ Claude drafts or translates (uses your logged-in Claude Code, no API key needed)
   ├─ Voice ......... ElevenLabs / Chatterbox / say / your recording; Whisper listens back to every line
   │                  and re-takes or re-spells any word it did not hear correctly
   ├─ Captions ...... English, timed per word from the forced alignment
   ├─ Graphics ...... HyperFrames (HTML + GSAP) → transparent ProRes 4444, placed off the face box
   ├─ Export ........ ffmpeg → 2160×3840 60 fps HEVC hvc1 Rec.709, −14 LUFS
   └─ Quality check . spec, captions, loudness, black/frozen frames, stutters, every script word heard
```

Graphic templates (`reelstudio/motion/templates.py`): title, list, stat (count-up), photo (with credit), chat,
bars, compare, timeline, cascade (dominoes), quote, screenshot, logo, nametag, and app-explainer scenes:
**pipeline**, **terminal**, **code** (a highlight steps through the lines) and **facebox** (real face detection →
no-graphics zone).

## Requirements

- Mac with Apple Silicon, macOS 14+ (uses MLX Whisper, VideoToolbox HEVC and Apple Vision)
- [Homebrew](https://brew.sh); `scripts/setup.sh` installs ffmpeg, Node ≥ 22, uv, Python 3.12 and the rest
- ~6 GB free for models, plus ~8 GB per minute of 4K60 while a reel renders (freed after export)
- Optional: an [ElevenLabs](https://elevenlabs.io) API key, and [Claude Code](https://claude.com/claude-code) logged in

## Setup

```bash
git clone https://github.com/itstofi/reel-studio.git
cd reel-studio
scripts/setup.sh            # installs / repairs everything that is missing (safe to re-run)
scripts/setup.sh --check    # report only
cp .env.example .env        # then add ELEVENLABS_API_KEY if you want ElevenLabs voices
.venv/bin/reel doctor       # lists anything still missing, with the fix
```

Optional local voice engine (~3 GB): `scripts/voice.sh install && scripts/voice.sh start`.

## Use it

### Web app
```bash
.venv/bin/reel serve        # http://127.0.0.1:8765
```
Create a project → paste take paths → paste the script → **Prepare** (ingest, analyze, plan) → review the
beat-by-beat plan → **Render** (preview, then final) → **QA**. Deliver only when QA says PASSED.

### Edit your own takes (terminal)
```bash
R=.venv/bin/reel
ID=$($R new "My reel" --lang en)
$R import $ID ~/Movies/IMG_0001.MOV ~/Movies/IMG_0002.MOV   # originals are linked, never modified
$R script $ID --en script_en.txt                             # one spoken line per line
$R run $ID                                                   # prints the plan
$R approve $ID && $R render $ID && $R qa $ID
```

### Narrated reel from a script
```bash
.venv/bin/reel narrate "US airports ground stop" examples/us_airports_ground_stop_en.json
.venv/bin/reel qa us-airports-ground-stop
```
A script is JSON: `title`, `lang`, `engine` (`elevenlabs` | `chatterbox` | `say`), `voice` and `lines`, where
each line has `text` and optional `graphics` (`template` + `params`). See [`examples/`](examples/).
To use ElevenLabs, put any voice ID from your ElevenLabs library in `voice`; with `"engine": "say"` it runs with
no key at all.

The optional `presenter` block shows a round photo + name card. The examples use a public-domain NASA portrait
(`examples/assets/presenter/presenter.png`). Make your own cut-out with
`swift scripts/cutout.swift me.jpg examples/assets/presenter/presenter.png`.

### Record the narration yourself
```bash
.venv/bin/reel record <project-id>           # opens the teleprompter dashboard in the browser
.venv/bin/reel record <project-id> --build   # builds the reel from your takes
```
Pick your microphone, read line by line (stressed words and pauses are marked), re-record any line. The app
removes room noise and key clicks, trims silence, tightens long pauses (only if Whisper hears identical words
before and after), levels the voice, and keeps your last clean take.

## Project layout

```
reelstudio/          Python package (CLI, FastAPI server, pipeline)
  asr/               Whisper transcription + name/brand fixes
  edit/              alignment, gaps, edit decision list, captions, planner
  media/             probe, conform (HDR → SDR), face detection, render, loudness
  motion/            HyperFrames graphic templates + presenter card
  voice/             TTS engines, pronunciation check loop, your recorded takes
  qa/                checks run on the delivered file
  config.py          every threshold lives here
web/                 vanilla-JS web app (no build step)
render/              HyperFrames (npm)
examples/            ready-to-run scripts + licensed example photos (see CREDITS.json)
scripts/             setup, local voice server, test footage generator, cut-out tool
tests/               pytest suite
.claude/skills/      Claude Code skills: reel-standard (house rules), reel-motion (graphics)
DESIGN.md            module contracts
```

## Tests

```bash
.venv/bin/python -m pytest -q             # fast suite
.venv/bin/python -m pytest -q -m slow     # real media, Whisper, HyperFrames (~3 min)
.venv/bin/python scripts/make_test_takes.py tests/media   # synthetic takes for the e2e tests
```

## Working with Claude Code

Open the folder in Claude Code: `CLAUDE.md` and the skills in `.claude/skills/` carry the editing rules (plan
before render, never cover the face, captions always English, deliver 4K60 HEVC, QA must pass). `.mcp.json` adds
kinocut (inspection only), the ElevenLabs hosted MCP and Higgsfield.

## Credits and licenses

- Code: [MIT](LICENSE).
- Example photos: Wikimedia Commons, CC BY / CC BY-SA or US-government public domain. Author and license for
  each file are in `examples/assets/*/CREDITS.json`, and the reels show the credit on screen.
- Presenter sample: "Neil Armstrong pose.jpg", NASA, public domain.
- Fonts: Montserrat and Noto Kufi Arabic, SIL Open Font License (`assets/fonts/`).
- Built on [HyperFrames](https://github.com/heygen-com/hyperframes) (Apache-2.0), GSAP, ffmpeg, MLX Whisper,
  OpenCV YuNet, ElevenLabs and Chatterbox. Third-party repos are fetched by `scripts/setup.sh` at the commits
  pinned in `vendor/README.md`, under their own licenses.
