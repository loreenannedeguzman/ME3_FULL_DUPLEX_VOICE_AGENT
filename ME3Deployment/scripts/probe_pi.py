"""Read-only target inventory. Never accepts an SSH password or disables host checks."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("target", nargs="?", default="owner@example.invalid")
parser.add_argument("--identity", type=Path)
args = parser.parse_args()
if args.target.startswith("-") or any(c.isspace() for c in args.target):
    parser.error("target must be user@hostname or user@IP, without whitespace")
remote = (
    "cat /proc/device-tree/model; printf '\\n'; cat /etc/os-release; "
    "python3 --version; command -v arecord; command -v aplay; "
    "arecord -l; aplay -l"
)
command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=yes", args.target, remote]
if args.identity:
    command[1:1] = ["-i", str(args.identity.resolve())]
result = subprocess.run(command, capture_output=True, text=True, timeout=25)
row = {
    "timestamp": datetime.now(timezone.utc).isoformat(), "command": command,
    "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    "scope": "target inventory only; successful SSH alone is not live voice acceptance",
}
path = Path(__file__).resolve().parents[1] / "evidence" / "m1"
path.mkdir(parents=True, exist_ok=True)
name = datetime.now(timezone.utc).strftime("pi-probe-%Y%m%dT%H%M%S%f.json")
(path / name).write_text(json.dumps(row, indent=2), encoding="utf-8")
print(json.dumps(row, indent=2))
raise SystemExit(0 if result.returncode == 0 else 2)
