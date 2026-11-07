"""General schema-selected read workflows; direct-user confirmation owns actions."""
import asyncio,json
from google.genai import types
from jsonschema import Draft202012Validator
from task_mcp.reasoner import CONFIRM,CANCEL,latest_text,normalized
from speech_mcp.contracts import validate_text
from me3_assistant.actions import Actions
from me3_assistant.result_delivery import deliver_results,verbatim_passage
from me3_assistant.workflow import Workflow,NAME as GOAL_TOOL,declaration as goal_declaration
from me3_assistant.registry import remember_result
from me3_assistant.schema_catalog import Selection,NAME as SCHEMA_TOOL

# A stand-alone, direct statement; questions still clarify, and all action
# ownership, delivered-prompt, expiry and allowance checks remain in Actions.
CONFIRM_REPLY=CONFIRM|{'confirmed'}

# Social acknowledgments are neither new tasks nor action authorization.
NEUTRAL_ACKS=frozenset({"don't worry",'dont worry','don’t worry','no worries',"that's okay",'that’s okay','thanks','thank you','got it','understood'})

LOCAL=[{'name':'cancel_pending_action','description':'Cancel an unconfirmed proposal. Cannot undo an already executed action.','parameters':{'type':'object','properties':{},'additionalProperties':False}},
       {'name':'check_last_action','description':'Independently inspect the last action outcome, including an interrupted or uncertain action. Never repeats the action.','parameters':{'type':'object','properties':{},'additionalProperties':False}}]

