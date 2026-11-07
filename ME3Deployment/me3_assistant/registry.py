"""Real MCP discovery with configured permissions and bounded, identity-checked RPCs."""
import asyncio,copy,json,os,sys,uuid,anyio,time
from contextlib import AsyncExitStack
from datetime import timedelta
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from speech_mcp.client import CancellableSession,SpeechClients
from task_mcp.client import ROOT
from me3_assistant.config import WORKSPACE_GRANTED

class ToolFailure(ValueError):pass
PRIVATE={'host_owner','host_goal','current_user_request','generation','request_id','operation_id','token','confirmation','server_pid','server_role','host','machine','request_binding','proof'}

def public(value):
    if isinstance(value,dict):return {k:public(v) for k,v in value.items() if k not in PRIVATE}
    if isinstance(value,list):return [public(v) for v in value]
    return value

def remember_result(tasks,completed,name,args,result):
    """A workspace edit invalidates earlier file/command and goal observations.

    Chronological model responses and workflow reports still retain the original
    result; a later identical test command must actually execute the new source.
    """
    if name in getattr(tasks,'workspace_operations',set()) and name.endswith('__write_workspace_file') and result.get('ok') is True:
        server=name.split('__',1)[0]
        for key in list(completed):
            previous=json.loads(key)[0]
            if previous=='run_goal_steps' or previous.startswith(server+'__'):del completed[key]
    if name in getattr(tasks,'workspace_operations',set()) and name.endswith('__run_workspace_command') and result.get('ok') is True:
        server=name.split('__',1)[0]
        for key in list(completed):
            if json.loads(key)[0]==server+'__list_workspace_runs':del completed[key]
    completed[json.dumps([name,args],sort_keys=True)]=result

