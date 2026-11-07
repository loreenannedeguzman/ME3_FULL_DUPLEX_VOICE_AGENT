import json,os,re,time,uuid,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/m6'
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_new(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,indent=2)
def atomic(path,data):
    path=Path(path);tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(data,indent=2),encoding='utf-8');os.replace(tmp,path)
def run_path(name):
    if not re.fullmatch(r'run-[0-9]{8}-[0-9]{3}',name):raise ValueError('invalid_run_id')
    return BASE/name
def rows(path):
    if not Path(path).exists():return []
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]
def queue(folder,command):
    command={**command,'command_id':uuid.uuid4().hex}
    with (folder/'commands.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(command)+'\n');f.flush()
    until=time.monotonic()+10
    while time.monotonic()<until:
        state=read(folder/'control-state.json') if (folder/'control-state.json').exists() else {}
        if command['command_id'] in state.get('acks',{}):
            ack=state['acks'][command['command_id']]
            if ack['status']!='ok':raise ValueError(ack['reason'])
            return ack
        time.sleep(.05)
    raise TimeoutError('control_ack_timeout')
class Budget:
    def __init__(self,path,limits):self.path=path;self.limits=limits
    def reserve(self,key):
        data=read(self.path) if self.path.exists() else {k:0 for k in self.limits}
        if data[key]>=self.limits[key]:raise ValueError('m6_budget_exhausted_'+key)
        data[key]+=1;atomic(self.path,data)
        return data[key]
