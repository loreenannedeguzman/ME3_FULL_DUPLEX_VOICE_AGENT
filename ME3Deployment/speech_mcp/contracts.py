"""Deterministic validation at both sides of the speech MCP boundary."""
import base64, binascii, io, re, wave

MAX_UTTERANCE_SECONDS = 15
MAX_TEXT_CHARS = 700
MAX_TTS_SECONDS = 45

def identity(generation, request_id):
    if type(generation) is not int or not 0 <= generation <= 10000:
        raise ValueError("invalid_generation")
    if not isinstance(request_id,str) or not re.fullmatch(r"[a-z0-9-]{1,80}",request_id):
        raise ValueError("invalid_request_id")

def decode_pcm(value, rate, seconds):
    if rate not in (16000,24000) or not isinstance(value,str):
        raise ValueError("invalid_audio_format")
    maximum=rate*2*seconds
    if not value or len(value)>4*((maximum+2)//3):
        raise ValueError("audio_size_limit")
    try: data=base64.b64decode(value,validate=True)
    except (binascii.Error,ValueError): raise ValueError("invalid_base64") from None
    if not data or len(data)%2 or len(data)>maximum:
        raise ValueError("invalid_pcm_size")
    return data

def validate_text(text):
    if not isinstance(text,str) or not text.strip() or len(text)>MAX_TEXT_CHARS or "\x00" in text:
        raise ValueError("invalid_text")
    return text.strip()

def pcm_to_wav(pcm, rate=16000):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate);out.writeframes(pcm)
    return buffer.getvalue()

def wav_to_pcm(data):
    if len(data)>24000*2*MAX_TTS_SECONDS+65536:
        raise ValueError("tts_audio_size_limit")
    try:
        with wave.open(io.BytesIO(data),'rb') as src:
            rate=src.getframerate()
            if src.getnchannels()!=1 or src.getsampwidth()!=2 or rate!=24000 or src.getcomptype()!='NONE':
                raise ValueError("unsupported_tts_wav")
            if not 0<src.getnframes()<=rate*MAX_TTS_SECONDS:
                raise ValueError("tts_duration_limit")
            pcm=src.readframes(src.getnframes())
            if len(pcm)!=src.getnframes()*2: raise ValueError("truncated_tts_wav")
            return pcm
    except (wave.Error,EOFError): raise ValueError("invalid_tts_wav") from None

class GenerationClock:
    def __init__(self): self.current=0
    def advance(self): self.current+=1;return self.current
    def accepts(self,generation): return type(generation) is int and generation==self.current


def normalize_tts_audio(data,mime):
    if not isinstance(data,bytes) or not data:raise ValueError('tts_no_audio')
    if 'wav' in mime.lower() or data.startswith(b'RIFF'):
        return wav_to_pcm(data)
    if not mime.lower().startswith('audio/l16'):raise ValueError('unsupported_tts_audio')
    rate=re.search(r'rate=(\d+)',mime)
    if rate and rate.group(1)!='24000':raise ValueError('unsupported_tts_rate')
    if len(data)%2 or len(data)>24000*2*MAX_TTS_SECONDS:raise ValueError('invalid_tts_pcm')
    return data


def human_turn_limit(value):
    """A lower per-trial cap may be selected; never exceed the existing eight."""
    if not isinstance(value,str) or not re.fullmatch(r'[1-8]',value):
        raise ValueError('invalid_human_turn_limit')
    return int(value)
