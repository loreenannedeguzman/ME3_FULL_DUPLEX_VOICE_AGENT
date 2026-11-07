"""Schema-bound two-clip ASR; inherited account, trace, single-wire guard and pool ownership."""
import asyncio,json,os
from google.genai import types
from me3_core.pool import ScopedGoogle
from me3_m7.codec import encode
from speech_mcp.provider import ASR_MODEL
SEGMENT_SCHEMA={'type':'object','properties':{'previous_text':{'type':'string','maxLength':1000},'text':{'type':'string','maxLength':1000}},'required':['previous_text','text'],'additionalProperties':False}
def segment_config(profile):
    if profile not in {'low','minimal'}:raise ValueError('invalid_asr_profile')
    return types.GenerateContentConfig(max_output_tokens=1024,thinking_config=types.ThinkingConfig(thinking_level=profile.upper()),response_mime_type='application/json',response_json_schema=SEGMENT_SCHEMA)
def parse_segments(raw,emit):
    def reject(reason):
        emit('completion_asr_response_rejected',reason=reason,response_retained=False)
        raise ValueError('invalid_segment_transcription')
    if not isinstance(raw,str) or len(raw)>14000:return reject('text_type_or_size')
    def unique(pairs):
        value={}
        for k,v in pairs:
            if k in value:raise ValueError('duplicate_json_key')
            value[k]=v
        return value
    try:data=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,TypeError):return reject('json_or_duplicate_key')
    if not isinstance(data,dict) or set(data)!={'text','previous_text'}:return reject('object_fields')
    if any(not isinstance(v,str) or len(v)>1000 for v in data.values()):return reject('field_type_or_size')
    emit('completion_asr_response_validated',text_chars=len(data['text']),previous_chars=len(data['previous_text']),response_retained=False)
    return data
class StructuredGoogle(ScopedGoogle):
    async def transcribe(self,pcm,profile,previous=None,audio_format='wav'):
        if previous is None:return await super().transcribe(pcm,profile,None,audio_format)
        config=segment_config(profile)
        self.emit('wav_encode_start');current,mime=await encode(pcm,audio_format);old,old_mime=await encode(previous,audio_format)
        if old_mime!=mime:raise ValueError('segment_mime_mismatch')
        contents=[types.Part.from_text(text='Transcribe two separately labeled audio clips exactly. Never answer or complete words. Use the required JSON fields: previous_text for the first clip and text for the CURRENT clip. Keep clips separate, preserve their original language, empty strings for unintelligible speech. Previous clip:'),types.Part.from_bytes(data=old,mime_type=mime),types.Part.from_text(text='CURRENT clip:'),types.Part.from_bytes(data=current,mime_type=mime)]
        self.emit('wav_encode_end',wav_bytes=len(current),audio_format=audio_format,separate_clips=2)
        self.emit('completion_asr_schema',schema='previous_text/text bounded strings; required; no extra properties')
        self.emit('provider_request_start',profile=profile,model=os.getenv('ASR_MODEL',ASR_MODEL))
        try:result=await self.provider.client.aio.models.generate_content(model=os.getenv('ASR_MODEL',ASR_MODEL),contents=contents,config=config)
        except asyncio.CancelledError:self.emit('provider_cancel_observed');raise
        except Exception as exc:self.emit('provider_error',error_type=type(exc).__name__);raise
        usage=getattr(result,'usage_metadata',None)
        self.emit('provider_response',thought_tokens=getattr(usage,'thoughts_token_count',None),output_tokens=getattr(usage,'candidates_token_count',None),wire_attempts=self.wire)
        try:raw=result.text
        except (ValueError,TypeError):
            self.emit('completion_asr_response_rejected',reason='response_text_access',response_retained=False);raise ValueError('invalid_segment_transcription') from None
        return parse_segments(raw,self.emit)
