"""Pinned SDK tracing; exact existing single-clip prompt/model, explicit LOW/MINIMAL only."""
import asyncio,json,os,time
from google.genai import types
from speech_mcp.provider import GoogleSpeech,ASR_MODEL
from me3_m7.codec import encode
PROMPT='Transcribe only the spoken words exactly in their original language. No commentary, no answers, no invented speech. Return empty text if no intelligible speech.'
class TracedGoogle:
    def __init__(self,emit):
        self.emit=emit;self.wire=0;emit('provider_create_start');self.provider=GoogleSpeech();emit('provider_create_end')
        api=self.provider.client._api_client;emit('sdk_transport',aiohttp=api._use_aiohttp(),configured_attempts=1)
        original=api._async_request_once
        async def once(*args,**kw):
            emit('sdk_attempt_start')
            try:return await original(*args,**kw)
            finally:emit('sdk_attempt_end')
        api._async_request_once=once
        if api._use_aiohttp():
            get=api._get_aiohttp_session
            async def session(*args,**kw):
                s=await get(*args,**kw)
                if not getattr(s,'_me3_traced',False):
                    request=s.request
                    async def traced(*a,**k):
                        if self.wire>=1:emit('sdk_hidden_retry_blocked');raise ValueError('sdk_hidden_retry_blocked')
                        self.wire+=1;emit('http_request_start',wire_attempt=self.wire,request_body_bytes=len(k.get('data') or b''))
                        try:
                            result=await request(*a,**k);emit('http_response_headers',wire_attempt=self.wire,status=result.status);return result
                        except asyncio.CancelledError:emit('http_cancel_observed',wire_attempt=self.wire);raise
                        except Exception as exc:emit('http_error',wire_attempt=self.wire,error_type=type(exc).__name__);raise
                    s.request=traced;s._me3_traced=True
                    if hasattr(s,'trace_configs'):
                        import aiohttp
                        trace=aiohttp.TraceConfig()
                        for label in ('connection_create_start','connection_create_end','dns_resolvehost_start','dns_resolvehost_end','request_headers_sent','request_chunk_sent','request_end','request_exception'):
                            async def mark(_session,_ctx,params,event_label=label):
                                emit('transport_'+event_label,**({'body_chunk_bytes':len(params.chunk)} if hasattr(params,'chunk') else {}))
                            getattr(trace,'on_'+label).append(mark)
                        trace.freeze();s.trace_configs.append(trace)
                return s
            api._get_aiohttp_session=session
        else:
            async def request(_):
                if self.wire>=1:emit('sdk_hidden_retry_blocked');raise ValueError('sdk_hidden_retry_blocked')
                self.wire+=1;emit('http_request_start',wire_attempt=self.wire)
            async def response(value):emit('http_response_headers',wire_attempt=self.wire,status=value.status_code)
            api._async_httpx_client.event_hooks['request'].append(request);api._async_httpx_client.event_hooks['response'].append(response)
    async def transcribe(self,pcm,profile,previous=None,audio_format='wav'):
        if profile not in {'low','minimal'}:raise ValueError('invalid_asr_profile')
        self.emit('wav_encode_start');wav,mime=await encode(pcm,audio_format)
        if previous is None:contents=[types.Part.from_text(text=PROMPT),types.Part.from_bytes(data=wav,mime_type=mime)]
        else:
            contents=[types.Part.from_text(text='Transcribe two separately labeled audio clips exactly. Never answer or complete words. Return JSON with only previous_text for the first clip and text for the CURRENT clip. Keep clips separate, preserve their original language, empty strings for unintelligible speech. Previous clip:'),types.Part.from_bytes(data=(await encode(previous,audio_format))[0],mime_type=mime),types.Part.from_text(text='CURRENT clip:'),types.Part.from_bytes(data=wav,mime_type=mime)]
        self.emit('wav_encode_end',wav_bytes=len(wav),audio_format=audio_format,separate_clips=2 if previous else 1)
        config=types.GenerateContentConfig(max_output_tokens=1024,thinking_config=types.ThinkingConfig(thinking_level=profile.upper()),**({'response_mime_type':'application/json'} if previous else {}))
        self.emit('provider_request_start',profile=profile,model=os.getenv('ASR_MODEL',ASR_MODEL))
        try:
            result=await self.provider.client.aio.models.generate_content(model=os.getenv('ASR_MODEL',ASR_MODEL),contents=contents,config=config)
        except asyncio.CancelledError:self.emit('provider_cancel_observed');raise
        except Exception as exc:self.emit('provider_error',error_type=type(exc).__name__);raise
        self.emit('provider_response',thought_tokens=getattr(result.usage_metadata,'thoughts_token_count',None),output_tokens=getattr(result.usage_metadata,'candidates_token_count',None),wire_attempts=self.wire)
        text=(result.text or '').strip()
        if previous is None:return {'text':text[:1000]}
        data=json.loads(text)
        if not isinstance(data,dict) or set(data)!={'text','previous_text'} or any(not isinstance(v,str) or len(v)>1000 for v in data.values()):raise ValueError('invalid_segment_transcription')
        return data
    async def close(self):await self.provider.close()
