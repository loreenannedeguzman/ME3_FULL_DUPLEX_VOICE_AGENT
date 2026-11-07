"""Synthetic IPC fixture, never live acceptance."""
import json,sys,time,os
assert not os.getenv('GOOGLE_API_KEY')
index=0
for line in sys.stdin.buffer:
    q=json.loads(line);expected={k:q[k] for k in ('token','generation','epoch')};text=q['text']
    def send(data,raw=b''):
        sys.stdout.buffer.write(json.dumps({**expected,**data}).encode()+b'\n'+raw);sys.stdout.buffer.flush()
    if text=='sleep':time.sleep(20)
    if text=='die':sys.exit(2)
    if text=='identity':expected['generation']+=1
    if text=='malformed':sys.stdout.buffer.write(b'bad-json\n');sys.stdout.buffer.flush();continue
    send({'kind':'start','sample_rate':22050,'channels':1,'sample_width':2,'load_seconds':.01,'import_seconds':.01,'reuse_index':index})
    if text=='oversize':send({'kind':'pcm','byte_count':65537});continue
    if text=='truncated':send({'kind':'pcm','byte_count':4410},b'x');sys.exit(2)
    raw=bytes(4410) if text!='odd' else b'x'
    send({'kind':'pcm','byte_count':len(raw)},raw)
    send({'kind':'end','byte_count':len(raw)+(1 if text=='end-size' else 0),'chunks':1});index+=1
