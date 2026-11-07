"""Explicit evaluation annotations and conservative semantic ownership policy."""
import math,re
ACKNOWLEDGMENTS=frozenset({'mm-hmm','mm-hm','mhm','uh-huh','okay','ok'})
COMMANDS=frozenset({'stop','cancel','no','confirm','yes','wait'})
def annotation(text,duration_seconds):
    if not isinstance(text,str) or type(duration_seconds) not in (int,float) or not math.isfinite(duration_seconds) or duration_seconds<0:raise ValueError('invalid_utterance_annotation')
    # Hyphenated tokens count as one; spaces separate words. No merging ASR words.
    words=re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*",text)
    return {'word_count':len(words),'duration_seconds':duration_seconds,'brief_by_rule':duration_seconds<1 and len(words)<2,'duration_method':'VAD active span proxy excluding stop hysteresis; not acoustic duration','tokenizer':'ASCII alphanumeric tokens; internal hyphen/apostrophe retained'}
def listener_ack(text,duration_seconds,*,task_pending=False):
    note=annotation(text,duration_seconds)
    normalized=text.lower().strip().rstrip('.!?')
    return bool(note['brief_by_rule'] and note['word_count']==1 and normalized in ACKNOWLEDGMENTS and not task_pending)

# Explicit floor-holding discourse, not a guess at missing task arguments.
def holds_floor(text):
    # Explicit requests to retain the floor remain holds. Declarative stems
    # hold only when they are the whole utterance: a completed personal
    # statement beginning "I'm thinking ..." still belongs to the planner.
    value=text.strip()
    return bool(re.match(r"^(?:please )?let me (?:explain|think|finish)\b",value,re.I)
        or re.fullmatch(r"(?:please )?(?:i want to ask|i(?:'m| am) (?:trying to|thinking)|the reason (?:i|we))",value.rstrip(' .!?'),re.I))
def requests_reply(text):
    return bool(re.search(r"\b(?:what (?:is|are|was)|where (?:is|are)|how (?:do|can|should)|can you|could you|please (?:give|tell|help|explain|suggest|recommend)|give me|tell me)\b",text,re.I))
def resolve_segments(previous,current):
    from me3_m7.controller import resolve
    from task_mcp.reasoner import normalized,CONFIRM
    if previous and normalized(current) in CONFIRM:return 'ambiguous',None
    if re.match(r'^(?:actually|instead|stop)[,.:! ]+',current.strip(),re.I):return resolve(previous,current)
    if previous and holds_floor(previous):
        joined=previous.rstrip()+' '+current.strip()
        return ('continuation' if requests_reply(current) else 'incomplete'),joined
    if holds_floor(current) and not requests_reply(current):return 'incomplete',current
    if previous and re.match(r"^(?:i have|i am|my)\b",previous.strip(),re.I) and re.match(r'^(?:please )?(?:give|tell|help|explain|suggest|recommend)\b',current.strip(),re.I):return 'continuation',previous.rstrip()+' '+current.strip()
    return resolve(previous,current)
