---
name: reel-motion
description: How to author or change a motion-graphic template for Jweed's reels (HyperFrames HTML + GSAP rendered to transparent ProRes 4444 and composited off his face). Use when adding a new graphic type, restyling cards, or debugging a graphic in Reel Studio.
---

# Reel motion graphics

Templates live in `reelstudio/motion/templates.py` (one function per template: title, list, stat, screenshot,
nametag, logo, quote; narrated scenes: photo (tag, kicker, headline, credit), chat, bars, compare, timeline; app explainers: pipeline (nodes light up as a packet travels), terminal (typed command + coloured output), code (highlight steps through lines with ok/bad notes), facebox (real face box → no-graphics zone → card slides in below)). `reelstudio/motion/hyperframes.py` writes one HyperFrames project per graphic, renders it
with `render/node_modules/.bin/hyperframes render <dir> --format mov --prores-profile 4444 --fps 60 --strict`, and
`media/render.final_encode` overlays the clip at its start time. Load the `hyperframes`, `gsap` and
`motion-doctrine` skills for the authoring details.

## Rules (creator taste + measured pitfalls)
- 2160x3840 coordinate space. The card box comes from the face layout (a graphics band below/above his head).
  Never place anything on his face or head — QA measures the clip's real alpha (shadows included) against the head box.
- Never paint `#root`, `html` or `body` (alpha output keeps a `[data-composition-id]` background as opaque).
- Every graphic moves: enter (slow scale-up + fade, 0.6–0.9 s power2/3, never a bounce), internal motion
  (rows reveal, a highlight travels, numbers count up with per-frame `tl.set`, a screenshot is built with a masked
  wipe and pans vertically inside its frame), exit (`tl.fromTo(... immediateRender:false)` at `D - 0.35`).
- Internal duration must equal `data-duration`. Tween only transform/opacity/filter; author hidden start states in
  CSS; no CSS `transform` on an element GSAP animates (lint error).
- No static highlight box over a moving page. No camera moves on his face — motion belongs to the graphics.
- Screenshots and logos must be REAL (fetch, never invent); a screenshot without a local image is skipped.
- Cards are English like the captions; Arabic text gets `dir="rtl"` and the bundled Noto Kufi Arabic font.
- herk-edit-kit's card library (`style-library` skill) is 1920x1080: re-layout for vertical and replace the
  AIS blue `#37BDF8` with our accent `#FFD400`.

## Test
```bash
.venv/bin/python -m pytest -q -m slow tests/test_motion.py   # renders a real clip, checks alpha stays off the head
```

## Photos in narrated/news reels
- Real photos only from sources whose license allows reuse (Wikimedia Commons, US-government public domain);
  never lift agency/news photos from Google Images. Pass `credit` ("Photo: author · CC BY-SA 4.0 · Wikimedia
  Commons") — CC BY/BY-SA require it. Generic airport/venue shots are tagged "file photo", never presented as
  photos of the event itself.
