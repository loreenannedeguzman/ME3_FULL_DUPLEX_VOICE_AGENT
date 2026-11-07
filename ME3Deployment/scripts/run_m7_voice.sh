#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export MAX_SESSION_SECONDS="${MAX_SESSION_SECONDS:-480}"
export M3_MAX_HUMAN_TURNS="${M3_MAX_HUMAN_TURNS:-8}"
exec .venv/bin/python -m me3_m7.app "$@"
