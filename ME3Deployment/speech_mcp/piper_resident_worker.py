"""Private framed IPC; one local model, serialized utterances. No credentials/log text."""
import json,sys,time,os
from pathlib import Path
MAX_NATIVE=48000*2*45

def send(header,pcm=b''):
    sys.stdout.buffer.write(json.dumps(header,separators=(',',':')).encode()+b'\n')
    if pcm:sys.stdout.buffer.write(pcm)
    sys.stdout.buffer.flush()

def main():
    # Fixed per-process speaker identity: never request/model-supplied data.
    speaker_id=None
    if len(sys.argv)==3:
        if sys.argv[2]!='1' or Path(sys.argv[1]).name!='es_ES-sharvard-medium.onnx':raise ValueError('invalid_fixed_speaker')
        metadata=json.loads(Path(str(sys.argv[1])+'.json').read_bytes())
        mapping=metadata.get('speaker_id_map')
        if type(metadata.get('num_speakers')) is not int or metadata['num_speakers']!=2 or not isinstance(mapping,dict) or set(mapping)!={'M','F'} or any(type(mapping[k]) is not int for k in mapping) or mapping!={'M':0,'F':1}:raise ValueError('invalid_fixed_speaker')
        speaker_id=1
    elif len(sys.argv)!=2:raise ValueError('invalid_worker_arguments')
    elif Path(sys.argv[1]).name=='es_ES-sharvard-medium.onnx':raise ValueError('fixed_female_speaker_required')
    began=time.perf_counter()
    from piper import PiperVoice,SynthesisConfig
    imported=time.perf_counter()-began;began=time.perf_counter()
    profile=os.getenv('ME3_PIPER_CPU_PROFILE')
    if profile is None:voice=PiperVoice.load(Path(sys.argv[1]))
    else:
        from piper_profile import load_voice
        voice=load_voice(Path(sys.argv[1]),profile)
    loaded=time.perf_counter()-began
    uses=0
    while line:=sys.stdin.buffer.readline(8193):
        if len(line)>8192:raise ValueError('request_size')
        req=json.loads(line)
        if set(req)!={'text','style','token','generation','epoch'}:raise ValueError('request_schema')
        if not isinstance(req['text'],str) or not 1<=len(req['text'])<=700 or req['style'] not in {'normal','slow'}:raise ValueError('request_text')
        if not isinstance(req['token'],str) or len(req['token'])!=32 or any(c not in '0123456789abcdef' for c in req['token']):raise ValueError('request_identity')
        if type(req['generation']) is not int or not 0<=req['generation']<=10000 or type(req['epoch']) is not int or req['epoch']<1:raise ValueError('request_identity')
        identity={k:req[k] for k in ('token','generation','epoch')}
        first=True;size=0;chunks=0;fmt=None
        config=SynthesisConfig(length_scale=1/.75 if req['style']=='slow' else 1.0,**(dict(speaker_id=speaker_id) if speaker_id is not None else {}))
        for chunk in voice.synthesize(req['text'],syn_config=config):
            header={'sample_rate':chunk.sample_rate,'channels':chunk.sample_channels,'sample_width':chunk.sample_width}
            if first:
                fmt=header;send({'kind':'start',**identity,**header,'import_seconds':imported,'load_seconds':loaded,'reuse_index':uses});first=False
            elif header!=fmt:raise ValueError('format_changed')
            raw=chunk.audio_int16_bytes;size+=len(raw)
            if size>MAX_NATIVE:raise ValueError('audio_limit')
            for offset in range(0,len(raw),65536):
                part=raw[offset:offset+65536];send({'kind':'pcm',**identity,'byte_count':len(part)},part);chunks+=1
        if first:raise ValueError('empty_audio')
        send({'kind':'end',**identity,'byte_count':size,'chunks':chunks});uses+=1

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(2)
