"""Dedicated Pi calling MCP. All lifecycle endpoints are host private."""
import asyncio,logging
from pydantic import StrictInt
from mcp.server.fastmcp import FastMCP
from speech_mcp.contracts import identity

def make_server(service):
    app=FastMCP('ME3 Calling',log_level='CRITICAL');mutex=asyncio.Lock()
    async def perform(g,r,fn):
        identity(g,r)
        async with mutex:
            try:return dict(ok=True,**(await fn()),generation=g,request_id=r,server_role='utility')
            except asyncio.CancelledError:raise
            except Exception:return dict(ok=False,error='call_unavailable_or_outcome_unverified',generation=g,request_id=r,server_role='utility')
    @app.tool()
    async def abort_call(proof:dict,generation:StrictInt,request_id:str)->dict:
        """Private closed-voice host cancellation, only when no dispatch ever occurred."""
        async def abort():return service.abort_before_dispatch(proof)
        return await perform(generation,request_id,abort)
    @app.tool()
    async def prepare_call(operation_id:str,selection_id:str,host_owner:str,host_goal:str,current_user_request:str,generation:StrictInt,request_id:str)->dict:
        """Private prepare only; never touches browser, microphone or dial controls."""
        return await perform(generation,request_id,lambda:service.prepare(operation_id,selection_id,host_owner,host_goal,current_user_request,generation))
    @app.tool()
    async def commit_call(proof:dict,generation:StrictInt,request_id:str)->dict:
        """Private signed exact-review+separate-confirmation+closed-voice commit."""
        return await perform(generation,request_id,lambda:service.commit(proof))
    @app.tool()
    async def read_call(operation_id:str,generation:StrictInt,request_id:str)->dict:
        """Private durable scoped outcome only; no dial, browser mutation or audio."""
        async def read():return service.public(operation_id)
        return await perform(generation,request_id,read)
    @app.tool()
    async def reconcile_call(proof:dict,gui_request:dict,generation:StrictInt,request_id:str)->dict:
        """Private authenticated GUI status check after End; never dials/closes/retries."""
        return await perform(generation,request_id,lambda:service.reconcile(proof,gui_request))
    @app.tool()
    async def end_call(proof:dict,gui_request:dict,generation:StrictInt,request_id:str)->dict:
        """Private signed current authenticated-GUI End call command; never redials."""
        return await perform(generation,request_id,lambda:service.end(proof,gui_request))
    return app

def main():
    from task_mcp.client import ROOT
    from .backend import Backend
    from .browser_call import BrowserCall,CallUIProfile
    from .call_service import CallService,digest
    from .native_call_end import NativeCallOwner
    backend=Backend(ROOT);service=None
    native=NativeCallOwner(ROOT,ROOT/'data/whatsapp-call-native-owner.json',backend)
    # Read-only inspection proves this exact direct Voice call control only.
    # No ringing/connected/ended profile is inferred or fabricated from it.
    import hashlib,json
    path=ROOT/'evidence/assistant/wb06-call-cutover-20261030-130/pi/inventory-002/receipt.json'
    evidence=path.read_bytes()
    if hashlib.sha256(evidence).hexdigest()!='988899a938a6086e0687570fd4bbb3ac81a32e3d3fc7ea0ff4acdc24ce485171':raise RuntimeError('inspected_call_control_receipt_required')
    inventory=json.loads(evidence)['inventory']
    if inventory.get('official') is not True or inventory.get('linked') is not True or inventory.get('header') is not True or inventory.get('dialogs')!=0 or not any(x.get('label')=='Voice call' and x.get('enabled') is True for x in inventory.get('controls',[])):raise RuntimeError('exact_observed_direct_voice_call_control_required')
    profile=CallUIProfile(hashlib.sha256(evidence).hexdigest(),direct_labels={'voice':'Voice call'})
    def browser(row,proof):
        return BrowserCall(ROOT,ROOT/'data/whatsapp-call-browser.sqlite3',backend,row['owner'],row['id'],profile=profile,calls_enabled=True,authority=lambda owner,op,kind,person_hash:owner==proof['owner'] and op==proof['operation_id'] and kind=='voice' and person_hash==digest(row['binding']['person']),allow_unqualified_observation=True)
    async def attest(row,proof):
        p=row['binding']['person'];await native.attest(row['id'],row['owner'],digest(p),p['target_id'])
    async def local_end(row,request):
        person=digest(row['binding']['person'])
        try:
            async with native.connect_attested(row['id'],row['owner'],person):
                result=await native.close_once(row['id'],row['owner'],person,authorized=True)
        except Exception:
            # A disconnected/closed browser is not an ended-call claim. Only
            # independent saved PID/session/audio-node evidence can reconcile
            # it here, and this branch never repeats a close click.
            result=await native.observe_gone(row['id'],row['owner'],person)
        # Translate independently qualified native owner evidence, never the
        # authenticated command itself, into local terminal status.
        established=(result.get('operation_id')==row['id'] and result.get('state')=='LOCAL_ENDED' and result.get('verified') is True and result.get('verification_source')=='fresh_owned_browser_session_and_audio_streams_gone' and result.get('remote_end_acknowledged') is False)
        return dict(result,independent=established,owned_browser_closed=established,owned_streams_closed=established)
    async def local_observe(row):
        result=await native.observe_gone(row['id'],row['owner'],digest(row['binding']['person']))
        established=(result.get('operation_id')==row['id'] and result.get('state')=='LOCAL_ENDED' and result.get('verified') is True and result.get('verification_source')=='fresh_owned_browser_session_and_audio_streams_gone' and result.get('remote_end_acknowledged') is False)
        return dict(result,independent=established,owned_browser_closed=established,owned_streams_closed=established)
    service=CallService(ROOT,backend,browser,attest=attest,local_end=local_end,local_observe=local_observe)
    logging.disable(logging.CRITICAL);make_server(service).run(transport='stdio')
if __name__=='__main__':main()

