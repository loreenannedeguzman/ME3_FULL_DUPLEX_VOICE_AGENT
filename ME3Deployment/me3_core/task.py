"""Bounded complete list read-back; recover only explicit trial-proposal mismatches."""
import asyncio,re
from task_mcp.reasoner import TaskReasoner,latest_text,normalized
from task_mcp.store import item_value,quantity_value
from task_mcp.client import TaskError
class ProposalMismatch(TaskError):pass

def check_trial_proposal(events,generation,**arguments):
    item_ok=arguments.get('item')=='erasers'
    quantity_ok=type(arguments.get('quantity')) is int and arguments['quantity']==3
    events.emit('core_trial_proposal_validation',generation=generation,item_matches_expected=item_ok,quantity_matches_expected=quantity_ok,payload_retained=False)
    if not item_ok or not quantity_ok:raise ProposalMismatch('trial_proposal_mismatch')

def task_only_clarification(events,g):
    events.emit('core_task_only_clarification',generation=g,provider_called=False,automatic_retry=False)
    return 'Please say add to my shopping list. Nothing has been saved.'

class CoreTaskReasoner(TaskReasoner):
    async def prepare(self,detail,g):
        try:return await super().prepare(detail,g)
        except ProposalMismatch:
            if not self.current(g):return ''
            self.pending={'phase':'item'}
            self.events.emit('core_task_detail_clarification',generation=g,write_executed=False,automatic_retry=False)
            return 'Please say the quantity and item again. Nothing has been saved.'
    async def answer(self,history):
        text=normalized(latest_text(history))
        words=set(text.split());action=bool(re.match(r'^(?:please )?(?:add|put|save)\b',text))
        exact=bool(re.fullmatch(r'(?:please )?(?:add|put|save)(?: (.*?))? (?:to|on) (?:my|the)(?: pi)? shopping list',text))
        self.events.emit('core_task_route_shape',generation=self.generation(),action_prefix=action,shopping_and_list_tokens={'shopping','list'}<=words,exact_task_grammar=exact,content_retained=False)
        if not self.blocked and not self.uncertain and action and {'shopping','list'}<=words and not exact:
            self.pending={'phase':'item'}
            self.events.emit('core_task_intent_clarification',generation=self.generation(),action_executed=False,inferred_item=False)
            return 'What item, and how many? Nothing has been saved.'
        reads={'read my shopping list','read the shopping list','what is on my shopping list','what did you add to my list','what did you add'}
        if text not in reads or self.blocked or self.uncertain:return await super().answer(history)
        g=self.generation();self.pending=None
        try:
            data=await self.tasks.call('list_items',g);rows=data.get('rows')
            if not isinstance(rows,list) or len(rows)>100 or data.get('verification_source')!='fresh_readonly_sqlite_connection':raise ValueError('task_verification_failed')
            # Validate every row; speech stays below the existing700-character TTS bound.
            descriptions=[self.describe({'item':item_value(x['item']),'quantity':quantity_value(x['quantity'])}) for x in rows]
            visible=[]
            for value in descriptions[:8]:
                trial='Your saved Pi shopping list has '+', '.join(visible+[value])+'. There are one hundred more items.'
                if len(trial)>700:break
                visible.append(value)
            more=len(rows)-len(visible)
            reply='Your saved Pi shopping list is empty.' if not rows else 'Your saved Pi shopping list has '+', '.join(visible)+('. There are '+str(more)+' more items.' if more else '.')
            if not self.current(g):return ''
            self.events.emit('core_list_readback',generation=g,row_count=len(rows),spoken_count=len(visible),remaining_count=more)
            return reply
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.pending=None;self.blocked=True;self.failed.set()
            self.events.emit('task_failure',generation=g,error_type=type(exc).__name__,success_claimed=False)
            return 'I could not verify the task outcome. I have stopped the task and will not retry it automatically.'
