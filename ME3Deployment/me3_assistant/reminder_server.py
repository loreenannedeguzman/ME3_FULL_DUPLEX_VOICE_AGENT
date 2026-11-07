"""Student-owned local reminder MCP. Private effects require controller confirmation."""
import argparse,logging,os,secrets,time,math
from typing import Annotated,Literal
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from pydantic import StrictInt,BeforeValidator
from task_mcp.client import ROOT
from speech_mcp.contracts import identity
from me3_assistant.reminders import Store,digest,identifier
def exact_json_integer(value):
    # JSON Schema integer includes 90.0. Canonicalize only mathematically whole
    # finite numbers; StrictInt still rejects bool/string/fractional inputs.
    return int(value) if type(value) is float and math.isfinite(value) and value.is_integer() else value
JsonInt=Annotated[StrictInt,BeforeValidator(exact_json_integer)]
REJECTIONS={'explicit_time_required','timezone_offset_required','invalid_reminder_id','reminder_scheduler_not_running','invalid_reminder','choose_explicit_time_or_delay','delay_must_be_10_seconds_to_30_days','time_must_be_future_within_30_days','active_reminder_limit','cancel_requires_only_reminder_id','reminder_not_pending_cannot_undo_due_alert','unsupported_reminder_command','operation_identity_changed','reminder_history_limit','proposal_time_passed_make_fresh_request','reminder_changed_or_already_due','invalid_reminder_page','pending_limit','duplicate_proposal','invalid_confirmation'}

def server(path):
    store=Store(path);pending={};app=FastMCP('me3-local-reminders',log_level='CRITICAL')
    def perform(g,r,fn):
        identity(g,r)
        try:return {'ok':True,**fn(),'generation':g,'request_id':r,'server_role':'utility','server_pid':os.getpid()}
        except Exception as exc:return {'ok':False,'error':'reminder_request_rejected','rejection_reason':str(exc) if type(exc) is ValueError and str(exc) in REJECTIONS else 'reminder_validation_or_storage_failure','generation':g,'request_id':r,'server_role':'utility'}
    @app.tool()
    async def list_reminders(generation:StrictInt,request_id:str,offset:JsonInt=0,limit:JsonInt=8)->dict:
        """Page actual pending/due Pi alarms/reminders, IDs, UTC clock and scheduler health. Up to16 per page; use next_offset if present. GUI server must run; visible alerts and a fixed local chime through an already active speaker-AEC voice stream. Idle/text/call-held alerts remain visual; a software chime attempt does not prove human hearing. No recurring alarms or boot/power-off wake."""
        def go():
            if not 0<=offset<=31 or not 1<=limit<=16:raise ValueError('invalid_reminder_page')
            data=store.listing();rows=data['reminders'];data['reminders']=rows[offset:offset+limit];data['total']=len(rows);data['next_offset']=offset+limit if offset+limit<len(rows) else None;return data
        return perform(generation,request_id,go)
    @app.tool()
    async def prepare_reminder(command:Literal['schedule','cancel'],operation_id:str,generation:StrictInt,request_id:str,kind:Literal['alarm','reminder']|None=None,label:str|None=None,due_at:str|None=None,delay_seconds:JsonInt|None=None,reminder_id:str|None=None)->dict:
        """Propose schedule or cancel. Schedule needs alarm/reminder kind, label and either seconds from NOW or ISO8601 time with explicit timezone offset (Manila +08:00). Ask for missing/ambiguous time; never guess. Cancel needs exact listed reminder_id only. Preparation never schedules/cancels; human confirmation required."""
        def go():
            identifier(operation_id)
            for key in list(pending):
                if time.monotonic()>pending[key]['expires']:del pending[key]
            if len(pending)>=32:raise ValueError('pending_limit')
            if operation_id in pending:raise ValueError('duplicate_proposal')
            intent=store.prepare(command,kind=kind,label=label,due_at=due_at,delay_seconds=delay_seconds,reminder_id=reminder_id)
            token=secrets.token_hex(16);pending[operation_id]={'intent':intent,'token':token,'expires':time.monotonic()+120}
            return {'operation_id':operation_id,'intent':intent,'expected_digest':digest(intent),'token':token}
        return perform(generation,request_id,go)
    @app.tool()
    async def commit_reminder(operation_id:str,token:str,confirmation:str,generation:StrictInt,request_id:str)->dict:
        def go():
            p=pending.get(operation_id)
            if p is None or time.monotonic()>p['expires'] or confirmation!='confirm' or not secrets.compare_digest(token,p['token']):raise ValueError('invalid_confirmation')
            return store.commit(operation_id,p['intent'])
        return perform(generation,request_id,go)
    @app.tool()
    async def read_reminder_operation(operation_id:str,generation:StrictInt,request_id:str)->dict:
        return perform(generation,request_id,lambda:store.verify(operation_id))
    return app

def main():
    p=argparse.ArgumentParser();p.add_argument('--store',type=Path,default=ROOT/'data/reminders.sqlite3');a=p.parse_args()
    path=a.store.resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('reminder_store_outside_project')
    logging.disable(logging.CRITICAL);server(path).run(transport='stdio')
if __name__=='__main__':main()
