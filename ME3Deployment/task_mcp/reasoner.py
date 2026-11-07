"""Small deterministic task policy around accepted reasoning. Real ASR text, no model authorization."""
import asyncio,re,uuid
from task_mcp.store import item_value,quantity_value,state_digest
WORDS=['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen','twenty']
CONFIRM={'confirm','yes','yes please','yes confirm'}
CANCEL={'cancel','no','no thanks','never mind','do not add it'}
def normalized(text):return ' '.join(text.lower().strip().rstrip('.!?').split())
def parse_item(text):
    text=normalized(text)
    if not text:raise ValueError('missing_item')
    parts=text.split(' ',1);quantity=1
    if parts[0] in WORDS+['a','an'] or parts[0].isdigit():
        quantity=int(parts[0]) if parts[0].isdigit() else WORDS.index(parts[0]) if parts[0] in WORDS else 1
        if len(parts)!=2:raise ValueError('missing_item')
        text=parts[1]
    if text.split()[0] in {'what','where','why','how','who','count'}:raise ValueError('not_item_detail')
    return item_value(text),quantity_value(quantity)
def latest_text(history):return history[-1]['content'][0]['text']
def prompt_delivered(history,prompt):
    if len(history)<2:return False
    previous=history[-2]
    return previous.get('type')=='model_output' and not previous.get('control_state') and not previous.get('partial_output') and previous.get('content')==[{'type':'text','text':prompt}]

class TaskReasoner:
    def __init__(self,ordinary,tasks,events):
        self.ordinary=ordinary;self.tasks=tasks;self.events=events;self.controller=None;self.pending=None;self.uncertain=None;self.last_verified=None;self.failed=asyncio.Event();self.blocked=False
    def generation(self):return self.controller.clock.current if self.controller else 1
    def current(self,g):return self.controller.current(g) if self.controller else True
    def describe(self,row):return WORDS[row['quantity']]+' '+row['item']
    async def prepare(self,detail,g):
        try:item,quantity=parse_item(detail)
        except ValueError:return 'Please say a quantity from one to twenty and one simple item, such as two notebooks.'
        proposal=await self.tasks.call('prepare_item',g,item=item,quantity=quantity,operation_id=uuid.uuid4().hex)
        if not self.current(g):return ''
        prompt='I will save '+self.describe(proposal)+' to your Pi shopping list. Say confirm to save it, or cancel.'
        self.pending={**proposal,'phase':'confirm','prompt':prompt}
        self.events.emit('task_confirmation_requested',generation=g,operation_id=proposal['operation_id'])
        return prompt
    async def verify(self,proposal,g):
        data=await self.tasks.call('read_item',g,operation_id=proposal['operation_id']);row=data.get('row')
        if not row or row.get('operation_id')!=proposal['operation_id'] or row.get('item')!=proposal['item'] or type(row.get('quantity')) is not int or row['quantity']!=proposal['quantity'] or data.get('state_digest')!=state_digest(row) or data.get('verification_source')!='fresh_readonly_sqlite_connection':raise ValueError('task_verification_failed')
        self.last_verified=row
        self.events.emit('task_independently_verified',generation=g,operation_id=row['operation_id'],state_digest=data['state_digest'],verification_source=data['verification_source'])
        return row
    async def execute(self,proposal,g):
        if not self.current(g):return ''
        self.pending=None
        try:
            self.events.emit('task_human_confirmation',generation=g,operation_id=proposal['operation_id'],source='direct_current_asr_exact_confirmation')
            await self.tasks.call('commit_item',g,operation_id=proposal['operation_id'],token=proposal['token'],confirmation='confirm')
            if not self.current(g):self.uncertain=proposal;return ''
            row=await self.verify(proposal,g)
            if not self.current(g):return ''
            return 'I saved and independently checked '+self.describe(row)+' in your Pi shopping list. Say read my shopping list to check it.'
        except asyncio.CancelledError:self.uncertain=proposal;raise
    async def answer(self,history):
        text=normalized(latest_text(history));g=self.generation();ordinary_stage=False
        if self.blocked:return 'The task has stopped. No successful outcome is being claimed.'
        try:
            if self.uncertain:
                proposal=self.uncertain
                data=await self.tasks.call('read_item',g,operation_id=proposal['operation_id'])
                if data.get('row'):await self.verify(proposal,g)
                self.uncertain=None
                self.events.emit('task_cancel_outcome_reconciled',generation=g,operation_id=proposal['operation_id'],exists=bool(data.get('row')),automatic_write_retry=False)
                if text in CONFIRM:return 'That save was interrupted. I checked its state without retrying it. Say read my shopping list for the result.'
            if text in CANCEL:
                self.pending=None;return 'Canceled. I will not add that item. You can request another shopping-list item.'
            match=re.fullmatch(r'(?:please )?(?:add|put|save)(?: (.*?))? (?:to|on) (?:my|the)(?: pi)? shopping list',text)
            if match:
                self.pending=None;detail=match.group(1)
                if not detail:
                    self.pending={'phase':'item'};return 'What item, and how many? For example, say two notebooks.'
                return await self.prepare(detail,g)
            if self.pending and self.pending['phase']=='item':
                if text.split()[0] not in {'what','where','why','how','who','count'}:return await self.prepare(text,g)
                self.pending=None
            if self.pending and self.pending['phase']=='confirm':
                if text in CONFIRM:
                    if not prompt_delivered(history,self.pending['prompt']):
                        self.events.emit('task_confirmation_prompt_not_completed',generation=g,operation_id=self.pending['operation_id']);return self.pending['prompt']
                    return await self.execute(self.pending,g)
                self.pending=None # An unrelated latest request abandons the unconfirmed task.
            if text in CONFIRM:
                return 'There is no pending action to confirm. I will not add a duplicate. Say read my shopping list to check it.'
            if text in {'read my shopping list','read the shopping list','what is on my shopping list','what did you add to my list','what did you add'}:
                data=await self.tasks.call('list_items',g);rows=data.get('rows')
                if not isinstance(rows,list) or len(rows)>100 or data.get('verification_source')!='fresh_readonly_sqlite_connection':raise ValueError('task_verification_failed')
                descriptions=[self.describe({'item':item_value(x['item']),'quantity':quantity_value(x['quantity'])}) for x in rows[:3]]
                return 'Your saved Pi shopping list is empty.' if not rows else 'Your saved Pi shopping list has '+', '.join(descriptions)+('. There are more items.' if len(rows)>3 else '.')
            ordinary_stage=True
            return await self.ordinary.answer(history)
        except asyncio.CancelledError:raise
        except Exception as exc:
            if ordinary_stage:raise
            self.pending=None;self.blocked=True;self.failed.set()
            self.events.emit('task_failure',generation=g,error_type=type(exc).__name__,success_claimed=False)
            return 'I could not verify the task outcome. I have stopped the task and will not retry it automatically.'
