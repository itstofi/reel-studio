---
name: reel-standard
description: The house procedure for building one of Jweed's talking-head Instagram Reels with Reel Studio — ingest, analyze, plan (shown before executing), cut, English captions, HyperFrames graphics off the face, 4K60 HEVC Rec.709 delivery and adversarial QA. Use for ANY request to edit, cut, caption, re-render or QA a reel in this project, including "edit this video", "make a reel from these takes", "fix the reel", "re-export", "add captions/graphics".
---

# Reel Standard

**Standing rule:** after every reel, record what broke and add the check to this
procedure (and to `reelstudio/qa/`). Every rule below replaced a step that had
already shipped a broken reel. Thresholds live in `reelstudio/config.py` — never
hard-code different numbers.

## Hard rules (the creator has had to repeat these)

1. **Show the plan before executing.** Run `reel plan`, show the beat-by-beat plan,
   wait for approval (`reel approve`). Never render an unapproved plan.
2. **Mirror every take** (`hflip`) of his footage, in the cut chain only — never on a
   finished video (that mirrors captions and titles). Re-measure the face after flipping.
3. **Never cover his face or head.** Measure the face box per take (YuNet). The whole
   head region plus clearance is off-limits — including `box-shadow` alpha, which
   reaches ~60 px below a card at 1080p.
4. **Captions always, and ALWAYS ENGLISH** — the Arabic cut carries English subtitles
   (a translation of the line), never an Arabic transcript. Same styling either way.
5. **His name is Jweed** (j-w-e-e-d). Whisper writes "Juaid"/"Juwaid" — fix it in every
   caption and card without being asked.
6. **Last repeat wins.** He re-records a line until it is right: the LAST clean attempt
   is the keeper. **Full script coverage, in script order** — every line must appear.
7. **Deliver 2160x3840, 60 fps, HEVC `hvc1`, `.mov`, Rec.709 SDR, tv range.** Never
   downscale (a 1080p/30 export was rejected as "bad quality"). HLG / Dolby Vision
   takes are converted to SDR before grading — HLG treated as SDR looks flat and grey.
8. **No camera moves on his face** (punch-ins read as shake). All motion belongs to the
   graphics, and every graphic moves: enter, internal motion, exit. A screenshot must be
   *built* on screen (rows reveal, highlights travel, numbers count), never a still.
9. **Verify claims against the real source** before building a graphic about them;
   report mismatches instead of papering over them. Fetch real logos/screenshots;
   generate only what has no source.

## Audio rules (his mic hard-gates pauses to digital zero)

- Speech floor **-52 dB**, never -38 (his speech runs -39..-46 dB; -38 deleted words).
- Cuts come from the **silence map**, not whisper word times (whisper stretches words
  across dropouts). Blocks come from the silence map, then each block is
  **isolate-transcribed** — never group takes from whisper's segment text.
- Pad **0.25 s** each side of every cut (0.15 s clipped "engineers" to "engines").
- Only a **long gap in the MIDDLE of a phrase** is a defect. Digital silence at a pause is
  the hardware working normally.
- **A/B every closed gap**: transcribe the uncut and cut spans and compare words. Never
  dismiss a word-level difference — a missing word looks exactly like a mishearing.
  Prefer keeping a long pause over risking a word.

## THE BUILD

```bash
reel doctor                                   # tools, models, integrations ready?
reel new "Claude weapons reel" --lang ar      # project per language cut
reel import <id> ~/Movies/IMG_2210.MOV ...    # originals are symlinked, never modified
reel script <id> --ar script_ar.txt --en script_en.txt   # line-aligned; [..] = on-screen text
reel ingest <id>     # 1-3: probe, detect HLG/DV, extract audio, conformed proxy (mirrored, SDR)
reel analyze <id>    # 4: speech map (-52 dB), blocks, isolate-transcribe (large-v3), face box
reel plan <id>       # 5: align to script (last repeat wins), gap audit + A/B, beats -> SHOW IT
reel approve <id>    # 6: only after the creator says yes
reel render <id> --preview   # 7: fast 1080p check render (graphics probe at 6 fps)
reel render <id>     # 8-10: frame-accurate cut, English captions, HyperFrames inserts,
                     #       loudness, 4K60 HEVC hvc1 Rec.709 encode
reel qa <id>         # 11: adversarial QA on the DELIVERED file — must pass
                     # 12: deliver; record anything that broke in this skill
```

The web UI (`reel serve`, http://127.0.0.1:8765) runs the same stages with buttons.

## QA must be adversarial (`reel qa`)

A check that cannot see the failure is not verification. On the delivered file:

1. **Segments never overlap** (`segs[i+1].src_in >= segs[i].src_out` within a take) —
   overlapped audio plays twice ("what went what went wrong"). Overlaps are merged.
2. **Stutter scan** in **3.5 s windows stepping 1.75 s**; flag any repeated word-pair.
3. **Re-cutting invalidates every downstream timing.** Any change to the plan
   regenerates timeline, captions and graphics in the same breath; every beat must end
   inside the cut. An A/V duration mismatch means the project and cut diverged.
4. **A/B transcript diff** on every closed gap; mid-phrase gap scan on the final audio.
5. **Delivery spec probe** (size, fps, codec, `hvc1`, bt709 tags, tv range) — note
   `hevc_videotoolbox` passes source colour tags through, so the encode rewrites the VUI.
6. **Face occlusion** of the graphics overlay, shadows included, must be zero.
7. **Generated voice is heard back** (`voice/pronounce.py`). Shipped defect: the TTS said
   "AI" as "I"/"eye" and doubled "look". Every generated line is transcribed and aligned
   to the script; any word missing, misheard or repeated → regenerate (new seed +
   phonetic respelling from `PronounceSpec.respell`, captions keep the real spelling),
   best of 4 tries. QA `pronunciation_lines` + `pronunciation_final` (whole delivered
   audio vs script) must pass. Never spell out times like "3:54 PM" in the spoken line —
   put them on a graphic. Arabic/Iraqi lines: numbers may be spoken as words (the check maps them to
   digits), brand names may be written in Arabic letters (add new ones to `PronounceSpec.aliases`).

## His own voice on a narrated reel (`reel record`)

When he says "let's record the audio for <video>": run `reel record <project>` (opens the record studio; an
AI-voice project gets a "(my voice)" copy). He reads line by line; every take is cleaned and heard back —
any missing / misheard / repeated word means retake, last clean take wins. When every line is green:
`reel record <pid> --build` (or the Build button), then show `reel qa`. Never "fix" a flagged take by editing
words out of it; ask for a retake.

## HyperFrames pitfalls (graphics)

- A clip does not fade out on its own — every sub-composition needs an explicit exit
  tween on the element carrying `data-composition-id`.
- A sub-composition's internal duration must equal its `data-duration`; fork the
  project per language cut instead of sharing one.
- A `transform` on a full-frame `<video>` stalls capture — pin clips at `left:0; top:0`
  at exact canvas size. Probe long renders at `--fps 6` first.

## Apple-style motion (when asked)

Slow scale-up + fade on enter (never a bounce); a screenshot pans vertically inside
its frame like the page scrolling; a spec line gets a pinned highlight while the rest
moves; exit on a crossfade. Long eases (0.6-0.9 s, power2/3).
