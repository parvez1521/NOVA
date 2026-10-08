"""Bounded standard-library DSP; no cloud noise reduction or audio persistence."""
import io
import math
import struct
import wave


def condition_audio(audio: bytes) -> tuple[bytes,dict]:
    with wave.open(io.BytesIO(audio),"rb") as source:
        samples=[sample[0]/32768 for sample in struct.iter_unpack("<h",source.readframes(source.getnframes()))]
    peak=max((abs(sample) for sample in samples),default=0)
    rms=math.sqrt(sum(sample*sample for sample in samples)/max(1,len(samples)))
    clipped=sum(abs(sample)>=.999 for sample in samples)/max(1,len(samples))
    # Remove DC/low-frequency rumble with a gentle 70-Hz high-pass; bounded gain
    # avoids amplifying a quiet background into an apparent command.
    alpha=math.exp(-2*math.pi*70/16000);previous_input=previous_output=0.;filtered=[]
    for sample in samples:
        output=alpha*(previous_output+sample-previous_input);filtered.append(output)
        previous_input,previous_output=sample,output
    gain=min(4.,.85/max(max((abs(sample) for sample in filtered),default=0),.01)) if rms>.002 else 1.
    pcm=b"".join(struct.pack("<h",round(max(-1,min(.9999,sample*gain))*32767)) for sample in filtered)
    output=io.BytesIO()
    with wave.open(output,"wb") as target:target.setparams((1,2,16000,0,"NONE","not compressed"));target.writeframes(pcm)
    return output.getvalue(),{"rms":round(rms,4),"peak":round(peak,4),"clipping_fraction":round(clipped,4),"gain":round(gain,2)}
