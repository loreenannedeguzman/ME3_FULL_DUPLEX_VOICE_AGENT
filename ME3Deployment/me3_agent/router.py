"""Generation-owned tool loop with deterministic write confirmation."""
import asyncio,json
from google.genai import types
from jsonschema import Draft202012Validator
from me3_completion.task import ConversationReasoner
from task_mcp.reasoner import normalized,latest_text,CONFIRM,CANCEL,WORDS
from task_mcp.store import item_value,quantity_value
from task_mcp.client import TaskError
from speech_mcp.contracts import validate_text
class AgentRouter(ConversationReasoner):
    async def answer(self,history):
        text=normalized(latest_text(history));g=self.generation()
        # Exact direct confirmation, cancellation and outcome reconciliation remain outside LLM authority.
        if self.blocked or self.uncertain or text in CONFIRM|CANCEL|{'stop','stop talking','be quiet'}:
            return await super().answer(history)
        declarations=self.tasks.declarations();schemas={x['name']:x['parameters'] for x in declarations};continuation=[]
        # Abandon old unconfirmed proposal before routing latest request. Corrections reconstruct a fresh one.
        previous=self.pending;self.pending=None
        if previous and previous.get('phase')=='confirm':
            self.events.emit('agent_previous_proposal_abandoned',generation=g,write_executed=False)
        for step in range(3):
            if not self.current(g):return ''
            content,calls,speech=await self.ordinary.decide(history,declarations,continuation)
            if not self.current(g):return ''
            if not calls:return validate_text(speech[:650]) if speech.strip() else 'Please clarify what you would like me to do.'
            if len(calls)>3:return 'Please request at most three tool operations at a time. Nothing has been saved.'
            # Validate the WHOLE batch before any operation. The model never supplies identity, tokens, or operation IDs.
            try:
                batch=[]
                for call in calls:
                    if call.name not in schemas:raise ValueError('unknown_tool')
                    args=dict(call.args or {});schema={**schemas[call.name],'additionalProperties':False}
                    Draft202012Validator(schema).validate(args);batch.append((call,args))
                if any(c.name in {'propose_shopping_item','cancel_pending_action'} for c,a in batch) and len(batch)!=1:raise ValueError('mixed_proposal_batch')
            except Exception:
                self.events.emit('agent_tool_decision_rejected',generation=g,reason='schema_or_policy',write_executed=False)
                return 'I could not safely interpret that tool request. Please clarify it. Nothing has been saved.'
            continuation.append(content);responses=[]
            for call,args in batch:
                if not self.current(g):return ''
                name=call.name;self.events.emit('agent_tool_selected',generation=g,tool=name,step=step,model_authorizes_write=False)
                if name=='cancel_pending_action':
                    return 'Canceled the unconfirmed proposal. Completed saved items remain in the list.'
                if name=='propose_shopping_item':
                    try:item=item_value(args['item']);quantity=quantity_value(args['quantity'])
                    except (KeyError,ValueError):return 'What item and quantity from one to twenty should I propose? Nothing has been saved.'
                    try:return await self.prepare(WORDS[quantity]+' '+item,g)
                    except asyncio.CancelledError:raise
                    except Exception:
                        self.pending=None;return 'I could not prepare that item. Nothing has been saved. Please try a corrected request.'
                try:
                    data=await self.tasks.call(name,g,**args)
                    if not self.current(g):return ''
                    # Strip metadata and bound tool data; keep raw results out of persistent transcript logs.
                    data={k:v for k,v in data.items() if k not in {'request_id','generation','server_pid','host','machine','server_role'}}
                    if len(json.dumps(data))>12000:raise TaskError('tool_result_limit')
                    responses.append(types.Part.from_function_response(name=name,response=data))
                except asyncio.CancelledError:raise
                except Exception:
                    self.events.emit('agent_tool_failure_recovered',generation=g,tool=name,automatic_retry=False)
                    return 'That tool could not complete the request. Please check the input or ask another question. No write was made.'
            continuation.append(types.Content(role='user',parts=responses))
        self.events.emit('agent_tool_round_limit',generation=g,automatic_retry=False)
        return 'I reached the tool-step limit. Please break the request into smaller steps. No item was saved.'
