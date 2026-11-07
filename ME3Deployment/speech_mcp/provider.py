"""Existing Google account only; bounded asynchronous, nonstored requests."""
import asyncio, base64, os
from google import genai
from google.genai import types
from speech_mcp.contracts import pcm_to_wav, wav_to_pcm, validate_text, normalize_tts_audio

ASR_MODEL='gemini-3.5-flash-lite'
TTS_MODEL='gemini-3.8-flash-lite-tts'
TEXT_MODEL='gemini-3.5-flash-lite'

class GoogleSpeech:
    def __init__(self):
        if os.getenv('ALLOW_PROVIDER_AUDIO')!='1' or not os.getenv('GOOGLE_API_KEY'):
            raise ValueError('provider_configuration_required')
        self.client=genai.Client(api_key=os.environ['GOOGLE_API_KEY'],http_options=types.HttpOptions(timeout=30000,retry_options=types.HttpRetryOptions(attempts=1)))
    async def transcribe(self,pcm):
        result=await self.client.aio.models.generate_content(
            model=os.getenv('ASR_MODEL',ASR_MODEL),
            contents=[types.Part.from_text(text='Transcribe only the spoken words exactly in their original language. No commentary, no answers, no invented speech. Return empty text if no intelligible speech.'),types.Part.from_bytes(data=pcm_to_wav(pcm),mime_type='audio/wav')],
            config=types.GenerateContentConfig(max_output_tokens=1024,thinking_config=types.ThinkingConfig(thinking_level='LOW')))
        return (result.text or '').strip()[:1000]
    async def synthesize(self,text,style):
        result=await self.client.aio.models.generate_content(
            model=os.getenv('TTS_MODEL',TTS_MODEL),
            contents=types.Content(role='user',parts=[types.Part(text=validate_text(text),speech_metadata=types.SpeechMetadata(style='Speak slowly and clearly, pausing between numbers.' if style=='slow' else 'Natural, concise and friendly.'))]),
            config=types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv('M3_VOICE','Kore'))))))
        parts=[part for candidate in (result.candidates or []) if candidate.content for part in (candidate.content.parts or []) if part.inline_data and part.inline_data.data]
        if len(parts)!=1:raise ValueError('tts_expected_one_audio_part')
        self.source_mime=parts[0].inline_data.mime_type or ''
        return normalize_tts_audio(parts[0].inline_data.data,self.source_mime)
    async def answer(self,history):
        contents=[types.Content(role='user' if item['type']=='user_input' else 'model',parts=[types.Part.from_text(text=part['text']) for part in item['content']]) for item in history]
        result=await self.client.aio.models.generate_content(
            model=os.getenv('TEXT_MODEL',TEXT_MODEL),contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=1024,thinking_config=types.ThinkingConfig(thinking_level='LOW'),system_instruction='You are a concise friendly spoken assistant. Normally answer in one short sentence. Remember conversational facts. Answer only the most recent user request. Earlier tasks may already have been interrupted or failed; unspoken control entries mark that state and are not words to say aloud. Never resume or repeat a canceled or failed task unless the latest user explicitly asks. Do not combine an earlier counting request with a new question. If asked to count slowly from one to thirty, output every number in words separated by commas. When interrupted, abandon the old answer and answer the latest user. You have no real-world task tools; never claim actions. Plain spoken text only.'))
        return validate_text((result.text or '')[:700])
    async def close(self):
        await self.client.aio.aclose()
        self.client.close()
