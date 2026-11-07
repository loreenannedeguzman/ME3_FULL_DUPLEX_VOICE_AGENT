"""Runs only in optional pinned Piper environment. Text stdin; PCM stdout; no keys."""
import json,sys
from pathlib import Path

def main():
    line=sys.stdin.buffer.readline(8193)
    if len(line)>8192:raise ValueError('bounded_input_required')
    value=json.loads(line)
    if set(value)!={'text','style'} or not isinstance(value['text'],str) or not 1<=len(value['text'])<=700 or value['style'] not in {'normal','slow'}:raise ValueError('invalid_input')
    from piper import PiperVoice,SynthesisConfig
    voice=PiperVoice.load(Path(sys.argv[1]))
    config=SynthesisConfig(length_scale=1/.75 if value['style']=='slow' else 1.0)
    header_sent=False;expected=None
    for chunk in voice.synthesize(value['text'],syn_config=config):
        current=(chunk.sample_rate,chunk.sample_channels,chunk.sample_width)
        if expected is not None and current!=expected:raise ValueError('changed_audio_format')
        expected=current
        if not header_sent:
            sys.stdout.buffer.write(json.dumps({'sample_rate':chunk.sample_rate,'channels':chunk.sample_channels,'sample_width':chunk.sample_width}).encode()+b'\n');header_sent=True
        sys.stdout.buffer.write(chunk.audio_int16_bytes);sys.stdout.buffer.flush()
    if not header_sent:raise ValueError('no_audio')
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(2)
