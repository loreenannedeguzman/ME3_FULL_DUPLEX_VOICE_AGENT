"""Bounded in-memory reply delivery state; no model, devices, effects or persistence."""
import copy,re,uuid
from onit_candidate.answer_guard import clean_answer
from onit_candidate.protocol import CandidateError,MAX_RESPONSE_CHARS
from onit_candidate.presentation import spoken_text
NAME='resume_previous_reply'
DECL=dict(name=NAME,description='Resume the remaining previously generated reply only when the latest user asks to continue it, including while music plays. Complete and independently verify requested music controls first, then call this operation alone. Does not play music or undo actions; transfers the exact retained text to the current speech owner without another model rewrite.',parameters=dict(type='object',properties={},additionalProperties=False))
def validate_snapshot(value):
 if value is None:return None
 if not isinstance(value,dict) or set(value) not in ({'token','text'},{'token','text','speech_segments'}) or not isinstance(value['token'],str) or not re.fullmatch('[0-9a-f]{32}',value['token']) or not isinstance(value['text'],str) or not value['text'].strip() or len(value['text'])>MAX_RESPONSE_CHARS:raise CandidateError('invalid_retained_reply')
 clean_answer(value['text'])
 if 'speech_segments' in value:
  from onit_candidate.speech_presentation import validate_segments
  validate_segments(value['speech_segments'],text=value['text'])
 return copy.deepcopy(value)
def validate_batch(calls,snapshot):
 if snapshot is None or len(calls)!=1 or calls[0].get('name')!=NAME or calls[0].get('args')!={}:raise CandidateError('retained_reply_must_be_alone_and_host_bound')
class RetainedReplies:
 def __init__(self):self.entries=[]
 def remember(self,result,generation):
  if result.get('status') in {'waiting_confirmation','reading_held'}:return
  speech_segments=None
  if 'speech_segments' in result:
   from onit_candidate.speech_presentation import validate_segments,project
   validate_segments(result['speech_segments'],text=clean_answer(result['answer']))
   if result.get('speech_projection') not in {'source','spoken'}:raise CandidateError('invalid_host_speech_projection')
   speech_segments=project(result['speech_segments'],already_projected=result['speech_projection']=='spoken');text=''.join(row['text'] for row in speech_segments)
  else:text=spoken_text(clean_answer(result['answer']))
  if not text.strip() or len(text)>MAX_RESPONSE_CHARS:raise CandidateError('invalid_retained_reply')
  token=result.get('resumed_reply_token')
  if token is not None:
   original=next((r for r in self.entries if r['token']==token),None)
   if original is None or original['text'][original['offset']:]!=result['answer']:raise CandidateError('retained_reply_transfer_changed')
   if 'speech_segments' in original:
    from onit_candidate.speech_presentation import suffix
    if result.get('speech_projection')!='spoken' or speech_segments!=suffix(original['speech_segments'],original['offset']):raise CandidateError('retained_reply_language_transfer_changed')
   elif speech_segments is not None:raise CandidateError('retained_reply_language_transfer_changed')
   original['offset']=len(original['text']) #Transferred, not claimed acoustically heard.
  self.entries=(self.entries+[dict(token=uuid.uuid4().hex,generation=generation,text=text,offset=0,**(dict(speech_segments=speech_segments) if speech_segments is not None else {}))])[-4:]
 def submitted(self,generation,prefix):
  entry=next((r for r in reversed(self.entries) if r['generation']==generation),None)
  if entry is None:return
  if not entry['text'].startswith(prefix) or not entry['offset']<=len(prefix)<=len(entry['text']):raise CandidateError('retained_reply_progress_changed')
  entry['offset']=len(prefix)
 def snapshot(self):
  entry=next((r for r in reversed(self.entries) if r['text'][r['offset']:].strip()),None)
  if entry is None:return None
  result=dict(token=entry['token'],text=entry['text'][entry['offset']:])
  if 'speech_segments' in entry:
   from onit_candidate.speech_presentation import suffix
   result['speech_segments']=suffix(entry['speech_segments'],entry['offset'])
  return result
 def metadata(self):
  return dict(available=self.snapshot() is not None,remaining_characters=[len(r['text'])-r['offset'] for r in self.entries if r['text'][r['offset']:].strip()],scope='in-memory generated reply; submitted prefixes are not acoustic proof; continuation requires current user intent and native tool selection')
 def clear(self):self.entries=[]
