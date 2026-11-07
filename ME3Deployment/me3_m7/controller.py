"""Conservative split policy; transient separate clips, no guessed words or model-authorized writes."""
import asyncio,base64,re,time
from speech_mcp.controller import TurnController
from task_mcp.reasoner import normalized,CONFIRM
from me3_m7.client import RecoverableASRTimeout
WINDOW=8.0
INCOMPLETE={'please tell me','tell me','what is the capital','what is the capital of','what is','what','where is','add','read my','add five'}
def incomplete(text):
    n=normalized(text)
    return n in INCOMPLETE or bool(re.fullmatch(r'(?:please )?(?:add|put|save) .+ (?:to|on)(?: my| the)?',n))
def name_fragment(text):
    words=text.strip().rstrip('?.!').split()
    blocked={'what','where','who','why','how','when','which','please','actually','instead','stop','cancel','confirm','yes','no','read','add','put','save','name','count','tell','start','turn','delete','buy','book','send','call','email','open','close','remove','play','hello','hi'}
    return bool(1<=len(words)<=4 and words[0].lower() not in blocked and all(all(part.isalpha() and part[0].isupper() for part in word.split('-')) for word in words))
def fragment_shape(text):
    if not text.strip():return 'empty'
    if normalized(text).startswith('of '):return 'of_argument'
    return 'bare_name' if name_fragment(text) else 'other'
def resolve(previous,current):
    n=normalized(current);old=normalized(previous or '')
    if n in CONFIRM and previous:return 'ambiguous',None
    replace=re.match(r'^(?:actually|instead|stop)(?:[,.:! ]+)(.+)$',current.strip(),re.I)
    if replace:
        if normalized(replace[1]) in CONFIRM:return 'ambiguous',None
        return ('incomplete' if incomplete(replace[1]) else 'replacement'),replace[1].strip()
    if old in {'what is the capital','what is the capital of'} and re.fullmatch(r'(?:of )?[a-z][a-z -]{0,45}[?.!]*',current.strip(),re.I) and (n.startswith('of ') or old.endswith(' of') or name_fragment(current)):
        return 'continuation',previous.rstrip(' .!?')+' '+current.strip()
    if n.startswith(('of ','to ','and ','with ')):return 'ambiguous',None
    if incomplete(current):return 'incomplete',current
    if previous and not re.match(r'^(?:what|where|who|how|why|name|count|read|add|put|save|hello|hi|cancel|no|yes)\b',n):return 'ambiguous',None
    return 'replacement' if previous else 'complete',current
class M7TurnController(TurnController):
    def __init__(self,clients,provider,events,play_audio,max_turns=8):
        super().__init__(clients,provider,events,play_audio,max_turns);self.real_clients=clients;self.slot=None;self.saved=None
        owner=self
        class Proxy:
            def __getattr__(self,name):return getattr(clients,name)
            async def call(self,role,generation,**kw):return await owner.recognize(generation,**kw) if role=='asr' else await clients.call(role,generation,**kw)
        self.clients=Proxy()
    def interrupt(self):
        self.saved=None;slot=self.slot
        if slot and time.monotonic()-slot['submitted']<=WINDOW and slot['stage'] in {'asr','incomplete'}:
            self.saved={'generation':slot['generation'],'pcm':slot['pcm'] if slot['stage']=='asr' else None,'text':slot.get('recognized'),'submitted':slot['submitted']}
            self.events.emit('m7_prefix_retained_in_memory',generation=slot['generation'],byte_count=len(slot['pcm']) if slot['stage']=='asr' else 0,known_text=slot['stage']=='incomplete')
        return super().interrupt()
    def submit(self,pcm,generation):
        candidate=self.saved;self.saved=None
        self.slot={'generation':generation,'pcm':pcm,'stage':'asr','submitted':time.monotonic(),'candidate':candidate};super().submit(pcm,generation)
    def abandon_unconfirmed(self,generation):
        if hasattr(self.provider,'pending'):self.provider.pending=None
        self.events.emit('m7_unconfirmed_action_abandoned',generation=generation)
    async def clarify(self,generation):
        self.abandon_unconfirmed(generation)
        await self.speak('Please repeat your complete request in one phrase.',generation,'normal');return {'text':''}
    async def recognize(self,generation,**arguments):
        slot=self.slot;candidate=slot.get('candidate') if slot and slot['generation']==generation else None
        previous_text=candidate.get('text') if candidate else None
        if candidate and candidate.get('pcm'):
            if time.monotonic()-candidate['submitted']<=WINDOW and len(candidate['pcm'])+len(slot['pcm'])<=480000:
                arguments.update(previous_pcm_b64=base64.b64encode(candidate['pcm']).decode(),previous_generation=candidate['generation'])
                self.events.emit('m7_separate_clip_request',generation=generation,previous_generation=candidate['generation'],previous_bytes=len(candidate['pcm']))
            else:candidate=None
        try:data=await self.real_clients.call('asr',generation,**arguments)
        except RecoverableASRTimeout:
            if slot:slot['stage']='failed'
            if self.current(generation):
                self.abandon_unconfirmed(generation);self.events.emit('m7_timeout_notice',generation=generation)
                await self.speak('That speech request timed out. Please try one complete request.',generation,'normal',error_notice=True)
            return {'text':''}
        if not self.current(generation):return data
        if arguments.get('previous_pcm_b64'):previous_text=data.get('previous_text')
        text=data['text'];decision,resolved=resolve(previous_text,text)
        self.events.emit('m7_segment_decision',generation=generation,decision=decision,previous_chars=len(previous_text or ''),current_chars=len(text),joined_only_explicit_grammar=decision=='continuation',prefix_supported_for_continuation=normalized(previous_text or '') in {'what is the capital','what is the capital of'},fragment_shape=fragment_shape(text),connector_words_inserted=False)
        if not text:
            slot['stage']='empty'
            if previous_text and previous_text.strip():
                self.events.emit('m7_empty_current_with_retained_prefix',generation=generation,previous_chars=len(previous_text),action='ask_full_repetition_no_execution')
                return await self.clarify(generation)
            return data
        if decision=='ambiguous':slot['stage']='ambiguous';return await self.clarify(generation)
        if decision=='incomplete':
            slot.update(stage='incomplete',recognized=resolved);self.abandon_unconfirmed(generation)
            self.events.emit('m7_incomplete_prefix_wait',generation=generation,window_seconds=6)
            await asyncio.sleep(6)
            if self.current(generation):slot['stage']='expired';return await self.clarify(generation)
            return {'text':''}
        slot['stage']='reasoning';data['text']=resolved;return data
    async def close(self):
        try:await super().close()
        finally:self.slot=None;self.saved=None
