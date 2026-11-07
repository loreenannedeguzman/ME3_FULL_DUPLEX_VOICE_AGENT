"""Run one target check, preserving original output and native exit status."""
import json,platform
from pathlib import Path
import re
import subprocess
import os
import sys
from datetime import datetime, timezone

name, *arguments = sys.argv[1:]
if not re.fullmatch(r"[a-z0-9-]+", name):
    raise SystemExit("Unsafe check name")
root = Path(__file__).resolve().parents[1]
phase = "m6"
if phase != "m6":
    raise SystemExit("Unknown evidence phase")
folder = root / "evidence" / phase
folder.mkdir(parents=True, exist_ok=True)
log = folder / (name + ".log")
meta = folder / (name + ".json")
if log.exists() or meta.exists():
    raise SystemExit("Evidence exists; choose a new check name")
check_timeout = int(os.getenv("ME3_CHECK_TIMEOUT", "120"))
if not 10 <= check_timeout <= 720:
    raise SystemExit("Check timeout must be10..720seconds")
started = datetime.now(timezone.utc).isoformat()
command = [sys.executable, *arguments]
try:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=check_timeout)
    output = result.stdout + result.stderr
    code = result.returncode
except subprocess.TimeoutExpired as exc:
    def decode(value):
        return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
    output = decode(exc.stdout) + decode(exc.stderr) + f"\nCHECK TIMEOUT after {check_timeout}s\n"
    code = 124
log.write_text(output, encoding="utf-8")
meta.write_text(json.dumps({
    "started": started, "finished": datetime.now(timezone.utc).isoformat(),
    "command": command, "exit_code": code, "log": log.name,
    "host": platform.system(), "python": platform.python_version(),
    "scope": "Native check only; individual diagnostics are not a live conversation"
}, indent=2), encoding="utf-8")
print(output[-9000:])
print("CHECK_EXIT_CODE=" + str(code))
raise SystemExit(code)
