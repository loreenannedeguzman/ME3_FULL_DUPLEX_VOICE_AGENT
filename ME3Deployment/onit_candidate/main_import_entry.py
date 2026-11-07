"""MAIN-only imports READY, then unchanged original child.main; no init read early."""
import argparse,asyncio,hashlib,importlib,json,re,sys
from pathlib import Path
ROLE='main_onit_imports296'
async def run(a):
    if not re.fullmatch('[0-9a-f]{32}',a.nonce) or any(not re.fullmatch('[0-9a-f]{64}',v) for v in (a.child_sha256,a.upstream_sha256)):raise ValueError('main_ready_public_identity_required')
    child=importlib.import_module('onit_candidate.child')
    if hashlib.sha256(Path(child.__file__).read_bytes()).hexdigest()!=a.child_sha256 or hashlib.sha256(Path(child.c.__file__).read_bytes()).hexdigest()!=a.upstream_sha256:raise ValueError('main_import_source_changed')
    row=dict(v=1,sequence=0,kind='native_imports_ready',nonce=a.nonce,child_sha256=a.child_sha256,upstream_sha256=a.upstream_sha256,role=ROLE,request_received=False)
    sys.stdout.buffer.write((json.dumps(row,separators=(',',':'))+'\n').encode());sys.stdout.buffer.flush()
    await child.main()
def main():
    p=argparse.ArgumentParser(__doc__)
    for key in ('nonce','child-sha256','upstream-sha256'):p.add_argument('--'+key,required=True)
    asyncio.run(run(p.parse_args()))
if __name__=='__main__':main()
