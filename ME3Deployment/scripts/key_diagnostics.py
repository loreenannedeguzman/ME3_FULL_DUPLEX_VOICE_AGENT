"""Report credential-file metadata and format flags without revealing a credential."""
from datetime import datetime, timezone
from pathlib import Path
import json
from dotenv import dotenv_values
p=Path(__file__).resolve().parents[1]/".env"
c=dotenv_values(p,encoding="utf-8-sig")
key=c.get("GOOGLE_API_KEY") or ""
print(json.dumps({
 "file":str(p),"saved_at":datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat(),
 "google_key_present":bool(key),"key_character_count":len(key),
 "has_whitespace":any(ch.isspace() for ch in key),
 "has_google_standard_prefix":key.startswith("AIza"),
 "looks_like_placeholder":any(x in key.lower() for x in ("your_actual","your_api","replace_me","paste_here")),
 "is_resource_identifier":key.startswith(("projects/", "organizations/")),
 "is_url":key.startswith(("http://", "https://")),
 "has_assignment_prefix":key.startswith(("GOOGLE_API_KEY=", "GEMINI_API_KEY=")),
 "alternate_gemini_key_present":bool(c.get("GEMINI_API_KEY")),
 "model":c.get("GEMINI_MODEL")
},indent=2))
