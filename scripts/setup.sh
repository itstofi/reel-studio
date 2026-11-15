#!/usr/bin/env bash
# Reel Studio setup — idempotent. Safe to run any number of times; it only fetches what is missing.
#
#   scripts/setup.sh            install / repair everything
#   scripts/setup.sh --check    report only (changes nothing); exit 1 if a required piece is missing
#   scripts/setup.sh --voice    also install the local Chatterbox voice engine (scripts/voice.sh install)
#
# Steps: brew tools (ffmpeg, node>=22, uv) -> uv sync -> npm ci in render/ -> HyperFrames browser
# -> YuNet face model (sha256) -> whisper models -> vendor repos at pinned commits -> Claude Code
# skill symlinks -> kinocut (uvx cache) -> .env from .env.example -> optional voice engine.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

CHECK=0
VOICE=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --voice) VOICE=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# Homebrew + uv tools live outside the default non-login PATH on some shells.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# HyperFrames: no telemetry, no self-update, no surprise installs.
export HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_AUTO_INSTALL=1 HYPERFRAMES_SKIP_SKILLS=1

FAILS=0
ok()   { printf '  OK    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
step() { printf '\n== %s\n' "$*"; }
doing() { printf '  ...   %s\n' "$*"; }

# ── Pins ─────────────────────────────────────────────────────────────────────
YUNET_FILE="models/face_detection_yunet_2023mar.onnx"
YUNET_SHA="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
YUNET_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/eb9f4f2d4b79b58e5513b5d25d5f0243a8357bf4/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_MIRROR="https://huggingface.co/opencv/face_detection_yunet/resolve/3cc26e7f1014a5ee5d74a42acee58bafc9d0a310/face_detection_yunet_2023mar.onnx"
WHISPER_MODELS="mlx-community/whisper-large-v3-mlx mlx-community/whisper-large-v3-turbo"
HF_HUB="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
HYPERFRAMES_VERSION="0.9.41"
KINOCUT_PIN="kinocut==2.0.1"

# folder|upstream|full commit (matches the short commits in vendor/README.md)
VENDOR_PINS="
herk-edit-kit|https://github.com/nateherkai/herk-edit-kit.git|10511e5af86fc47756ea89803b397422e2d89284
kinocut|https://github.com/KyaniteLabs/kinocut.git|caa8b86bb26fcb92c5a2993fcfacd309c2c5b0cf
hyperframes|https://github.com/heygen-com/hyperframes.git|d208281091b1c00db1724a2ee3db0055955eb41b
claude-code-video-toolkit|https://github.com/digitalsamba/claude-code-video-toolkit.git|9b0c90d421d2ab32ce8e7522494bddfe185f8aa4
pireel|https://github.com/pireel/pireel.git|4c0f428df5bab8a53d237d5a6ca1b9b67511a06c
editor-pro-max|https://github.com/Hainrixz/editor-pro-max.git|ceec8084f651f4b5b7a8b3b302dd6d0da1cd0a2d
chatterbox|https://github.com/resemble-ai/chatterbox.git|ab55923c27757c90cfcc407baa57dc25a7d25217
Chatterbox-TTS-Server|https://github.com/devnen/Chatterbox-TTS-Server.git|915ae289340e10c6047f27f47e22eae9bf350c32
silma-tts|https://github.com/SILMA-AI/silma-tts.git|2816b5a548c3ccdeea389ebc2db2d246a25d01fd
kokoro-tts-mcp|https://github.com/mberg/kokoro-tts-mcp.git|88c05b5e31ed8096a7268b7296f4019cc288aaa0
awesome-claude-video-skills|https://github.com/zhuyansen/awesome-claude-video-skills.git|b4636b9ef9a0379137752133bd3540908074498d
"

# .claude/skills/<name> -> vendor path. herk's cut-*, edit-video, short-form-*, video-storytelling and
# motion-showreel are deliberately NOT linked: they prescribe punch-ins or bypass the Reel Studio pipeline.
SKILL_LINKS="
hyperframes|vendor/hyperframes/skills/hyperframes
hyperframes-cli|vendor/hyperframes/skills/hyperframes-cli
hyperframes-registry|vendor/hyperframes/skills/hyperframes-registry
media-use|vendor/hyperframes/skills/media-use
captions-overlay|vendor/hyperframes/.agents/skills/captions-overlay
motion-doctrine|vendor/hyperframes/.agents/skills/motion-doctrine
gsap|vendor/herk-edit-kit/.claude/skills/gsap
style-library|vendor/herk-edit-kit/.claude/skills/style-library
elevenlabs|vendor/claude-code-video-toolkit/.claude/skills/elevenlabs
acestep|vendor/claude-code-video-toolkit/.claude/skills/acestep
"

# ── 1. Homebrew tools ────────────────────────────────────────────────────────
brew_install() {  # brew_install <formula>
  if [ "$CHECK" = 1 ]; then return 1; fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "  Homebrew is missing: install it from https://brew.sh, then re-run." >&2
    return 1
  fi
  doing "brew install $1"
  brew install "$1"
}

step "Tools"
if ! command -v ffmpeg >/dev/null 2>&1; then brew_install ffmpeg || true; fi
if command -v ffmpeg >/dev/null 2>&1; then
  ENC="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
  FIL="$(ffmpeg -hide_banner -filters 2>/dev/null)"
  missing=""
  for e in hevc_videotoolbox prores_videotoolbox; do echo "$ENC" | grep -q " $e " || missing="$missing $e"; done
  for f in zscale tonemap ass loudnorm ebur128; do echo "$FIL" | grep -q " $f " || missing="$missing $f"; done
  if [ -z "$missing" ]; then ok "ffmpeg $(ffmpeg -hide_banner -version | head -1 | awk '{print $3}') ($(command -v ffmpeg))"
  else bad "ffmpeg lacks:$missing — install a full build (brew install ffmpeg) and put it first on PATH"; fi
else
  bad "ffmpeg not found (brew install ffmpeg)"
fi

node_major() { node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
if ! command -v node >/dev/null 2>&1 || [ "$(node_major)" -lt 22 ]; then brew_install node || true; fi
if command -v node >/dev/null 2>&1 && [ "$(node_major)" -ge 22 ]; then ok "node $(node --version)"
else bad "node >= 22 required (brew install node)"; fi

if ! command -v uv >/dev/null 2>&1; then brew_install uv || true; fi
if command -v uv >/dev/null 2>&1; then ok "$(uv --version)"; else bad "uv not found (brew install uv)"; fi
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')" || bad "git not found (xcode-select --install)"

# ── 2. Python environment ────────────────────────────────────────────────────
step "Python (.venv, uv.lock)"
if [ "$CHECK" = 0 ] && command -v uv >/dev/null 2>&1; then
  # --inexact: never uninstall extras someone added by hand; --frozen: install exactly uv.lock.
  doing "uv sync --frozen --inexact"
  uv sync --frozen --inexact --quiet || bad "uv sync failed"
fi
if [ -x .venv/bin/python ] && .venv/bin/python -c "import reelstudio, fastapi, numpy, cv2, rapidfuzz, arabic_reshaper, mlx_whisper" 2>/dev/null; then
  ok "venv $(.venv/bin/python --version 2>&1) with reelstudio + deps"
else
  bad "venv missing or incomplete (run scripts/setup.sh)"
fi

# ── 3. HyperFrames (render/ Node project) ────────────────────────────────────
step "HyperFrames (render/)"
hf_version() { node -p "require('./render/node_modules/hyperframes/package.json').version" 2>/dev/null || echo none; }
if [ "$(hf_version)" != "$HYPERFRAMES_VERSION" ] && [ "$CHECK" = 0 ]; then
  doing "npm ci in render/"
  (cd render && npm ci --no-audit --no-fund) || bad "npm ci failed in render/"
fi
if [ "$(hf_version)" = "$HYPERFRAMES_VERSION" ]; then ok "hyperframes $HYPERFRAMES_VERSION"
else bad "hyperframes $HYPERFRAMES_VERSION not installed in render/ (have: $(hf_version))"; fi

HF_BIN="render/node_modules/.bin/hyperframes"
if [ -x "$HF_BIN" ]; then
  if [ "$CHECK" = 0 ]; then
    doing "hyperframes browser ensure"
    "$HF_BIN" browser ensure </dev/null >/dev/null 2>&1 || warn "hyperframes browser ensure reported an error"
  fi
  BROWSER="$("$HF_BIN" browser path </dev/null 2>/dev/null | tail -1)"
  if [ -n "$BROWSER" ] && [ -e "$BROWSER" ]; then ok "render browser: $BROWSER"
  else bad "no render browser (run: $HF_BIN browser ensure)"; fi
fi

# ── 4. Models ────────────────────────────────────────────────────────────────
step "Models"
sha_of() { shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'; }
if [ "$(sha_of "$YUNET_FILE")" != "$YUNET_SHA" ] && [ "$CHECK" = 0 ]; then
  mkdir -p models
  doing "downloading YuNet face model"
  curl -fsSL -o "$YUNET_FILE.part" "$YUNET_URL" || curl -fsSL -o "$YUNET_FILE.part" "$YUNET_MIRROR"
  if [ "$(sha_of "$YUNET_FILE.part")" = "$YUNET_SHA" ]; then mv "$YUNET_FILE.part" "$YUNET_FILE"
  else rm -f "$YUNET_FILE.part"; fi
fi
if [ "$(sha_of "$YUNET_FILE")" = "$YUNET_SHA" ]; then ok "YuNet 2023mar (sha256 verified)"
else bad "YuNet model missing or wrong sha256: $YUNET_FILE"; fi

hf_present() {  # a snapshot with weights exists in the HF cache
  local dir="$HF_HUB/models--$(echo "$1" | sed 's#/#--#')/snapshots"
  local f
  for f in "$dir"/*/weights.* "$dir"/*/*.safetensors "$dir"/*/*.npz; do
    [ -e "$f" ] && return 0
  done
  return 1
}
for repo in $WHISPER_MODELS; do
  if ! hf_present "$repo" && [ "$CHECK" = 0 ] && [ -x .venv/bin/python ]; then
    doing "downloading $repo (a few GB, once)"
    .venv/bin/python -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])" "$repo" \
      || warn "download of $repo failed"
  fi
  if hf_present "$repo"; then ok "whisper $repo"
  elif [ "$repo" = "mlx-community/whisper-large-v3-mlx" ]; then bad "whisper $repo missing (default ASR model)"
  else warn "whisper $repo missing (optional)"; fi
done

# ── 5. Vendor repos at pinned commits ────────────────────────────────────────
step "Vendor repos (vendor/README.md)"
clone_pinned() {  # clone_pinned <dir> <url> <sha>
  local dir="$1" url="$2" sha="$3"
  # LFS filters off: hyperframes keeps only test baselines in LFS, and git-lfs is not needed.
  local lfs="-c filter.lfs.process= -c filter.lfs.smudge= -c filter.lfs.required=false"
  rm -rf "$dir.tmp" && mkdir -p "$dir.tmp" || return 1
  # shellcheck disable=SC2086
  ( cd "$dir.tmp" && git init -q && git remote add origin "$url" \
      && git $lfs fetch -q --depth 1 origin "$sha" && git $lfs checkout -q FETCH_HEAD ) \
    && mv "$dir.tmp" "$dir"
}
echo "$VENDOR_PINS" | while IFS='|' read -r name url sha; do
  [ -n "$name" ] || continue
  dir="vendor/$name"
  if [ ! -d "$dir" ] && [ "$CHECK" = 0 ]; then
    doing "cloning $name @ ${sha:0:12}"
    clone_pinned "$dir" "$url" "$sha" || { rm -rf "$dir.tmp"; echo "  FAIL  clone $name"; }
  fi
  if [ -d "$dir" ]; then
    head="$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo unknown)"
    if [ "$head" = "$sha" ]; then ok "$name @ ${sha:0:12}"
    else echo "  WARN  $name is at ${head:0:12}, pinned ${sha:0:12} (left as is)"; fi
  else
    echo "  FAIL  vendor/$name missing"
  fi
done > "${TMPDIR:-/tmp}/reel_vendor_check.$$"
cat "${TMPDIR:-/tmp}/reel_vendor_check.$$"
grep -q "FAIL" "${TMPDIR:-/tmp}/reel_vendor_check.$$" && FAILS=$((FAILS + 1))
rm -f "${TMPDIR:-/tmp}/reel_vendor_check.$$"
# (the loop runs in a subshell, so FAIL lines are counted from its output)

# ── 6. Claude Code skill symlinks ────────────────────────────────────────────
step "Claude Code skills (.claude/skills)"
mkdir -p .claude/skills 2>/dev/null || true
for row in $SKILL_LINKS; do
  name="${row%%|*}"; target="${row#*|}"
  link=".claude/skills/$name"
  if [ "$CHECK" = 0 ] && [ -d "$target" ]; then
    if [ -e "$link" ] && [ ! -L "$link" ]; then warn "$link is a real folder; not replacing it"
    else ln -sfn "../../$target" "$link"; fi
  fi
  if [ -f "$link/SKILL.md" ]; then ok "$name -> $target"; else bad "skill link $link does not resolve"; fi
done
[ -f .claude/skills/reel-standard/SKILL.md ] && ok "reel-standard (tracked)" || bad ".claude/skills/reel-standard/SKILL.md missing"

# ── 7. kinocut (inspection-only MCP sidecar, run through uvx) ────────────────
step "kinocut ($KINOCUT_PIN via uvx)"
if command -v uvx >/dev/null 2>&1; then
  if [ "$CHECK" = 0 ]; then
    # stdout piped: kinocut 2.0.1 hangs on a real TTY. First run fills the uv cache.
    if (cd "${TMPDIR:-/tmp}" && uvx --python 3.12 --from "$KINOCUT_PIN" kino --version </dev/null 2>/dev/null | cat) | grep -q "2.0.1"
    then ok "kinocut 2.0.1"; else warn "kinocut could not start (needs network once)"; fi
  else
    ok "uvx present (kinocut starts on demand)"
  fi
else
  warn "uvx not found: the kinocut MCP server will not start"
fi

# ── 8. .env ──────────────────────────────────────────────────────────────────
step "Settings (.env)"
if [ ! -f .env ] && [ -f .env.example ] && [ "$CHECK" = 0 ]; then
  cp .env.example .env && chmod 600 .env && doing "created .env from .env.example (add ELEVENLABS_API_KEY if you use ElevenLabs)"
fi
[ -f .env ] && ok ".env present" || warn ".env missing (copy .env.example to .env)"

# ── 9. Optional local voice engine ───────────────────────────────────────────
if [ "$VOICE" = 1 ]; then
  step "Local voice engine (Chatterbox)"
  if [ -x scripts/voice.sh ] || [ -f scripts/voice.sh ]; then
    if [ "$CHECK" = 1 ]; then bash scripts/voice.sh status || warn "voice engine not ready"
    else bash scripts/voice.sh install || bad "scripts/voice.sh install failed"; fi
  else
    warn "scripts/voice.sh not found"
  fi
fi

echo
if [ "$FAILS" -gt 0 ]; then
  echo "Setup incomplete: $FAILS required item(s) failed."
  [ "$CHECK" = 1 ] && echo "Run scripts/setup.sh (without --check) to fix."
  exit 1
fi
echo "Reel Studio is ready. Start with: uv run reel doctor   |   uv run reel serve"
exit 0
