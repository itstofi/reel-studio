#!/usr/bin/env bash
# Local voice sidecar (Chatterbox Multilingual V3 on Apple MPS, 127.0.0.1:8004).
#   scripts/voice.sh install   create services/voice/.venv (Python 3.11) + fetch pinned weights (~3.2 GB)
#   scripts/voice.sh start     start in the background (logs: services/voice/voice.log)
#   scripts/voice.sh stop | status | logs
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVC="$ROOT/services/voice"
PIDF="$SVC/voice.pid"
LOG="$SVC/voice.log"
PORT="${REEL_VOICE_PORT:-8004}"
export HF_HOME="$ROOT/models/hf" PKUSEG_HOME="$ROOT/models/pkuseg"
export PYTORCH_ENABLE_MPS_FALLBACK=1 PYTORCH_MPS_LOW_WATERMARK_RATIO=0.3 TOKENIZERS_PARALLELISM=false

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "${1:-status}" in
  install)
    cd "$SVC" && uv sync
    "$SVC/.venv/bin/python" -c "from huggingface_hub import snapshot_download as s; print(s('ResembleAI/chatterbox', revision='5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18', allow_patterns=['ve.pt','t3_mtl23ls_v3.safetensors','s3gen.pt','grapheme_mtl_merged_expanded_v1.json','conds.pt','Cangjie5_TC.json']))"
    echo "installed — start with: scripts/voice.sh start" ;;
  start)
    if running; then echo "already running (pid $(cat "$PIDF"))"; exit 0; fi
    nohup "$SVC/.venv/bin/reel-voice" >"$LOG" 2>&1 &
    echo $! >"$PIDF"
    for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/health" >/dev/null && { echo "voice sidecar up on :$PORT (model loads on first request)"; exit 0; }; sleep 1; done
    echo "did not come up — see $LOG"; exit 1 ;;
  stop)
    if running; then kill "$(cat "$PIDF")" && rm -f "$PIDF" && echo stopped; else echo "not running"; fi ;;
  status)
    if running && curl -fs "http://127.0.0.1:$PORT/health"; then echo; else echo "not running"; exit 1; fi ;;
  logs) tail -n 80 "$LOG" ;;
  *) echo "usage: $0 install|start|stop|status|logs"; exit 2 ;;
esac