class Router:
    def __init__(self,ordinary,tasks,events):
        self.ordinary=ordinary;self.tasks=tasks;self.events=events;self.controller=None;self.failed=asyncio.Event();self.blocked=False;self.workflow=None
        tasks.current=self.current
        self.actions=Actions(tasks,tasks.config['actions'],tasks.budget,tasks.journal,events,self.current,allowed=getattr(tasks,'allowed_actions',[]))
    def generation(self):return self.controller.clock.current if self.controller else 1
    def current(self,g):return self.controller.current(g) if self.controller else True
    @property
    def pending(self):return self.actions.pending
    @pending.setter
    def pending(self,value):
        if value is not None:raise ValueError('pending_actions_owned_by_controller')
        self.abandon_goal()
    def abandon_goal(self):
        if self.workflow:self.workflow.stop('canceled',self.generation())
        self.actions.cancel()
    def declarations(self):
        tools=self.tasks.declarations()+self.actions.declarations()
        return tools+([goal_declaration({d['name']:d for d in tools},self.actions.allowed)] if tools else [])+LOCAL
    def model_declarations(self,declarations,selection=None):
        # Keep existing controller aliases validated for compatibility, but give
        # the model one effect-planning surface instead of competing entry points.
        tools=[d for d in declarations if d['name'] not in self.actions.adapters]
        if getattr(self.ordinary,'supports_schema_loading',False):return (selection or Selection(tools)).declarations()
        return tools
    def validated_calls(self,content,calls,schemas,g):
        if len(calls)>self.tasks.config['runtime']['batch_calls']:raise ValueError('batch_limit')
        for c in calls:
            if c.name not in schemas:raise ValueError('tool_not_permitted')
            Draft202012Validator(schemas[c.name]).validate(dict(c.args or {}))
        if len(calls)>1 and all(c.name==GOAL_TOOL for c in calls):
            # Several ordered model plans become ONE queue. No calls run here;
            # the whole queue still validates before its first proposal/effect.
            steps=[s for c in calls for s in dict(c.args)['steps']]
            args={'steps':steps};Draft202012Validator(schemas[GOAL_TOOL]).validate(args)
            call=types.FunctionCall(id=calls[0].id,name=GOAL_TOOL,args=args)
            self.events.emit('assistant_goal_calls_coalesced',generation=g,calls=len(calls),steps=len(steps),execution_started=False)
            calls=[call];content=types.Content(role='model',parts=[types.Part(function_call=call)])
        if any(c.name in self.actions.adapters or c.name in {'cancel_pending_action',GOAL_TOOL} for c in calls) and len(calls)!=1:raise ValueError('action_or_goal_must_be_separate')
        return content,calls
    async def confirmation(self,history,g,clarify_only=False):
        workflow=self.workflow
        operation=self.pending['operation_id'] if self.pending else None
        try:reply=await self.actions.confirm(history,g,clarify_only=clarify_only)
        except asyncio.CancelledError:
            if workflow:workflow.stop('canceled',g)
            raise
        if workflow is None or operation is None or operation!=workflow.waiting:return reply
        result=await workflow.confirmed(operation,g,reply)
        if isinstance(result,str):return result
        # Actual action adapters already produced independently checked replies.
        # A second model call cannot add evidence and can fail after an effect.
        if workflow.status=='completed' and all(s=='verified' for s in workflow.states):
            replies=[r['verified_reply'] for r in workflow.results]
            summary=' '.join(replies)
            if len(summary)>650:summary=f'{len(replies)} planned actions completed and were independently checked. '+reply
            self.events.emit('assistant_goal_verified_summary',generation=g,actions=len(replies),model_request=False)
            return validate_text(summary)
        # One ordinary summary loop receives real results/verification states.
        # No action is retried; a duplicated goal call reuses its completed result.
        args={'steps':workflow.steps};content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(id='goal_'+workflow.id,name=GOAL_TOOL,args=args))])
        continuation=[content,types.Content(role='user',parts=[types.Part.from_function_response(name=GOAL_TOOL,response={'result':result})])]
        completed=dict(workflow.completed);completed[json.dumps([GOAL_TOOL,args],sort_keys=True)]=result
        return await self.run_tools(history,g,self.declarations(),continuation,completed)
    async def answer(self,history):
        g=self.generation();raw=latest_text(history);text=normalized(raw)
        from me3_assistant.voice_policy import ends_voice_session
        stop_voice=getattr(self,'request_voice_session_stop',None)
        if callable(stop_voice) and ends_voice_session(raw):
            self.abandon_goal() # Cancel only uncommitted/obsolete work.
            stop_voice(g)
            # The owner now closes capture/MCP/playout in order. Do not synthesize
            # an acknowledgment or let the turn return to ordinary Listening.
            raise asyncio.CancelledError()

        # An uncertain one-word question is not permission for an effect.
        # ASR can add inverted question punctuation; acknowledgments may ignore
        # that surface, but consequential confirmation must remain conservative.
        if any(mark in raw for mark in ('?','¿')) and normalized(raw.strip().lstrip('¿?').strip()) in CONFIRM_REPLY:
            return await self.confirmation(history,g,clarify_only=True)
        if text in CONFIRM_REPLY:return await self.confirmation(history,g)
        if text in CANCEL|{'stop','stop talking','stop generating','stop responding','be quiet'}:
            was_pending=self.pending is not None;self.abandon_goal()
            return 'Canceled the unconfirmed proposal.' if was_pending else 'Stopped. Previously completed actions were not undone.'
        if text in NEUTRAL_ACKS:
            if self.pending is not None:return await self.confirmation(history,g,clarify_only=True)
            self.abandon_goal();self.workflow=None
            self.events.emit('assistant_social_acknowledgment',generation=g,reasoning_request=False,action_authorization=False)
            return 'Okay.'
        self.abandon_goal();self.workflow=None # A replacement request cannot retain old authorization or queued work.
        return await self.run_tools(history,g,self.declarations(),[],{})
    async def run_tools(self,history,g,declarations,continuation,completed):
        schemas={d['name']:d['parameters'] for d in declarations}
        selection=Selection([d for d in declarations if d['name'] not in self.actions.adapters]) if getattr(self.ordinary,'supports_schema_loading',False) else None
        if selection:schemas[SCHEMA_TOOL]=selection.directory()['parameters']
        for step in range(self.tasks.config['runtime']['tool_rounds']):
            if not self.current(g):return ''
            # Preserve one permitted decision to explain actual observations.
            # Direct reads need this boundary too, not only run_goal_steps.
            # Other read dependencies retain tools while another decision fits.
            remaining=getattr(self.tasks.budget,'remaining',None)
            last_request=bool(completed) and callable(remaining) and remaining('reasoning')==1
            final_only=(self.workflow is not None and self.workflow.status=='completed'
                        or bool(completed) and (last_request or step==self.tasks.config['runtime']['tool_rounds']-1))
            available=[] if final_only else self.model_declarations(declarations,selection)
            capacity_final=getattr(self.ordinary,'requires_final_reply',None)
            if not final_only and completed and callable(capacity_final) and capacity_final(history,available,continuation):
                final_only=True;available=[]
                self.events.emit('assistant_capacity_final_reply_policy',generation=g,executable_schemas_removed=True,request_and_source_observations_preserved=True,admission_not_attempted=True,automatic_retry=False)
            if final_only:self.events.emit('assistant_goal_summary_policy',generation=g,available_tools=0,new_tool_execution_permitted=False)
            try:content,calls,speech=await self.ordinary.decide(history,available,continuation)
            except asyncio.CancelledError:raise
            except Exception as exc:
                code=getattr(exc,'code',None)
                json_rejection=type(exc) is ValueError and str(exc)=='invalid_json_object_decision'
                final_rejection=final_only and type(exc) is ValueError and str(exc)=='invalid_final_speech'
                planning_rejection=(code==400 and getattr(exc,'provider_error_code',None) in {'tool_use_failed','json_validate_failed'}
                                    or json_rejection)
                recoverable=(isinstance(exc,TimeoutError) or code in {500,502,503,504}
                             or final_rejection or planning_rejection or type(exc) is ValueError and str(exc)=='empty_assistant_decision')
                # Quota/authentication, protocol and unexpected faults retain
                # their existing stop behavior. Never silently retry a request.
                if not recoverable:raise
                if not self.current(g):return ''
                self.events.emit('assistant_reasoning_recoverable_failure',generation=g,code=code,error_type=type(exc).__name__,completed_read_steps=len(completed),automatic_retry=False)
                # Keep this rejection notice in the ordinary single-part path.
                if final_rejection:return 'The planned steps completed, but I could not produce the final explanation. I will not repeat the steps. Previously completed actions were not undone.'
                if json_rejection:return 'I could not plan safely. No new action was started. Please clarify; earlier completed actions were not undone.'
                if planning_rejection:return 'I could not safely plan that request. No new action was started. I will not retry automatically. You can clarify the request; earlier completed actions were not undone.'
                verified=sum(s=='verified' for s in self.workflow.states) if self.workflow and self.workflow.status=='completed' else 0
                prefix=f'The goal steps completed; {verified} actions were independently checked. ' if verified else ''
                return prefix+'The reasoning service could not finish. I will not retry automatically. Please give your next request.'
            if not self.current(g):return ''
            if final_only and calls:
                self.events.emit('assistant_goal_summary_rejected',generation=g,reason='new_call_after_completed_goal',automatic_retry=False)
                return 'The planned steps completed, but I could not produce the final explanation. I will not repeat the steps. Please give your next request.'
            if not calls:
                model_reply_characters=len(speech)
                speech,readbacks=deliver_results(self.tasks,completed,speech)
                if readbacks:self.events.emit('assistant_result_readback',generation=g,results=readbacks,source='completed_owned_calculator',automatic_retry=False,model_reply_characters=model_reply_characters,delivered_characters=len(speech),policy='preserve_non_arithmetic_sentences')
                return validate_text(speech[:650]) if speech.strip() else 'Please clarify what you would like me to do.'
            try:
                if selection:
                    visible={d['name'] for d in available}
                    if any(c.name not in visible for c in calls) or any(c.name==SCHEMA_TOOL for c in calls) and len(calls)!=1:raise ValueError('operation_schema_not_loaded')
                content,calls=self.validated_calls(content,calls,schemas,g)
                # Validate EVERY planned tool/schema before any plan step runs.
                for c in calls:
                    if c.name==GOAL_TOOL:
                        args=dict(c.args or {});key=json.dumps([GOAL_TOOL,args],sort_keys=True)
                        if key not in completed:
                            self.workflow=Workflow(self,args['steps'],{n:s for n,s in schemas.items() if n not in {GOAL_TOOL,SCHEMA_TOOL,*[d['name'] for d in LOCAL]}},completed)
            except Exception:
                self.events.emit('assistant_tool_rejected',generation=g,reason='schema_or_permission')
                return 'I could not safely interpret that tool request. Please clarify the details.'
            continuation.append(content);responses=[]
            for c in calls:
                if not self.current(g):return ''
                args=dict(c.args or {});self.events.emit('assistant_tool_selected',generation=g,tool=c.name,step=step)
                if selection and c.name==SCHEMA_TOOL:
                    result=selection.load(args['names'])
                    self.events.emit('assistant_operation_schemas_loaded',generation=g,names=args['names'],executed_operations=0)
                    responses.append(types.Part(function_response=types.FunctionResponse(id=c.id,name=c.name,response={'result':result})));continue
                if c.name==GOAL_TOOL:
                    key=json.dumps([GOAL_TOOL,args],sort_keys=True)
                    if key not in completed:
                        outcome=await self.workflow.advance(g)
                        if isinstance(outcome,str):return outcome
                        if self.workflow.status=='completed' and all(x=='verified' for x in self.workflow.states):
                            reply=' '.join(x['verified_reply'] for x in self.workflow.results)
                            self.events.emit('assistant_goal_verified_summary',generation=g,actions=len(self.workflow.results),model_request=False)
                            return validate_text(reply if len(reply)<=650 else f'{len(self.workflow.results)} local actions completed and were independently checked.')
                        completed[key]=outcome
                    responses.append(types.Part(function_response=types.FunctionResponse(id=c.id,name=c.name,response={'result':completed[key]})));continue
                if c.name in self.actions.adapters:return await self.actions.propose(c.name,args,g)
                if c.name=='cancel_pending_action':
                    self.actions.cancel();return 'Canceled any unconfirmed proposal. Completed actions were not undone.'
                key=json.dumps([c.name,args],sort_keys=True)
                try:
                    if key not in completed:
                        result=await self.actions.status(g) if c.name=='check_last_action' else await self.tasks.read(c.name,g,args)
                        remember_result(self.tasks,completed,c.name,args,result)
                    if not self.current(g):return ''
                    passage=verbatim_passage(self.tasks,c.name,args,completed[key],completed) if len(calls)==1 else None
                    if passage is not None:
                        self.events.emit('assistant_verbatim_passage',generation=g,characters=len(passage),source='owned_library_read',rewriting_model_round=False)
                        return validate_text(passage)
                    responses.append(types.Part(function_response=types.FunctionResponse(id=c.id,name=c.name,response={'result':completed[key]})))
                except asyncio.CancelledError:raise
                except Exception:
                    self.events.emit('assistant_tool_failed',generation=g,tool=c.name,automatic_retry=False)
                    return 'A tool could not finish that request. Earlier completed steps were not undone. You can correct the request or ask another question.'
            continuation.append(types.Content(role='user',parts=responses))
        return 'I reached the configured tool-step limit. Completed steps were not undone. Please break the remaining work into a smaller request.'
