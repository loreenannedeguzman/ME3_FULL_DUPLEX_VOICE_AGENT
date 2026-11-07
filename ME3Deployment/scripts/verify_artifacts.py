from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

root=Path(__file__).resolve().parents[1]
required=["voice_agent.py","requirements.txt","requirements-windows.lock.txt","requirements-pi.lock.txt",
          ".env.example",".gitignore","scripts/setup_pi.sh","scripts/check_pi.py",
          "scripts/provider_check.py","config/asound-pi.conf","tests/test_voice_agent.py"]
checks=[]
for rel in required:
    p=root/rel
    checks.append({"check":"artifact:"+rel,"pass":p.is_file() and p.stat().st_size>0})
broken=[]
for rel in ["README.md","PROJECT_STATUS.md","docs/architecture.md",
            "docs/requirements_traceability.md","docs/milestone1.md"]:
    p=root/rel
    text=p.read_text(encoding="utf-8-sig")
    for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)",text):
        if "://" in link or link.startswith("#"):
            continue
        if not (p.parent/link.split("#")[0]).exists():
            broken.append({"file":rel,"target":link})
checks.append({"check":"local_document_links","pass":not broken,"broken":broken})
checks.append({"check":"git_not_initialized","pass":not (root/".git").exists()})
hashes={}
for rel in required:
    if rel not in {".env.example",".gitignore"}:
        hashes[rel]=hashlib.sha256((root/rel).read_bytes()).hexdigest()
folder=root/"evidence"/"m1"
result={"timestamp":datetime.now(timezone.utc).isoformat(),"checks":checks,
        "passed":sum(c["pass"] for c in checks),"failed":sum(not c["pass"] for c in checks),
        "sha256":hashes,"scope":"Artifact consistency only; not live voice acceptance"}
output=folder/"artifact-check-002.json"
if output.exists():
    raise SystemExit("Evidence already exists; use a new output name.")
output.write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k!="sha256"},indent=2))
raise SystemExit(1 if result["failed"] else 0)
