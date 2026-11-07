import json,os,re,stat
from pathlib import Path
MODEL="qwen/qwen3.8-27b"
ROLE="qwen_floor268"
CAP=128
LEDGER_NAME="groq-qwen3.8-27b-floor268.jsonl"
def need(v,c):
    if not v:raise ValueError(c)

class RoleLedger:
    """Additional consumed-once role allowance, never a replacement quota account."""
    def __init__(self,path,*,lease=None):
        from me3_assistant.guard import lock
        self.path=Path(path);self.lease=lease or lock
        need(self.path.name==LEDGER_NAME and self.path.parent.name=='provider-admission','exact_role_ledger_path_required')
    def check_parent(self):
        for parent in self.path.parents:
            if parent.exists() or parent.is_symlink():need(stat.S_ISDIR(parent.lstat().st_mode),'private_floor_role_ledger_required')
    def rows(self):
        self.check_parent()
        if not self.path.exists():return []
        s=self.path.lstat()
        need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=16384,'invalid_floor_role_ledger')
        if os.name=='posix':need(s.st_uid==os.getuid() and s.st_mode&0o077==0,'private_floor_role_ledger_required')
        data=[json.loads(row) for row in self.path.read_bytes().splitlines()]
        need(len(data)<=3 and all(type(r) is dict for r in data),'invalid_floor_role_ledger')
        if data:
            need(set(data[0])=={'state','role','model','goal_id','declared_output_tokens'} and data[0].get('state')=='PREPARED' and data[0].get('model')==MODEL and data[0].get('role')==ROLE and data[0].get('declared_output_tokens')==CAP and type(data[0].get('goal_id')) is str and re.fullmatch('[0-9a-f]{32}',data[0]['goal_id']),'invalid_floor_role_ledger')
            need(all(r.get('goal_id')==data[0]['goal_id'] for r in data),'invalid_floor_role_ledger')
            tail=data[1:]
            if tail and tail[0].get('state')=='BOUND':
                need(set(tail[0])=={'state','goal_id','shared_ordinal'} and type(tail[0].get('shared_ordinal')) is int and tail[0]['shared_ordinal']>0,'invalid_floor_role_ledger');tail=tail[1:]
            if tail:
                row=tail[0]
                need(len(tail)==1 and set(row)=={'state','goal_id','dispatched','actual_output_tokens','unknown_output_reserved_tokens'} and row['state'] in {'COMPLETED','FAILED','INTERRUPTED'} and type(row['dispatched']) is bool and (row['actual_output_tokens'] is None or type(row['actual_output_tokens']) is int and 0<=row['actual_output_tokens']<=CAP) and row['unknown_output_reserved_tokens']==(CAP if row['dispatched'] and row['actual_output_tokens'] is None else 0),'invalid_floor_role_ledger')
        return data
    def append(self,row):
        need(not self.path.parent.is_symlink() and not self.path.is_symlink(),'private_floor_role_ledger_required')
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as f:f.write((json.dumps(row,separators=(',',':'))+'\n').encode());f.flush();os.fsync(f.fileno())
    def prepare(self,goal):
        need(type(goal) is str and re.fullmatch('[0-9a-f]{32}',goal),'floor_goal_binding_required')
        self.check_parent()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.lease(self.path.with_suffix('.lock')):
            need(not self.rows(),'floor_role_attempt_already_consumed')
            self.append(dict(state='PREPARED',role=ROLE,model=MODEL,goal_id=goal,declared_output_tokens=CAP))
    def bind(self,goal,ordinal):
        with self.lease(self.path.with_suffix('.lock')):
            rows=self.rows();need(len(rows)==1 and rows[0]['goal_id']==goal and type(ordinal) is int and ordinal>0,'floor_shared_binding_required')
            self.append(dict(state='BOUND',goal_id=goal,shared_ordinal=ordinal))
    def finish(self,goal,state,*,dispatched,actual=None):
        with self.lease(self.path.with_suffix('.lock')):
            rows=self.rows();need(rows and rows[-1]['state'] in {'PREPARED','BOUND'} and rows[0]['goal_id']==goal,'floor_role_terminal_once_required')
            need(state in {'COMPLETED','FAILED','INTERRUPTED'} and type(dispatched) is bool and (actual is None or type(actual) is int and 0<=actual<=CAP),'floor_role_terminal_fields')
            self.append(dict(state=state,goal_id=goal,dispatched=dispatched,actual_output_tokens=actual,unknown_output_reserved_tokens=CAP if dispatched and actual is None else 0))
