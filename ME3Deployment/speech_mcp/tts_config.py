"""Explicit TTS selection/readiness. No network, synthesis or secret rendering."""
from dataclasses import dataclass,field,replace
from pathlib import Path
import os,re,shutil,hashlib,json
from speech_mcp.provider import TTS_MODEL

@dataclass(frozen=True)
class TTSConfig:
    provider:str='google'
    model:str=TTS_MODEL
    voice:str='Kore'
    timeout:float=30
    ffmpeg:str=''
    api_key:str=field(default='',repr=False)
    allowed:bool=False
    credits_confirmed:bool=False
    budget_id:str=''
    request_budget:int=0
    character_budget:int=0
    piper_python:str=''
    piper_model:str=''
    piper_worker_mode:str='per_request'
    piper_spanish_model:str=''
    piper_speaker_id:int|None=None

    @classmethod
    def from_env(cls,env=None):
        env=os.environ if env is None else env
        provider=env.get('TTS_PROVIDER','google')
        if provider not in {'google','elevenlabs','flite','piper'}:raise ValueError('invalid_tts_provider')
        try:timeout=float(env.get('TTS_TIMEOUT_SECONDS','30'))
        except (ValueError,TypeError):raise ValueError('invalid_tts_timeout') from None
        if not 1<=timeout<=30:raise ValueError('invalid_tts_timeout')
        if provider=='google':
            model=env.get('TTS_MODEL',TTS_MODEL)
            if not re.fullmatch(r'gemini-[a-z0-9.-]{1,100}',model):raise ValueError('invalid_google_tts_model')
            return cls(provider,model,env.get('M3_VOICE','Kore'),timeout)
        if provider=='flite':
            voice=env.get('FLITE_VOICE','kal')
            if voice not in {'kal','kal16','awb','rms','slt'}:raise ValueError('invalid_flite_voice')
            binary=env.get('TTS_FFMPEG_PATH') or shutil.which('ffmpeg') or ''
            return cls(provider,'flite:'+voice,voice,timeout,binary)
        if provider=='piper':
            mode=env.get('PIPER_WORKER_MODE','per_request')
            if mode not in {'per_request','resident'}:raise ValueError('invalid_piper_worker_mode')
            selected=Path(env.get('PIPER_MODEL','')).stem if env.get('PIPER_MODEL') else 'en_US-ljspeech-high'
            if selected not in {'en_US-ljspeech-high','en_US-ljspeech-medium'}:raise ValueError('piper_model_required')
            return cls(provider,'piper:'+selected,selected,timeout,piper_python=env.get('PIPER_PYTHON',''),piper_model=env.get('PIPER_MODEL',''),piper_worker_mode=mode,piper_spanish_model=env.get('PIPER_SPANISH_MODEL',''))
        voice=env.get('ELEVENLABS_VOICE_ID','');model=env.get('ELEVENLABS_MODEL','eleven_flash_v2_5')
        if voice and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',voice):raise ValueError('invalid_elevenlabs_voice')
        if not re.fullmatch(r'eleven_[a-z0-9_]{1,73}',model):raise ValueError('invalid_elevenlabs_model')
        budget_id=env.get('ELEVENLABS_BUDGET_ID','')
        if budget_id and not re.fullmatch(r'[a-z0-9-]{1,48}',budget_id):raise ValueError('invalid_tts_budget')
        try:requests=int(env.get('ELEVENLABS_REQUEST_BUDGET','0'));characters=int(env.get('ELEVENLABS_CHARACTER_BUDGET','0'))
        except (ValueError,TypeError):raise ValueError('invalid_tts_budget') from None
        if not 0<=requests<=50 or not 0<=characters<=35000:raise ValueError('invalid_tts_budget')
        return cls(provider,model,voice,timeout,api_key=env.get('ELEVENLABS_API_KEY') or env.get('ELEVEN_API_KEY') or '',allowed=env.get('ALLOW_ELEVENLABS_TTS')=='1',credits_confirmed=env.get('ELEVENLABS_CREDITS_CONFIRMED')=='1',budget_id=budget_id,request_budget=requests,character_budget=characters)

    def require_ready(self):
        if self.provider=='flite' and (not self.ffmpeg or not Path(self.ffmpeg).is_file()):raise ValueError('local_tts_engine_required')
        if self.piper_speaker_id is not None and (type(self.piper_speaker_id) is not int or self.piper_speaker_id!=1 or self.provider!='piper' or Path(self.piper_model).name!='es_ES-sharvard-medium.onnx' or self.piper_worker_mode!='resident'):raise ValueError('fixed_spanish_female_speaker_required')
        if self.provider=='piper':
            if self.piper_worker_mode not in {'per_request','resident'}:raise ValueError('invalid_piper_worker_mode')
            if not self.piper_python or not Path(self.piper_python).is_absolute() or not Path(self.piper_python).is_file():raise ValueError('piper_python_required')
            model=Path(self.piper_model)
            if not self.piper_model or not model.is_absolute() or model.name not in {'en_US-ljspeech-high.onnx','en_US-ljspeech-medium.onnx','es_ES-sharvard-medium.onnx'} or not model.is_file() or not Path(str(model)+'.json').is_file():raise ValueError('piper_model_required')
        if self.provider=='piper' and Path(self.piper_model).name=='es_ES-sharvard-medium.onnx':
            if self.piper_worker_mode!='resident' or type(self.piper_speaker_id) is not int or self.piper_speaker_id!=1:raise ValueError('spanish_female_resident_required')
            # Hash once at adapter construction, not per chunk. Both artifacts are
            # pinned from the reviewed official model repository; no arbitrary voice.
            model=Path(self.piper_model);metadata=Path(str(model)+'.json')
            if model.stat().st_size!=76733615 or metadata.stat().st_size!=4903:raise ValueError('spanish_piper_asset_size_mismatch')
            if hashlib.sha256(model.read_bytes()).hexdigest()!='40febfb1679c69a4505ff311dc136e121e3419a13a290ef264fdf43ddedd0fb1':raise ValueError('spanish_piper_model_hash_mismatch')
            if hashlib.sha256(metadata.read_bytes()).hexdigest()!='7438c9b699c72b0c3388dae1b68d3f364dc66a2150fe554a1c11f03372957b2c':raise ValueError('spanish_piper_config_hash_mismatch')
            data=json.loads(metadata.read_bytes())
            if data['espeak']['voice']!='es' or data['audio']['sample_rate']!=22050 or type(data.get('num_speakers')) is not int or data['num_speakers']!=2 or data.get('speaker_id_map')!={'M':0,'F':1}:raise ValueError('spanish_piper_config_mismatch')
        if self.provider=='elevenlabs':
            if not self.api_key:raise ValueError('elevenlabs_key_required')
            if not self.voice:raise ValueError('elevenlabs_voice_required')
            if not self.allowed or not self.credits_confirmed:raise ValueError('elevenlabs_access_confirmation_required')
            if not self.budget_id or not self.request_budget or not self.character_budget:raise ValueError('elevenlabs_budget_required')

    def speech_languages(self):
        return ('en','es') if self.provider=='piper' and self.piper_spanish_model else ('en',)

    def for_language(self,language):
        if language not in self.speech_languages():raise ValueError('unsupported_speech_language')
        if language=='en':return self
        if self.piper_worker_mode!='resident':raise ValueError('spanish_female_resident_required')
        model=Path(self.piper_spanish_model)
        if not model.is_absolute() or model.name!='es_ES-sharvard-medium.onnx':raise ValueError('spanish_piper_model_required')
        return replace(self,model='piper:es_ES-sharvard-medium',voice='es_ES-sharvard-medium',piper_model=str(model),piper_spanish_model='',piper_speaker_id=1)

    def require_languages_ready(self):
        self.require_ready()
        if self.piper_spanish_model:
            self.for_language('es').require_ready()

    def public_info(self):
        return {'tts_provider':self.provider,'model':self.model,'timeout_seconds':self.timeout}
