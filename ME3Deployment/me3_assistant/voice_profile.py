"""Explicit current normal-voice profile; no runtime model fallback or downloads."""
import hashlib
from pathlib import Path

# Explicit normal-profile migration after repeated final-answer failures.
# No runtime fallback; the historical120b receipts remain unchanged.
DECISION_MODEL='qwen/qwen3.8-27b'
DECISION_REASONING_EFFORT='none'
PIPER_VOICE='en_US-ljspeech-medium'
PIPER_ASSETS={
 'en_US-ljspeech-medium.onnx':'6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22',
 'en_US-ljspeech-medium.onnx.json':'141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860',
 'es_ES-sharvard-medium.onnx':'40febfb1679c69a4505ff311dc136e121e3419a13a290ef264fdf43ddedd0fb1',
 'es_ES-sharvard-medium.onnx.json':'7438c9b699c72b0c3388dae1b68d3f364dc66a2150fe554a1c11f03372957b2c',
}

def normal_piper_environment(root):
    root=Path(root).resolve()
    for name,digest in PIPER_ASSETS.items():
        path=root/'models/piper'/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('normal_piper_asset_mismatch')
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('normal_piper_asset_mismatch')
    return dict(ME3_PIPER_CPU_PROFILE='duplex_2t_no_spin',TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(root/'.venv-piper/bin/python'),PIPER_MODEL=str(root/'models/piper'/(PIPER_VOICE+'.onnx')),PIPER_SPANISH_MODEL=str(root/'models/piper/es_ES-sharvard-medium.onnx'))
