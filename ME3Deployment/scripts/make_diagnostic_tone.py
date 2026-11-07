"""Generate a quiet 48kHz stereo diagnostic locally; never records speech."""
from pathlib import Path
import math, struct, wave
root=Path(__file__).resolve().parents[1]
path=root/".tmp"/"tone-48k-stereo.wav"
path.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(path),"wb") as out:
    out.setparams((2,2,48000,0,"NONE","not compressed"))
    out.writeframes(b"".join(struct.pack("<hh", sample, sample) for sample in (
        int(1200*math.sin(2*math.pi*440*i/48000)) for i in range(96000)
    )))
print("Generated diagnostic tone; no captured or prerecorded speech.")
