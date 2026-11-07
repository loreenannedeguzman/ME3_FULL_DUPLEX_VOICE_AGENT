"""Repair duplicated dotenv assignment prefixes without printing or exporting secrets."""
from pathlib import Path
from datetime import datetime, timezone
import json, os
from dotenv import dotenv_values, set_key

root=Path(__file__).resolve().parents[1]
p=root/".env"
config=dotenv_values(p,encoding="utf-8-sig")
key=(config.get("GOOGLE_API_KEY") or "").strip()
old=key
for _ in range(8):
    for prefix in ("GOOGLE_API_KEY=", "GEMINI_API_KEY="):
        if key.startswith(prefix):
            key=key[len(prefix):].strip().strip("'\"")
            break
    else:
        break
if key==old or not key:
    print(json.dumps({"changed":False,"status":"no_safe_prefix_repair"}))
    raise SystemExit(2)
backup=root/".tmp"/("config-before-prefix-repair-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+".env")
backup.parent.mkdir(exist_ok=True)
fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"wb") as out:
    out.write(p.read_bytes())
set_key(str(p),"GOOGLE_API_KEY",key,quote_mode="always",encoding="utf-8-sig")
p.chmod(0o600)
print(json.dumps({"changed":True,"status":"duplicated_assignment_prefix_removed",
                  "backup_protected":True,"credential_disclosed":False}))
