"""Read-only entry wrapper: verify imported bytes, READY, then original child.main."""
import argparse,asyncio,hashlib,importlib,json,re,sys
from pathlib import Path
def need(v,c):
    if not v:raise ValueError(c)
async def run(a):
    need(re.fullmatch('[0-9a-f]{32}',a.nonce) and re.fullmatch('[0-9a-f]{64}',a.child_sha256) and re.fullmatch('[0-9a-f]{64}',a.upstream_sha256),'fixed_public_ready_identity_required')
    child=importlib.import_module('onit_candidate.child')
    need(hashlib.sha256(Path(child.__file__).read_bytes()).hexdigest()==a.child_sha256,'loaded_child_pin_changed')
    need(hashlib.sha256(Path(child.c.__file__).read_bytes()).hexdigest()==a.upstream_sha256,'loaded_upstream_pin_changed')
    row=dict(v=1,sequence=0,kind='native_imports_ready',nonce=a.nonce,child_sha256=a.child_sha256,upstream_sha256=a.upstream_sha256,role='qwen_floor268',request_received=False)
    sys.stdout.buffer.write((json.dumps(row,separators=(',',':'))+'\n').encode());sys.stdout.buffer.flush()
    # No init frame, model, key, schema or tool was received above. Preserve the
    # actual original child reader, model/batch validation and native final loop.
    await child.main()
def main():
    p=argparse.ArgumentParser(__doc__)
    for name in ('nonce','child-sha256','upstream-sha256'):p.add_argument('--'+name,required=True)
    asyncio.run(run(p.parse_args()))
if __name__=='__main__':main()