class Registry:
    def __init__(self,events,config,*,music_bus=None,stream_broker=None):
        self.stream_broker=stream_broker
        self.music_owner=uuid.uuid4().hex
        self.communication_owner=uuid.uuid4().hex
        self.communication_context=None
        from me3_whatsapp.contact_choices import ContactChoices
        self.contact_choices=ContactChoices(self.communication_owner)
        from me3_stream.discovery import SearchChoices
        self.stream_choices=SearchChoices(self.music_owner)
        self.music_bus=music_bus;self.events=events;self.config=config;self.stack=AsyncExitStack();self.sessions={};self.schemas={};self.reads={};self.workspace_operations=set();self.calls=0;self.current=lambda g:True;self.hosted_search=None
    async def __aenter__(self):
        try:
            if any(spec['module']=='me3_gmail.server' for spec in self.config['servers']):
                from me3_gmail.server import load_authority
                try:self.gmail_authority=load_authority(ROOT)
                except (ValueError,OSError):self.gmail_authority=None
            for spec in self.config['servers']:
                env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','HOME','USERPROFILE','VIRTUAL_ENV','LD_LIBRARY_PATH'}}
                env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
                if self.music_bus is not None and spec['module']=='me3_assistant.music_server':env['ME3_MUSIC_BUS']=str(self.music_bus)
                if spec['module']=='me3_stream.server':
                    if self.stream_broker is None:raise ToolFailure('owned_SoundCloud_broker_required')
                    env['ME3_SOUNDCLOUD_BROKER']=str(getattr(self.stream_broker,'path',self.stream_broker))
                stderr=self.stack.enter_context(open(os.devnull,'w'))
                streams=await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m',spec['module'],*spec['args']],cwd=str(ROOT),env=env),errlog=stderr))
                client=await self.stack.enter_async_context(CancellableSession(*streams,events=self.events,role='task',read_timeout_seconds=timedelta(seconds=spec.get('rpc_timeout_seconds',5))))
                await client.initialize();listed=await client.list_tools()
                self.sessions[spec['id']]=(client,spec)
                for tool in listed.tools:
                    Draft202012Validator.check_schema(tool.inputSchema)
                    self.schemas[(spec['id'],tool.name)]=tool
                for name in [*spec['read_tools'],*spec.get('workspace_tools',[])]:
                    if (spec['id'],name) not in self.schemas:raise ToolFailure('configured_tool_missing')
                    # Action lifecycle endpoints never become direct model calls.
                    if name in {'prepare_call','commit_call','read_call','end_call','abort_call','reconcile_call','prepare_contact_message','commit_contact_message','read_contact_message','prepare_window_close','commit_window_close','read_window_operation','prepare_self_message','commit_self_message','read_message_operation','prepare_stream_control','commit_stream_control','read_stream_operation','prepare_player_control','commit_player_control','read_player_operation','prepare_item','commit_item','prepare_music_control','commit_music_control','read_music_operation','prepare_reminder','commit_reminder','read_reminder_operation','prepare_productivity_change','commit_productivity_change','read_productivity_operation'}:raise ToolFailure('action_endpoint_cannot_be_read_tool')
                    if name in {'prepare_google_calendar_change','commit_google_calendar_change','read_google_calendar_operation'}:raise ToolFailure('action_endpoint_cannot_be_read_tool')
                    if name in {'prepare_gmail_send','commit_gmail_send','read_gmail_operation','prepare_gmail_read','commit_gmail_read','read_gmail_read_operation'}:raise ToolFailure('action_endpoint_cannot_be_read_tool')
                    if spec['module']=='me3_gmail.server':raise ToolFailure('gmail_reads_require_exact_review_and_separate_confirmation')
                    alias=spec['id']+'__'+name
                    if name in WORKSPACE_GRANTED:
                        if name not in spec.get('workspace_tools',[]) or spec['module']!='me3_assistant.workspace_server':raise ToolFailure('workspace_operation_requires_explicit_configuration')
                        self.workspace_operations.add(alias)
                    self.reads[alias]=(spec['id'],name)
                self.events.emit('assistant_mcp_connected',server=spec['id'],discovered_tools=len(listed.tools),exposed_reads=len(spec['read_tools']))
            return self
        except BaseException:await self.stack.aclose();raise
    async def __aexit__(self,*args):
        unsettled=False
        try:
            # Consume the pinned MCP cancellation reply before closing its receiver.
            # Otherwise a late reply can race stdio shutdown (BrokenResourceError).
            with anyio.CancelScope(shield=True):
                for name,(session,spec) in self.sessions.items():
                    try:
                        async with asyncio.timeout(2):await session.cancelled_settled.wait()
                    except TimeoutError:
                        unsettled=True;self.events.emit('assistant_cancel_cleanup_timeout',server=name,pending=len(session.abandoned))
        finally:result=await self.stack.__aexit__(*args)
        if unsettled and args[0] is None:raise ToolFailure('tool_cancellation_cleanup_unverified')
        if not unsettled and self.music_bus and hasattr(self,'journal'):
            # The child stack has closed. Independently require the owned bus
            # to be stopped/empty before retiring ONLY this owner's uncertain
            # volatile controls. Unknown legacy or persistent effects stay held.
            try:
                from me3_assistant.music_audio import MusicBus
                bus=MusicBus(self.music_bus)
                try:snapshot=bus.snapshot()
                finally:bus.close()
                if snapshot['state']=='stopped' and snapshot['buffered_bytes']==0:
                    count=self.journal.end_music_owner(self.music_owner)
                    if count:self.events.emit('assistant_music_session_retired',count=count,original_outcome_verified=False,write_retried=False)
            except Exception:self.events.emit('assistant_music_session_retirement_unverified',write_retried=False)
        if not unsettled and self.stream_broker is not None and getattr(self.stream_broker,'stopped_cleanly',False) is True and hasattr(self,'journal'):
            count=self.journal.end_music_owner(self.music_owner,actions=('propose_stream_control',))
            if count:self.events.emit('assistant_stream_session_retired',count=count,original_outcome_verified=False,write_retried=False)
        return result
    def exposed_schema(self,server,name):
        schema=copy.deepcopy(self.schemas[(server,name)].inputSchema)
        schema['properties']={k:v for k,v in schema.get('properties',{}).items() if k not in PRIVATE}
        schema['required']=[k for k in schema.get('required',[]) if k not in PRIVATE]
        schema['additionalProperties']=False
        if (name=='manage_workspace_runtime' and self.sessions.get(server,(None,{}))[1].get('module')=='me3_assistant.workspace_server'
            and getattr(self,'background_runtime_observer',None) is None):
            # Keep the authoritative discovered schema, attenuating only the
            # unavailable host-owned completion lane. Ordinary startup remains.
            field=schema.get('$defs',{}).get('RuntimeStart',{}).get('properties',{}).get('background_ready')
            if not isinstance(field,dict) or field.get('type')!='boolean':raise ToolFailure('background_runtime_schema_shape_changed')
            field['const']=False
            field['description']='Unavailable in this session; leave false or omit. Startup uses the existing blocking health result. No background notice is promised.'
        if name=='search_contacts' and self.sessions.get(server,(None,{}))[1].get('module')=='me3_whatsapp.server':
            import re
            request=(getattr(self,'reference_request',None) or (self.communication_context or {}).get('request') or '').strip().rstrip('.!?')
            # A short single-name clarification must use its actual spelling,
            # e.g. CONTACT_QUERY_EXAMPLE, rather than silently substituting an old CONTACT_LABEL query.
            if re.fullmatch(r"[\w’'-]{2,80}",request):schema['properties']['query']['enum']=[request]
        if name=='inspect_contact' and self.sessions.get(server,(None,{}))[1].get('module')=='me3_whatsapp.server':
            from me3_whatsapp.recipients import selection_references
            request=getattr(self,'reference_request',None) or (self.communication_context or {}).get('request')
            candidates=selection_references(request,self.contact_choices.snapshot()['matches'])
            if candidates:schema['properties']['reference_text']['enum']=candidates
        return schema
    def declarations(self):
        from me3_google_calendar.budget import authorized as calendar_authorized
        return [{'name':alias,'description':(self.schemas[key].description or 'Configured MCP tool.')[:1000],'parameters':self.exposed_schema(*key)} for alias,key in sorted(self.reads.items()) if (alias not in self.workspace_operations or getattr(self,'session_grant',{}).get('workspace_access') is True) and (self.sessions[key[0]][1]['module']!='me3_google_calendar.server' or calendar_authorized(getattr(self,'session_grant',{})))]
    def productivity_read_contract(self,alias):
        from me3_productivity.server import READ_TOOLS
        key=getattr(self,'reads',{}).get(alias)
        if key is None or key not in self.schemas or key[0] not in self.sessions:return False
        spec=self.sessions[key[0]][1]
        return spec.get('module')=='me3_productivity.server' and spec.get('identity')=='me3' and spec.get('role')=='utility' and key[1] in READ_TOOLS and key[1] in spec.get('read_tools',[])
    def workspace_command_feedback_contract(self,alias):
        key=getattr(self,'reads',{}).get(alias)
        if key is None or key not in self.schemas or key[0] not in self.sessions:return False
        spec=self.sessions[key[0]][1]
        return (alias in self.workspace_operations and getattr(self,'session_grant',{}).get('workspace_access') is True
                and key[1]=='run_workspace_command' and key[1] in spec.get('workspace_tools',[])
                and spec.get('module')=='me3_assistant.workspace_server' and spec.get('identity')=='me3' and spec.get('role')=='utility')
    def workspace_write_feedback_contract(self,alias):
        key=getattr(self,'reads',{}).get(alias)
        if key is None or key not in self.schemas or key[0] not in self.sessions:return False
        spec=self.sessions[key[0]][1]
        return (alias=='workspace__write_workspace_file' and alias in self.workspace_operations and getattr(self,'session_grant',{}).get('workspace_access') is True
                and key[1]=='write_workspace_file' and key[1] in spec.get('workspace_tools',[])
                and spec.get('module')=='me3_assistant.workspace_server' and spec.get('identity')=='me3' and spec.get('role')=='utility')
    def productivity_read_preflight(self,alias,args):
        if not self.productivity_read_contract(alias):raise ToolFailure('read_contract_not_permitted')
        if not isinstance(args,dict) or len(json.dumps(args,allow_nan=False))>12000:raise ToolFailure('tool_arguments_too_large_or_invalid')
        if PRIVATE.intersection(args):raise ToolFailure('caller_identity_injection')
    def productivity_read_rejected(self,server,tool,g,reason,*,request_id=None,dispatched=False):
        if type(g) is not int or not self.current(g):raise ToolFailure('superseded_or_invalid_read_generation')
        self.events.emit('assistant_rpc_rejected',server=server,tool=tool,generation=g,request_id=request_id,validation_reason=reason,dispatched=dispatched)
        return dict(ok=False,status='read_rejected',read_only=True,no_effect=True,validation_reason=reason,automatic_retry=False,
            guidance='No record content or successful lookup is implied. Check productivity_context for the configured clock/timezone, use explicit offset-bearing calendar start/end and a bounded range or explicit planning day, and discover exact current IDs with list/search before reading their current revision. Creation/update timestamps and section durations do not establish a scheduled start; an explicit start time in note content may. Clarify missing information; do not guess IDs/dates/times or repeat the same rejected call.')
    async def rpc(self,server,tool,g,args):
        rejection=None
        if server+'__'+tool in self.workspace_operations and getattr(self,'session_grant',{}).get('workspace_access') is not True:raise ToolFailure('workspace_authorization_required')
        if not self.current(g):raise ToolFailure('superseded_before_dispatch')
        if self.calls>=self.config['runtime']['rpc_calls']:raise ToolFailure('assistant_rpc_budget')
        if (server,tool) not in self.schemas:raise ToolFailure('tool_not_discovered')
        client,spec=self.sessions[server];rid='assistant-'+uuid.uuid4().hex
        arguments=dict(args)
        if any(k in arguments for k in {'generation','request_id'}):raise ToolFailure('caller_identity_injection')
        read_contract=self.productivity_read_contract(server+'__'+tool)
        if read_contract:
            self.productivity_read_preflight(server+'__'+tool,args)
            if type(g) is not int:raise ToolFailure('invalid_generation')
        if spec['identity']=='me3':arguments.update(generation=g,request_id=rid)
        wire_limit=128000 if spec['module'] in {'me3_gmail.server','me3_google_calendar.server'} else 12000
        argument_limit=400000 if self.workspace_write_feedback_contract(server+'__'+tool) else wire_limit
        if len(json.dumps(arguments))>argument_limit:raise ToolFailure('tool_arguments_too_large')
        try:Draft202012Validator(self.schemas[(server,tool)].inputSchema).validate(arguments)
        except ValidationError:
            if read_contract:return self.productivity_read_rejected(server,tool,g,'productivity_read_parameters_invalid')
            raise
        if spec['module']=='me3_google_calendar.server':
            from me3_google_calendar.budget import authorized as calendar_authorized
            if not calendar_authorized(getattr(self,'session_grant',{})):raise ToolFailure('google_calendar_authorization_required')
            if tool!='google_calendar_context':
                context=self.communication_context
                if not context or context.get('generation')!=g or not self.current(g):raise ToolFailure('current_calendar_owner_command_required')
                if tool!='commit_google_calendar_change':
                    budget=getattr(self,'google_calendar_budget',None)
                    if budget is None:raise ToolFailure('calendar_read_accounting_unavailable')
                    budget.reserve('google_calendar_read')
        self.calls+=1;self.events.emit('assistant_rpc_start',server=server,tool=tool,generation=g,request_id=rid)
        background_origin=None;background_observer=None;data=None
        try:
            expected_workspace_manifest=None
            expected_workspace_environment=None
            expected_runtime_manifest=None
            runtime_contract=spec['module']=='me3_assistant.workspace_server' and tool=='manage_workspace_runtime'
            if spec['module']=='me3_assistant.workspace_server' and tool=='run_workspace_command':
                from me3_assistant.workspace_history import manifest as workspace_manifest
                from me3_assistant.workspace import Workspace
                from me3_assistant.workspace_runtime import active_environment_sha256
                expected_workspace_manifest=workspace_manifest(Workspace(ROOT/'data/agent-workspace').files())
                expected_workspace_environment=active_environment_sha256(ROOT/'data/agent-runtime')
            if runtime_contract and args['request']['command'] in {'start','restart'}:
                from me3_assistant.workspace_history import manifest as workspace_manifest
                from me3_assistant.workspace import Workspace
                expected_runtime_manifest=workspace_manifest(Workspace(ROOT/'data/agent-workspace').files())
                if args['request']['expected_workspace_sha256']!=expected_runtime_manifest:raise ToolFailure('workspace_runtime_manifest_changed_before_dispatch')
            if runtime_contract:
                background_observer=getattr(self,'background_runtime_observer',None)
                if args['request'].get('background_ready') is True and background_observer is None:raise ToolFailure('background_ready_delivery_CORE_UNBOUND')
                if background_observer is not None:background_origin=background_observer.prepare(server,tool,g,args)
            hosted_contract=(spec.get('module')=='me3_assistant.research_server' and spec.get('identity')=='me3' and spec.get('role')=='utility'
                and tool=='search_web' and tool in spec.get('read_tools',[]) and self.reads.get(server+'__'+tool)==(server,tool))
            if hosted_contract and self.hosted_search is not None:
                from me3_assistant.hosted_search import HostedSearch
                if type(self.hosted_search) is not HostedSearch or not self.current(g) or not self.hosted_search.eligible(g):raise ToolFailure('hosted_search_current_host_binding_required')
                try:
                    async with asyncio.timeout(spec.get('rpc_timeout_seconds',5)):
                        data=await self.hosted_search(args['query'],args.get('limit',4),g,rid)
                except TimeoutError:
                    if not self.current(g) or not self.hosted_search.eligible(g):raise asyncio.CancelledError()
                    await self.hosted_search.authority(g)
                    data=dict(ok=True,available=False,backend='selected_plan_hosted_web_search',read_only=True,error='hosted_search_RPC_deadline',automatic_retry=False,helper_reasoning_requests=self.hosted_search.last_request_count,instruction='The search deadline elapsed and its owned request settled. No usable sources were obtained; report this limitation without retry or fallback.')
                if not self.current(g) or not self.hosted_search.eligible(g):raise ToolFailure('superseded_hosted_search_result')
                data=dict(data,generation=g,request_id=rid,server_role=spec['role'],actual_MCP_call=False,execution_location='authorized_host_utility')
            else:
                async with asyncio.timeout(spec.get('rpc_timeout_seconds',5)):result=await client.call_tool(tool,arguments,read_timeout_seconds=timedelta(seconds=spec.get('rpc_timeout_seconds',5)))
                if result.isError:raise ToolFailure('tool_error')
                if spec['identity']=='me3':data=SpeechClients.unpack(result)
                elif result.structuredContent is not None:data=result.structuredContent
                else:
                    if any(x.type!='text' for x in result.content):raise ToolFailure('unsupported_tool_result_content')
                    text='\n'.join(x.text for x in result.content)
                    if len(text)>12000:raise ToolFailure('tool_result_too_large')
                    try:data=json.loads(text)
                    except ValueError:data={'text':text}
            if len(json.dumps(data))>wire_limit:raise ToolFailure('tool_result_too_large')
            if spec['identity']=='me3' and (data.get('generation')!=g or data.get('request_id')!=rid or data.get('server_role')!=spec['role']):raise ToolFailure('tool_identity_mismatch')
            if runtime_contract:
                from me3_assistant.workspace_runtime import validate_runtime_result
                validate_runtime_result(data,args['request'],expected_runtime_manifest)
                if background_origin is not None:await background_observer.complete(background_origin,data)
            if isinstance(data,dict) and data.get('ok') is False:
                if self.workspace_write_feedback_contract(server+'__'+tool):
                    from me3_assistant.workspace import validate_write_rejection
                    reason=validate_write_rejection({k:v for k,v in data.items() if k not in PRIVATE})
                    if type(g) is not int or not self.current(g):raise ToolFailure('superseded_workspace_parameter_feedback')
                    self.events.emit('assistant_rpc_rejected',server=server,tool=tool,generation=g,request_id=rid,validation_reason=reason,dispatched=True,execution_started=False,no_effect=True)
                    return data
                if self.workspace_command_feedback_contract(server+'__'+tool):
                    from me3_assistant.workspace_validation import validate_command_rejection
                    reason=validate_command_rejection({k:v for k,v in data.items() if k not in PRIVATE})
                    if type(g) is not int or not self.current(g):raise ToolFailure('superseded_workspace_parameter_feedback')
                    self.events.emit('assistant_rpc_rejected',server=server,tool=tool,generation=g,request_id=rid,validation_reason=reason,dispatched=True,execution_started=False,no_effect=True)
                    return data
                if spec['module']=='me3_assistant.reminder_server':
                    from me3_assistant.reminder_server import REJECTIONS
                    reason=data.get('rejection_reason');rejection=reason if reason in REJECTIONS else 'reminder_validation_or_storage_failure'
                elif read_contract:
                    from me3_productivity.server import READ_VALIDATION_REJECTIONS
                    reason=data.get('rejection_reason')
                    if (data.get('error')=='productivity_request_rejected' and data.get('rejection_kind')=='read_validation' and data.get('read_only') is True and data.get('no_effect') is True
                        and isinstance(reason,str) and reason in READ_VALIDATION_REJECTIONS):
                        return self.productivity_read_rejected(server,tool,g,reason,request_id=rid,dispatched=True)
                if not runtime_contract:raise ToolFailure('tool_rejected')
            if spec['module']=='me3_assistant.workspace_server' and tool=='run_workspace_command':
                from me3_assistant.workspace_history import validate_execution_result
                validate_execution_result(data,args,expected_workspace_manifest,expected_environment_sha256=expected_workspace_environment)
            self.events.emit('assistant_rpc_end',server=server,tool=tool,generation=g,request_id=rid)
            return data
        except asyncio.CancelledError:
            if background_origin is not None and background_origin['command']=='start':background_observer.uncertain(background_origin,data,'canceled_runtime_RPC')
            self.events.emit('assistant_rpc_canceled',server=server,tool=tool,generation=g,request_id=rid);raise
        except Exception:
            if background_origin is not None and background_origin['command']=='start':background_observer.uncertain(background_origin,data,'failed_or_unverified_runtime_RPC')
            self.events.emit('assistant_rpc_error',server=server,tool=tool,generation=g,request_id=rid,**({'validation_reason':rejection} if rejection else {}))
            raise ToolFailure('tool_failed_or_unverified') from None
    async def read(self,alias,g,args):
        if alias not in self.reads:raise ToolFailure('read_tool_not_permitted')
        key=self.reads[alias];read_contract=self.productivity_read_contract(alias)
        if read_contract:
            self.productivity_read_preflight(alias,args)
            if type(g) is not int or not self.current(g):raise ToolFailure('superseded_or_invalid_read_generation')
        try:Draft202012Validator(self.exposed_schema(*key)).validate(args)
        except ValidationError:
            if read_contract:return self.productivity_read_rejected(*key,g,'productivity_read_parameters_invalid')
            raise
        discovery=key in self.schemas and key[1]=='search_music' and self.sessions[key[0]][1]['module']=='me3_stream.server'
        started=time.monotonic()
        if discovery:self.stream_choices.begin_search()
        if key[1] in {'search_contacts','inspect_contact'} and self.sessions[key[0]][1]['module']=='me3_whatsapp.server':
            context=self.communication_context
            if not context or context['generation']!=g or not self.current(g):raise ToolFailure('current_contact_command_required')
            if key[1]=='search_contacts':
                from me3_whatsapp.recipients import Command
                from me3_whatsapp.guard import Rejected
                try:Command(self.communication_owner,context['goal'],g,context['request']).permit_query(args['query'])
                except Rejected:
                    return dict(status='needs_clarification',reply='Please tell me directly which WhatsApp contact you want me to find. No contact search, message or call has started.',contact_queries=0,messages_sent=0,calls_started=0,automatic_retry=False)
            if key[1]=='inspect_contact':
                from me3_whatsapp.guard import Rejected
                try:self.contact_choices.check_inspection(context['request'],args['selection_id'],args['reference_text'])
                except Rejected:
                    return dict(status='needs_clarification',reason='current_contact_reference_required',reply='Choose a specific contact from the current offered list, or ask for a fresh lookup if it expired. No message or call has started.',messages_sent=0,calls_started=0,automatic_retry=False)
            arguments=dict(args,host_owner=self.communication_owner,host_goal=context['goal'],current_user_request=context['request'])
            data=await self.rpc(*key,g,arguments)
        elif self.sessions[key[0]][1]['module']=='me3_google_calendar.server':
            from me3_google_calendar.server import command_binding
            context=self.communication_context
            if not context or context.get('generation')!=g or not self.current(g):raise ToolFailure('current_calendar_owner_command_required')
            command_binding(self.communication_owner,context['goal'],g,context['request'])
            data=await self.rpc(*key,g,dict(args,host_owner=self.communication_owner,host_goal=context['goal'],current_user_request=context['request']))
        elif self.sessions[key[0]][1]['module']=='me3_gmail.server':
            return dict(status='needs_permission',available=False,no_effect=True,network_dispatched=False,automatic_retry=False,reply='Mailbox access requires an exact read-scope review and separate confirmation. No Gmail read started. Use the guarded Gmail read proposal only for the requested mailbox task; a send-only request does not authorize inbox access.')
        else:data=await self.rpc(*key,g,args)
        if not self.current(g):raise ToolFailure('superseded_result')
        if discovery:self.stream_choices.observe(data,started)
        if key[1]=='search_contacts' and self.sessions[key[0]][1]['module']=='me3_whatsapp.server':self.contact_choices.observe(data,started)
        if key[1]=='inspect_contact' and self.sessions[key[0]][1]['module']=='me3_whatsapp.server':self.contact_choices.verified(data)
        return public(data)
