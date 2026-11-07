"""Explicit bounded inference for the opt-in normal duplex Piper worker."""
import json
from pathlib import Path
PROFILE='duplex_2t_no_spin'

def session_options(runtime):
    options=runtime.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    options.execution_mode=runtime.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.intra_op.allow_spinning','0')
    options.add_session_config_entry('session.inter_op.allow_spinning','0')
    return options

def load_voice(model_path,profile):
    if profile!=PROFILE:raise ValueError('invalid_piper_cpu_profile')
    import onnxruntime
    from piper import PiperVoice
    from piper.config import PiperConfig
    path=Path(model_path)
    with Path(str(path)+'.json').open(encoding='utf-8') as f:config=PiperConfig.from_dict(json.load(f))
    options=session_options(onnxruntime)
    session=onnxruntime.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
    actual=session.get_session_options()
    if (actual.intra_op_num_threads!=2 or actual.inter_op_num_threads!=1
        or actual.execution_mode!=onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        or any(actual.get_session_config_entry(k)!='0' for k in ['session.intra_op.allow_spinning','session.inter_op.allow_spinning'])
        or session.get_providers()!=['CPUExecutionProvider']):
        raise ValueError('piper_cpu_profile_mismatch')
    # Same pinned package/model/config/default phonemizer resources as load();
    # only the session threading/spinning configuration changes. No downloads.
    return PiperVoice(config=config,session=session)

