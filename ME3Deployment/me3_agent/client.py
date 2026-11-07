"""Discover real MCP schemas, then expose only permitted model operations."""
import asyncio,os,sys,uuid
from datetime import timedelta
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from task_mcp.client import TaskClient,TaskError,ROOT
from speech_mcp.client import CancellableSession,SpeechClients
MODEL_TOOLS={'list_items','calculate_expression','get_current_time'}
class AgentTasks(TaskClient):
    def __init__(self,*args,utility_delay_ms=0,**kwargs):
        super().__init__(*args,**kwargs)
        if type(utility_delay_ms) is not int or not 0<=utility_delay_ms<=20000:raise ValueError('invalid_controlled_delay')
        self.utility_delay_ms=utility_delay_ms
    async def __aenter__(self):
        try:
            args=['-m','me3_agent.tools']
            if self.utility_delay_ms:args+=['--utility-delay-ms',str(self.utility_delay_ms)]
            if self.test_db:args+=['--test-db',str(self.test_db)]
            env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','HOME','USERPROFILE','VIRTUAL_ENV','LD_LIBRARY_PATH'}}
            stderr=self.stack.enter_context((self.events.path.parent/'agent-task-server.stderr.log').open('x',encoding='utf-8'))
            streams=await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=args,cwd=str(ROOT),env=env),errlog=stderr))
            self.session=await self.stack.enter_async_context(CancellableSession(*streams,events=self.events,role='task',read_timeout_seconds=timedelta(seconds=5)))
            init=await self.session.initialize();listed=await self.session.list_tools();self.registry={t.name:t for t in listed.tools}
            if not (MODEL_TOOLS|{'prepare_item','commit_item','read_item'})<=self.registry.keys():raise TaskError('agent_tools_missing')
            info=SpeechClients.unpack(await self.session.call_tool('server_info',{}))
            self.events.emit('mcp_connected',role='task',server_pid=info['server_pid'],host=info['host'],machine=info['machine'],protocol=init.protocolVersion,transport='stdio',tool_names=sorted(self.registry),model_commit_exposed=False)
            return self
        except BaseException:await self.stack.aclose();raise
    def declarations(self):
        output=[]
        for name in sorted(MODEL_TOOLS|{'prepare_item'}):
            tool=self.registry[name];schema=tool.inputSchema
            properties={k:v for k,v in schema.get('properties',{}).items() if k not in {'generation','request_id','operation_id'}}
            required=[k for k in schema.get('required',[]) if k in properties]
            output.append({'name':'propose_shopping_item' if name=='prepare_item' else name,'description':('Propose one shopping-list addition for human confirmation. Never saves. Ask for missing item or quantity instead of guessing.' if name=='prepare_item' else tool.description),'parameters':{'type':'object','properties':properties,'required':required}})
        output.append({'name':'cancel_pending_action','description':'Cancel an unconfirmed proposal when the user changes their mind. Does not undo completed writes.','parameters':{'type':'object','properties':{}}})
        return output
    async def call(self,tool,generation,**arguments):
        if tool not in {'calculate_expression','get_current_time'}:return await super().call(tool,generation,**arguments)
        if self.calls>=32:raise TaskError('task_rpc_budget')
        self.calls+=1;rid='task-'+uuid.uuid4().hex
        self.events.emit('task_mcp_request_start',tool=tool,generation=generation,request_id=rid)
        try:
            timeout=25 if self.utility_delay_ms else 5
            if self.utility_delay_ms:self.events.emit('agent_controlled_tool_delay',generation=generation,request_id=rid,delay_ms=self.utility_delay_ms,natural_service_latency=False)
            async with asyncio.timeout(timeout):result=await self.session.call_tool(tool,dict(generation=generation,request_id=rid,**arguments),read_timeout_seconds=timedelta(seconds=timeout))
            data=SpeechClients.unpack(result)
            if data.get('generation')!=generation or data.get('request_id')!=rid or data.get('server_role')!='task':raise TaskError('task_identity_mismatch')
            if data.get('ok') is not True:raise TaskError('utility_rejected')
            self.events.emit('task_mcp_request_end',tool=tool,generation=generation,request_id=rid,server_pid=data['server_pid'],read_only=True)
            return data
        except asyncio.CancelledError:
            self.events.emit('task_mcp_request_cancelled',tool=tool,generation=generation,request_id=rid);raise
        except Exception:
            self.events.emit('task_mcp_request_error',tool=tool,generation=generation,request_id=rid,read_only=True)
            raise TaskError('utility_failed_no_side_effect') from None
