"""Recover at most two independently cleaned ASR gateway timeouts; never replay."""
from me3_completion.client import ConversationClients
from speech_mcp.client import SpeechClients
from me3_m7.client import RecoverableASRTimeout

class GatewaySession:
    def __init__(self,session,owner):self.session=session;self.owner=owner
    def __getattr__(self,name):return getattr(self.session,name)
    async def call_tool(self,name,arguments,*args,**kwargs):
        result=await self.session.call_tool(name,arguments,*args,**kwargs)
        if name!='transcribe':return result
        data=SpeechClients.unpack(result)
        if data.get('ok') is False and type(data.get('code')) is int and data['code']==504 and data.get('error_type')=='ServerError' and data.get('cleanup_ok') is True:
            if data.get('generation')!=arguments.get('generation') or data.get('request_id')!=arguments.get('request_id') or data.get('server_role')!='asr':raise ValueError('gateway_timeout_identity_mismatch')
            if self.owner.gateway_timeouts<2:
                self.owner.gateway_timeouts+=1
                self.owner.events.emit('mcp_request_error',role='asr',generation=data['generation'],request_id=data['request_id'],code=504,error_type='ServerError',upstream_gateway_timeout=True,cleanup_ok=True)
                self.owner.events.emit('agent_clean_gateway_timeout_recoverable',generation=data['generation'],request_id=data['request_id'],code=504,count=self.owner.gateway_timeouts,cleanup_ok=True,automatic_retry=False)
                raise RecoverableASRTimeout('cleaned_provider_gateway_timeout')
        return result

class AgentSpeechClients(ConversationClients):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.gateway_timeouts=0
    async def __aenter__(self):
        await super().__aenter__();self.sessions['asr']=GatewaySession(self.sessions['asr'],self);return self
