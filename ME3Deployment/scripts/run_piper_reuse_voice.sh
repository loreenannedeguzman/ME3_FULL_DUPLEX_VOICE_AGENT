#!/bin/sh
# Explicit reuse opt-in. Original accepted launch remains available unchanged.
set -eu
export PIPER_WORKER_MODE=resident
exec sh "$(dirname -- "$0")/run_piper_voice.sh" "$@"
