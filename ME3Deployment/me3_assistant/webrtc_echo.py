"""Pi speaker AEC: verified local WebRTC APM1.3 artifact, fixed low NS, no AGC.

Same320sample16kmono interface and20ms uniform capture buffer as the prototype.
No VAD gate or transcript matching; actual final-output reference comes from
EchoControl. No model calls or audio-device ownership here. Native failure is
fatal to the session; earphone mode never imports/loads this library.
"""
import ctypes,hashlib,json,os,platform,stat,sys,threading
from pathlib import Path
from task_mcp.client import ROOT

def verified_library(root=ROOT):
    root=Path(root).resolve();base=root/'data/native/webrtc-aec'
    lib=base/'libme3_apm.so';receipt=base/'build.json'
    for path in (base,lib,receipt):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('webrtc_artifact_path_invalid')
    for path in (lib,receipt):
        if not path.is_file():raise ValueError('webrtc_project_build_required')
        info=path.stat()
        if os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o022):
            raise ValueError('webrtc_artifact_permissions_invalid')
    data=json.loads(receipt.read_text(encoding='utf-8'))
    cpp=root/'me3_assistant/native/webrtc_shim.cpp'
    if data.get('abi')!=1 or data.get('profile')!='AEC3_lowNS_noAGC' or data.get('source_cpp_sha256')!=hashlib.sha256(cpp.read_bytes()).hexdigest() or data.get('library_sha256')!=hashlib.sha256(lib.read_bytes()).hexdigest():
        raise ValueError('webrtc_artifact_hash_mismatch')
    return lib

class WebRtcEcho:
    metadata={'engine':'webrtc_apm_1.3','profile':'AEC3_lowNS_noAGC',
        'causal_reference_margin_ms':20,'native_frame_ms':10,
        'algorithmic_input_delay_ms':None,'measured_synthetic_delay_ms':33.94,
        'delay_scope':'Synthetic saved-speech correlation peak; frequency-dependent filtering, not physical acoustic delay'}
    def __init__(self):
        self.state=None;self.previous=bytes(640);self.lock=threading.Lock()
        if sys.platform!='linux' or platform.machine()!='aarch64' or sys.byteorder!='little':
            raise ValueError('webrtc_requires_Pi_aarch64_Linux')
        self.lib=ctypes.CDLL(str(verified_library()))
        P=ctypes.POINTER(ctypes.c_int16)
        for name in ('me3_apm_abi','me3_apm_profile'):
            function=getattr(self.lib,name);function.argtypes=[];function.restype=ctypes.c_int
        if self.lib.me3_apm_abi()!=1 or self.lib.me3_apm_profile()!=0x101:
            raise ValueError('webrtc_native_abi_mismatch')
        self.lib.me3_apm_new.argtypes=[];self.lib.me3_apm_new.restype=ctypes.c_void_p
        self.lib.me3_apm_process.argtypes=[ctypes.c_void_p,P,P,P,ctypes.c_int];self.lib.me3_apm_process.restype=ctypes.c_int
        self.lib.me3_apm_free.argtypes=[ctypes.c_void_p];self.lib.me3_apm_free.restype=None
        self.state=self.lib.me3_apm_new()
        if not self.state:raise ValueError('webrtc_initialization_failed')
        self.vector=ctypes.c_int16*320
    def process(self,microphone,reference):
        with self.lock:
            if not self.state:raise ValueError('speaker_aec_closed')
            if len(microphone)!=640 or len(reference)!=640:raise ValueError('speaker_aec_frame_invalid')
            out=self.vector()
            code=self.lib.me3_apm_process(self.state,self.vector.from_buffer_copy(self.previous),self.vector.from_buffer_copy(reference),out,320)
            if code:raise ValueError('webrtc_processing_failed')
            self.previous=bytes(microphone)
            return bytes(out)
    def close(self):
        with self.lock:
            if self.state:self.lib.me3_apm_free(self.state);self.state=None
            self.previous=bytes(640)
