#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec .venv/bin/python -B -m me3_assistant.runner "$@"
