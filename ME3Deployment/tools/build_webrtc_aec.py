"""Build Pi-only speaker AEC shim locally; no installs, downloads or audio calls.

Usage: python tools/build_webrtc_aec.py --include-root PATH/usr/include
The matching WebRTC1.3 development headers can be installed or extracted into
the project. The existing Pi runtime is used; system routing is never modified.
"""
import argparse,ctypes,hashlib,json,os,platform,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--include-root',type=Path,default=Path('/usr/include'));a=p.parse_args()
    if sys.platform!='linux' or platform.machine()!='aarch64':raise ValueError('Pi_aarch64_Linux_required')
    source=ROOT/'me3_assistant/native/webrtc_shim.cpp';headers=a.include_root.resolve()
    assert (headers/'webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h').is_file()
    base=ROOT/'data/native/webrtc-aec';base.mkdir(parents=True,exist_ok=True)
    assert not base.is_symlink() and base.resolve().is_relative_to(ROOT.resolve())
    lib=base/'libme3_apm.so';receipt=base/'build.json'
    if lib.exists() or receipt.exists():raise ValueError('existing_build_must_be_preserved_before_rebuild')
    runtime=Path('/lib/aarch64-linux-gnu/libwebrtc-audio-processing-1.so.3');assert runtime.is_file()
    version=subprocess.check_output(['dpkg-query','-W','-f=${Version}','libwebrtc-audio-processing-1-3:arm64'],text=True).strip()
    if not version.startswith('1.3-'):raise ValueError('qualified_WebRTC1_3_runtime_required')
    command=['g++','-std=c++17','-shared','-fPIC','-O2','-I'+str(headers/'webrtc-audio-processing-1'),'-I'+str(headers),str(source),str(runtime)]
    with tempfile.TemporaryDirectory(prefix='build-',dir=base) as temporary:
        binary=Path(temporary)/'libme3_apm.so';done=subprocess.run(command+['-o',str(binary)],capture_output=True,text=True,timeout=60)
        result={'command':command+['-o','PROJECT_TEMP/libme3_apm.so'],'exit':done.returncode,'stdout':done.stdout,'stderr':done.stderr}
        if done.returncode:print(json.dumps(result));return 1
        native=ctypes.CDLL(str(binary));native.me3_apm_abi.restype=ctypes.c_int;native.me3_apm_profile.restype=ctypes.c_int
        assert native.me3_apm_abi()==1 and native.me3_apm_profile()==0x101
        binary.chmod(0o700);binary.replace(lib)
    result.update(issued_utc=datetime.now(timezone.utc).isoformat(),abi=1,profile='AEC3_lowNS_noAGC',source_cpp_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),library_sha256=hashlib.sha256(lib.read_bytes()).hexdigest(),runtime_version=version,runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],calls=0,system_changes=False)
    with receipt.open('x',encoding='utf-8') as out:json.dump(result,out,indent=2);out.write('\n')
    receipt.chmod(0o600);print(json.dumps({'built':True,'abi':1,'profile':result['profile'],'library_sha256':result['library_sha256'],'calls':0}));return 0
if __name__=='__main__':raise SystemExit(main())
