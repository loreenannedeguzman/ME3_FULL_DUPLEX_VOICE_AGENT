#!/bin/sh
# Explicit opt-in; preserves .env and all accepted entrypoints. No automatic fallback.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export TTS_PROVIDER=piper
export PIPER_PYTHON="$PWD/.venv-piper/bin/python"
export PIPER_MODEL="$PWD/models/piper/en_US-ljspeech-high.onnx"
export MAX_SESSION_SECONDS="${MAX_SESSION_SECONDS:-480}"
export M3_MAX_HUMAN_TURNS="${M3_MAX_HUMAN_TURNS:-8}"
exec .venv/bin/python m4_voice_agent.py "$@"
