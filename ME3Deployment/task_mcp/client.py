"""Actual separate task MCP process. Metadata only; no provider credentials forwarded."""
import asyncio,os,sys,uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from speech_mcp.client import CancellableSession,SpeechClients
from speech_mcp.contracts import identity
ROOT=Path(__file__).resolve().parents[1]
class TaskError(ValueError):pass
class TaskClient:
    def __init__(self,events,test_db=None):self.events=events;self.test_db=test_db;self.stack=AsyncExitStack();self.calls=0;self.write_blocked=False
    async def __aenter__(self):
        try:
            args=['-m','task_mcp.server']
            if self.test_db:args+=['--test-db',str(self.test_db)]
            env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','HOME','USERPROFILE','VIRTUAL_ENV','LD_LIBRARY_PATH'}}
            stderr=self.stack.enter_context((self.events.path.parent/'task-server.stderr.log').open('w',encoding='utf-8'))
            streams=await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=args,cwd=str(ROOT),env=env),errlog=stderr))
            self.session=await self.stack.enter_async_context(CancellableSession(*streams,events=self.events,role='task',read_timeout_seconds=timedelta(seconds=5)))
            init=await self.session.initialize();tools=await self.session.list_tools()
            if not {'prepare_item','commit_item','read_item','list_items'}<={t.name for t in tools.tools}:raise TaskError('task_tools_missing')
            data=SpeechClients.unpack(await self.session.call_tool('server_info',{}))
            self.events.emit('mcp_connected',role='task',server_pid=data['server_pid'],host=data['host'],machine=data['machine'],protocol=init.protocolVersion,transport='stdio',storage='SQLite',write_budget=1)
            return self
        except BaseException:await self.stack.aclose();raise
    async def __aexit__(self,*args):return await self.stack.__aexit__(*args)
    async def call(self,tool,generation,**arguments):
        if tool not in {'prepare_item','commit_item','read_item','list_items'}:raise TaskError('invalid_task_tool')
        if self.calls>=20:raise TaskError('task_rpc_budget')
        if self.write_blocked and tool in {'prepare_item','commit_item'}:raise TaskError('task_writes_stopped')
        request_id='task-'+uuid.uuid4().hex;identity(generation,request_id);self.calls+=1
        self.events.emit('task_mcp_request_start',tool=tool,generation=generation,request_id=request_id,operation_id=arguments.get('operation_id'))
        try:
            async with asyncio.timeout(5):result=await self.session.call_tool(tool,dict(generation=generation,request_id=request_id,**arguments),read_timeout_seconds=timedelta(seconds=5))
            data=SpeechClients.unpack(result)
            if data.get('generation')!=generation or data.get('request_id')!=request_id or data.get('server_role')!='task':raise TaskError('task_identity_mismatch')
            if not data.get('ok'):raise TaskError('task_request_failed')
            if 'operation_id' in arguments and data.get('operation_id')!=arguments['operation_id']:raise TaskError('task_operation_mismatch')
            self.events.emit('task_mcp_request_end',tool=tool,generation=generation,request_id=request_id,operation_id=data.get('operation_id'),server_pid=data['server_pid'],state_digest=data.get('state_digest'),verification_source=data.get('verification_source'),duplicate=data.get('duplicate'))
            return data
        except asyncio.CancelledError:
            if tool=='commit_item':self.write_blocked=True
            self.events.emit('task_mcp_request_cancelled',tool=tool,generation=generation,request_id=request_id);raise
        except Exception as exc:
            self.write_blocked=True
            self.events.emit('task_mcp_request_error',tool=tool,generation=generation,request_id=request_id,error_type=type(exc).__name__)
            raise TaskError('task_request_failed_or_unverified') from None
