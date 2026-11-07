"""Strict final-answer presentation data; no language detection or translation."""
import copy
from onit_candidate.protocol import CandidateError,MAX_RESPONSE_CHARS,decode
SUPPORTED=('en','es')
MAX_SEGMENTS=32
POLICY=' When the host advertises both English and Spanish speech, encode only your complete final answer prose as one JSON object with exactly one key, segments. This does not change tool arguments, native tool schemas or host policy. Its value is an ordered list of objects with exactly language and text. language is en or es, chosen by you for the language of that exact text; text is the literal answer content, including all requested code/notation/quotations. The concatenated text fields are the entire displayed and spoken answer, so include all whitespace and punctuation needed between segments. Use one segment for an ordinary single-language answer, and separate segments only when the actual answer changes language. Only English and Spanish voices are installed. Never label Tagalog or another unsupported spoken language as en or es to force readout. For a requested unsupported spoken language, explain in a supported language that its voice is unavailable; written content can still be drafted through the normal text or artifact tools without claiming its pronunciation. Do not translate an exact quotation or host review to fit a voice. Do not wrap this JSON in Markdown or add text outside it. Preserve the full requested answer and all existing verification rules; this presentation format grants no tool/action/confirmation authority and requires no extra model call.'
def fail(code):raise CandidateError(code)
def languages(value=None):
    if value is None:return ('en',)
    if not isinstance(value,(list,tuple)) or tuple(value) not in (('en',),SUPPORTED):fail('invalid_host_speech_languages')
    return tuple(value)
def validate_segments(value,*,available=SUPPORTED,text=None):
    allowed=languages(available)
    if not isinstance(value,list) or not 1<=len(value)<=MAX_SEGMENTS:fail('speech_segment_bound')
    result=[];size=0
    for row in value:
        if not isinstance(row,dict) or set(row)!={'language','text'} or type(row['language']) is not str or row['language'] not in allowed:fail('invalid_speech_segment_language')
        body=row['text']
        if not isinstance(body,str) or not body.strip() or '\x00' in body:fail('invalid_speech_segment_text')
        size+=len(body)
        if size>MAX_RESPONSE_CHARS:fail('speech_presentation_text_bound')
        result.append(dict(language=row['language'],text=body))
    joined=''.join(row['text'] for row in result)
    if text is not None and joined!=text:fail('speech_presentation_text_mismatch')
    return result
def parse_final(raw,available):
    if languages(available)!=SUPPORTED:fail('multilingual_presentation_not_advertised')
    try:value=decode(raw)
    except CandidateError:fail('invalid_final_speech_presentation')
    if not isinstance(value,dict) or set(value)!={'segments'}:fail('invalid_final_speech_presentation')
    result=validate_segments(value['segments'],available=available)
    return ''.join(row['text'] for row in result),result
def project(value,*,available=SUPPORTED,already_projected=False):
    if type(already_projected) is not bool:fail('speech_projection_flag_boolean_required')
    from onit_candidate.presentation import spoken_text
    rows=validate_segments(value,available=available)
    projected=[dict(language=row['language'],text=row['text'] if already_projected else spoken_text(row['text'],language=row['language'])) for row in rows]
    return validate_segments(projected,available=available)
def suffix(value,offset):
    rows=validate_segments(value);size=sum(len(row['text']) for row in rows)
    if type(offset) is not int or not 0<=offset<=size:fail('speech_presentation_offset_invalid')
    result=[]
    for row in rows:
        if offset>=len(row['text']):offset-=len(row['text']);continue
        result.append(dict(language=row['language'],text=row['text'][offset:]));offset=0
    # Submitted prefixes may stop immediately before a span boundary. Carry
    # whitespace onto the next voiced span without dropping display characters
    # or asking TTS to synthesize an empty phrase. Whitespace has no language.
    while len(result)>1 and not result[0]['text'].strip():
        prefix=result.pop(0)['text'];result[0]['text']=prefix+result[0]['text']
    # A whitespace-only final tail has no resumable speech, as in the existing
    # retained-reply selection, which requires remaining_text.strip().
    return result

# Fixed host-owned final prose shape; not model/tool-provided authority.
SCHEMA={'type':'object','properties':{'segments':{'type':'array','items':{'type':'object','properties':{'language':{'type':'string','enum':['en','es']},'text':{'type':'string','minLength':1,'maxLength':40000}},'required':['language','text'],'additionalProperties':False},'minItems':1,'maxItems':32}},'required':['segments'],'additionalProperties':False}
RESPONSE_FORMAT={'type':'json_schema','json_schema':{'name':'me3_speech_presentation','strict':True,'schema':SCHEMA}}

def response_format(available):
    if languages(available)!=SUPPORTED:fail('multilingual_presentation_not_advertised')
    return copy.deepcopy(RESPONSE_FORMAT)

def final_policy(policy):
    # Only the bilingual final-prose representation changes. Existing tool,
    # exact review, literal content and verification instructions remain.
    old='Give a useful, complete, concise final answer in ordinary text.'
    if not isinstance(policy,str) or policy.count(old)!=1:fail('host_final_policy_binding_changed')
    return policy.replace(old,'Give a useful, complete, concise final answer in the host-bound speech presentation format.',1)
