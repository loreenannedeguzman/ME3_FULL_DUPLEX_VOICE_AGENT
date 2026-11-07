"""Read provider model metadata only; no audio or generation request."""
from pathlib import Path
import json
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / ".env", encoding="utf-8-sig")
key = config.get("GOOGLE_API_KEY")
model = config.get("GEMINI_MODEL")
if not key or not model:
    print(json.dumps({"key_present": bool(key), "model_configured": bool(model),
                      "status": "missing_configuration"}))
    raise SystemExit(2)
from google import genai
client = genai.Client(api_key=key)
try:
    result = client.models.get(model=model)
    print(json.dumps({"status": "model_metadata_accessible", "model": result.name,
                      "audio_uploaded": False, "generation_requested": False}))
except Exception as exc:
    message = str(getattr(exc, "message", "")).replace(key, "[REDACTED]")
    import re
    message = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", message)[:350]
    print(json.dumps({"message": message, "status": "metadata_request_failed", "error_type": type(exc).__name__,
                      "code": getattr(exc, "code", None),
                      "audio_uploaded": False, "generation_requested": False}))
    raise SystemExit(2)
finally:
    client.close()
