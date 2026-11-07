"""Bounded response parts from the sole Onit planner; buffering has no external effects."""
import json,re
from onit_candidate.protocol import CandidateError,MAX_RESPONSE_CHARS
NAME='emit_response_part'
MAX_PARTS=32
DECL=dict(name=NAME,description='Deliver every final answer through this response tool; short answers need one part with final=true, long answers need consecutive complete parts. Each part is at most1000characters and must end with sentence punctuation; use fewer words to finish the sentence rather than slicing at a character limit. Set final=true only on the last part that completes the requested answer. After the first part, only this tool is permitted; finish research/actions beforehand. These parts form ONE answer and do not run an external action.',parameters=dict(type='object',properties=dict(text=dict(type='string',minLength=1,maxLength=1000),final=dict(type='boolean')),required=['text','final'],additionalProperties=False))
POLICY=' Reply capacity is limited per model completion, not per user goal. Use permitted research/action tools when needed, then deliver EVERY final answer through emit_response_part. Do not deliver the answer as ordinary assistant content. A short answer uses one complete part with final=true. Long answers use consecutive compact sections (prefer100-150words or fewer, always<=1000characters). Each part must be a coherent paragraph or section ending with sentence punctuation. Never split a word or sentence between calls. Continue where the accepted text ends; never restart the opening or repeat a part. Preserve the full requested scope and duration across parts. Finish all research/actions before emitting the first part. Continue until the whole answer is complete; set final=true on its last part. Do not substitute a short summary for a requested long explanation. Each model response must call an offered tool; the response tool is available even when no external work is needed.'

def require_response_protocol(payload):
    if not any(t.get('type')=='function' and t.get('function')==DECL for t in payload.get('tools',[])):raise CandidateError('reply_protocol_declaration_required')
    # Keep the whole permitted tool set: this never forces a particular real
    # action or bypasses research. The model chooses a tool or final response.
    return {**payload,'tool_choice':'required'}
class ReplyBuffer:
    def __init__(self):self.parts=[];self.closed=False
    def validate(self,calls):
        if self.closed or len(calls)!=1 or calls[0].get('name')!=NAME:raise CandidateError('reply_part_batch_must_be_single_and_current')
        args=calls[0].get('args')
        if not isinstance(args,dict) or set(args)!={'text','final'} or not isinstance(args['text'],str) or not 1<=len(args['text'])<=1000 or type(args['final']) is not bool:raise CandidateError('invalid_reply_part')
        if not args['text'].strip() or len(self.parts)>=MAX_PARTS:raise CandidateError('reply_part_bound')
        # A mechanical boundary guard, not a claim that punctuation proves
        # semantic completeness. Never speak a known mid-word/sentence slice.
        if not re.search(r'''[.!?。！？][\s"'”’»\)\]\}*_`]*$''',args['text']):raise CandidateError('reply_part_sentence_boundary_required')
        if len(self.text)+len(args['text'])+(2 if self.parts else 0)>MAX_RESPONSE_CHARS:raise CandidateError('reply_total_bound')
    @property
    def text(self):return '\n\n'.join(self.parts)
    def accept(self,args):
        self.validate([dict(name=NAME,args=args)]);self.parts.append(args['text']);self.closed=args['final']
        return dict(accepted=True,part=len(self.parts),next_part=len(self.parts)+1,final=self.closed,total_characters=len(self.text),accepted_words=len(self.text.split()),continue_after_this_text=self.text[-400:] if not self.closed else '',instruction='Continue the same requested answer from this accepted prefix; do not restart or repeat. This buffer acknowledgement does not claim audible delivery.')
    def complete(self,answer):
        if self.parts and (not self.closed or answer!=self.text):raise CandidateError('incomplete_or_substituted_reply_parts')
        return answer

def compact_acknowledgments(payload,accepted):
    """Remove only redundant old host progress metadata; keep all answer text.

    Native assistant arguments, user/source messages, external tool results
    and the latest continuity acknowledgment remain byte-for-byte unchanged.
    IDs and original content must match this engine's accepted response parts.
    This is deterministic request packing, not model context summarization.
    """
    if len(accepted)<2:return payload,0
    known={(identity,pair[0]):pair[1] for identity,pair in accepted};latest=(accepted[-1][0],accepted[-1][1][0]);messages=[];changed=0
    for message in payload['messages']:
        identity=message.get('tool_call_id')
        if message.get('role')=='tool' and message.get('name')==NAME:
            key=(identity,message.get('content'))
            if key not in known:raise CandidateError('reply_acknowledgment_history_changed')
            if key!=latest:
                message={**message,'content':known[key]};changed+=1
        messages.append(message)
    return {**payload,'messages':messages},changed

def acknowledgment_pair(data):
    return (json.dumps(data,ensure_ascii=False),json.dumps({k:data[k] for k in ('accepted','part','final')},separators=(',',':')))
