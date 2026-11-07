"""Pi-owned shopping-list MCP. Atomic one-write budget; no keys or arbitrary tool paths."""
import argparse,asyncio,hmac,logging,os,platform,sqlite3,time,uuid
from pathlib import Path
from typing import Literal
from pydantic import StrictInt
from mcp.server.fastmcp import FastMCP
from speech_mcp.contracts import identity
from task_mcp.store import ShoppingStore,item_value,quantity_value,operation_value,state_digest
ROOT=Path(__file__).resolve().parents[1]
ERRORS={'invalid_item','invalid_quantity','invalid_operation','invalid_generation','invalid_request_id','operation_conflict','task_write_budget','item_already_exists','list_capacity','proposal_limit','confirmation_required','proposal_expired','proposal_missing'}

class TaskService:
    def __init__(self,store,clock=time.monotonic):self.store=store;self.clock=clock;self.proposals={}
    def prepare(self,item,quantity,operation_id):
        item=item_value(item);quantity=quantity_value(quantity);operation_value(operation_id)
        old=self.proposals.get(operation_id)
        if old:
            if old['item']!=item or old['quantity']!=quantity:raise ValueError('operation_conflict')
            return dict(old)
        if len(self.proposals)>=4:raise ValueError('proposal_limit')
        value={'operation_id':operation_id,'item':item,'quantity':quantity,'token':uuid.uuid4().hex,'expires_at':self.clock()+120}
        self.proposals[operation_id]=value;return dict(value)
    def commit(self,operation_id,token,confirmation):
        operation_value(operation_id)
        if confirmation!='confirm' or not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('confirmation_required')
        proposal=self.proposals.get(operation_id)
        if not proposal:raise ValueError('proposal_missing')
        if not hmac.compare_digest(token,proposal['token']):raise ValueError('confirmation_required')
        if self.clock()>proposal['expires_at']:raise ValueError('proposal_expired')
        return self.store.add(proposal['item'],proposal['quantity'],operation_id)

def make_server(path):
    service=TaskService(ShoppingStore(path));server=FastMCP('me3-shopping-list',log_level='CRITICAL')
    def info(generation,request_id):return {'generation':generation,'request_id':request_id,'server_pid':os.getpid(),'server_role':'task','host':platform.system(),'machine':platform.machine()}
    def perform(generation,request_id,operation):
        try:
            identity(generation,request_id);value=operation()
            return {'ok':True,**info(generation,request_id),**value}
        except Exception as exc:
            reason=exc.args[0] if type(exc) is ValueError and len(exc.args)==1 and exc.args[0] in ERRORS else 'task_storage_error' if isinstance(exc,sqlite3.Error) else 'task_internal_error'
            return {'ok':False,**info(generation,request_id),'error':reason}
    @server.tool()
    async def server_info()->dict:return {'ok':True,**info(0,'status'),'write_budget':1,'storage':'project-local SQLite','confirmation_ttl_seconds':120}
    @server.tool()
    async def prepare_item(item:str,quantity:StrictInt,operation_id:str,generation:StrictInt,request_id:str)->dict:
        """Validate and propose one item; does not insert a shopping-list row."""
        return perform(generation,request_id,lambda:service.prepare(item,quantity,operation_id))
    @server.tool()
    async def commit_item(operation_id:str,token:str,confirmation:Literal['confirm'],generation:StrictInt,request_id:str)->dict:
        """Commit ONLY an explicitly human-confirmed live proposal. Idempotent by operation ID."""
        await asyncio.sleep(0) # Last cancellable checkpoint before short atomic SQLite transaction.
        return perform(generation,request_id,lambda:service.commit(operation_id,token,confirmation))
    @server.tool()
    async def read_item(operation_id:str,generation:StrictInt,request_id:str)->dict:
        """Independent new read-only SQLite connection after a write; never echoes a proposal."""
        def read():
            row=service.store.read(operation_id)
            return {'operation_id':operation_id,'row':row,'state_digest':state_digest(row),'verification_source':'fresh_readonly_sqlite_connection'}
        return perform(generation,request_id,read)
    @server.tool()
    async def list_items(generation:StrictInt,request_id:str)->dict:
        """Read at most100 saved shopping-list rows through a fresh read-only connection."""
        return perform(generation,request_id,lambda:{'rows':service.store.list_items(),'verification_source':'fresh_readonly_sqlite_connection'})
    return server

def main():
    p=argparse.ArgumentParser();p.add_argument('--test-db');args=p.parse_args()
    path=ROOT/'data/shopping.sqlite3'
    if args.test_db:
        path=Path(args.test_db).resolve()
        if not path.is_relative_to((ROOT/'.tmp').resolve()):raise SystemExit('Test DB must stay in project .tmp')
    if not path.resolve().is_relative_to(ROOT.resolve()):raise SystemExit('Task database must remain inside project')
    logging.disable(logging.CRITICAL);make_server(path).run(transport='stdio')
if __name__=='__main__':main()
