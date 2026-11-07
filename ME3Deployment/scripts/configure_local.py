from pathlib import Path
from dotenv import dotenv_values
import re
root = Path(__file__).resolve().parents[1]
source = root / "Duplex Project.env"
values = dotenv_values(source, encoding="utf-8-sig")
key = (values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY") or "").strip()
if not key:
    raw = source.read_text(encoding="utf-8-sig").strip()
    if re.fullmatch(r"AIza[A-Za-z0-9_-]{30,}", raw):
        key = raw
if not key:
    raise SystemExit("No nonempty GOOGLE_API_KEY found; source preserved.")
settings = {
    "GOOGLE_API_KEY": key,
    "GEMINI_MODEL": values.get("GEMINI_MODEL") or "gemini-3.8-live",
    "GEMINI_VOICE": values.get("GEMINI_VOICE") or "Aoede",
    "ALLOW_PROVIDER_AUDIO": "1",
    "MAX_SESSION_SECONDS": "90",
    "INPUT_DEVICE_INDEX": "",
    "OUTPUT_DEVICE_INDEX": "",
}
# Quote values as JSON-compatible dotenv strings; never print the secret.
import json
content = "\n".join(f"{name}={json.dumps(value)}" for name, value in settings.items()) + "\n"
(root / ".env").write_text(content, encoding="utf-8")
print("Normalized local configuration; nonempty key present. Original source preserved.")
