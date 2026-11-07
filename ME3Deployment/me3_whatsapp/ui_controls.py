"""Inactive communication UI kernels; host/Onit/MCP binding still requires qualification."""
import asyncio,hashlib,json,re,secrets,time
from dataclasses import dataclass
from me3_whatsapp.guard import Rejected
NAVIGATION={'Calls','Chats','New chat'}
async def navigate(backend,label,timeout=8,clock=time.monotonic,sleep=asyncio.sleep):
 if label not in NAVIGATION:raise Rejected('navigation_only_not_call_or_send')
 nonce=secrets.token_hex(16);deadline=clock()+timeout;ready=False
 while clock()<deadline:
  backend.check()
  source=r'''(()=>{if(location.origin!=='https://web.whatsapp.com'||!document.querySelector('#pane-side'))return {count:0};const a=Array.from(document.querySelectorAll('button,[role="button"],[role="tab"]')).filter(e=>e.getAttribute('aria-label')===LABEL&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0);if(a.length!==1)return {count:a.length};const e=a[0];if(e.disabled||e.getAttribute('aria-disabled')==='true')return {count:1,ready:false};const same=window.__me3Navigation?.nonce===NONCE&&window.__me3Navigation.element===e;window.__me3Navigation={nonce:NONCE,element:e,label:LABEL};return {count:1,ready:same}})()'''
  value=await backend.evaluate(source.replace('LABEL',json.dumps(label)).replace('NONCE',json.dumps(nonce)))
  if value.get('count',0)>1:raise Rejected('ambiguous_navigation_control')
  if value.get('ready'):ready=True;break
  await sleep(.1)
 if not ready:raise Rejected('navigation_control_not_ready')
 backend.check()
 source=r'''(()=>{const d=window.__me3Navigation;if(location.origin!=='https://web.whatsapp.com'||!d||d.nonce!==NONCE||d.label!==LABEL||!d.element.isConnected||d.element.getAttribute('aria-label')!==LABEL||d.element.disabled||d.element.getAttribute('aria-disabled')==='true')return false;const r=d.element.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);if(r.width<=0||r.height<=0||!hit||!d.element.contains(hit))return false;delete window.__me3Navigation;d.element.click();return true})()'''
 if await backend.evaluate(source.replace('LABEL',json.dumps(label)).replace('NONCE',json.dumps(nonce))) is not True:raise Rejected('navigation_changed_no_retry')
 return dict(navigation=label,one_click=True,call_started=False,message_sent=False)

@dataclass(frozen=True)
class CloseApproval:
 operation_id:str
 target_id:str
 owner:str
 generation:int
 direct_current_user_command:bool

def approval_digest(approval):
 if not approval.direct_current_user_command:raise Rejected('explicit_close_command_required')
 if not re.fullmatch('[0-9a-f]{32}',approval.operation_id) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',approval.owner) or type(approval.generation) is not int or approval.generation<1:raise Rejected('host_close_identity_required')
 if not isinstance(approval.target_id,str) or not approval.target_id or len(approval.target_id)>128:raise Rejected('owned_target_required')
 return hashlib.sha256(json.dumps(dict(target=approval.target_id,owner=approval.owner,generation=approval.generation,command='close_owned_WhatsApp_window'),sort_keys=True).encode()).hexdigest()
def close_binding(snapshot,approval):
 expected=approval_digest(approval)
 if snapshot.get('target_id')!=approval.target_id or snapshot.get('official') is not True or snapshot.get('owned_profile') is not True or snapshot.get('linked') is not True:raise Rejected('owned_official_window_required')
 if snapshot.get('draft_empty') is not True:raise Rejected('existing_draft_preserved')
 if snapshot.get('active_call') is not False or snapshot.get('unknown_dialog') is not False:raise Rejected('call_or_dialog_state_requires_resolution')
 return expected

class CloseGuard:
 """Single owned Page.close only; no process kill, Browser.close or GUI shutdown."""
 def __init__(self,path,page,snapshot,observe_closed,current):
  from me3_assistant.actions import Journal
  self.journal=Journal(path);self.page=page;self.snapshot=snapshot;self.observe_closed=observe_closed;self.current=current;self.mutex=asyncio.Lock()
 def check(self,approval):
  if self.current(approval.owner,approval.generation) is not True:raise asyncio.CancelledError()
 async def close(self,approval):
  async with self.mutex:
   expected=approval_digest(approval);old=self.journal.records.get(approval.operation_id)
   if old:
    if old['expected_digest']!=expected:raise Rejected('close_identity_changed')
    return dict(state=old['state'],write_retried=False)
   if self.journal.unresolved():raise Rejected('previous_close_requires_readback')
   self.check(approval);expected=close_binding(await self.snapshot(),approval);self.check(approval)
   self.journal.record(approval.operation_id,'close_owned_whatsapp_window','PROPOSED',expected)
   # Re-read target/draft/call state immediately before the one close dispatch.
   try:
    if close_binding(await self.snapshot(),approval)!=expected:raise Rejected('window_binding_changed')
    self.check(approval)
   except BaseException:
    self.journal.record(approval.operation_id,'close_owned_whatsapp_window','CANCELED',expected);raise
   self.journal.record(approval.operation_id,'close_owned_whatsapp_window','DISPATCHED',expected)
   try:
    try:await self.page.call('Page.close')
    except asyncio.CancelledError:raise
    except Exception:pass # A target may close before its command reply arrives.
    self.check(approval)
    verified=await self.observe_closed(approval.target_id)
    state='VERIFIED' if verified is True else 'UNCERTAIN';self.journal.record(approval.operation_id,'close_owned_whatsapp_window',state,expected)
    return dict(state=state,write_retried=False,conversation_stop_requested=False)
   except BaseException:
    self.journal.record(approval.operation_id,'close_owned_whatsapp_window','UNCERTAIN',expected);raise
