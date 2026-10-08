"""Benchmark only installed local models on explicit, nonprivate audio fixtures."""
import argparse
import asyncio
import json
import re
import resource
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"backend"))


def word_error_proxy(expected,actual):
    a=re.findall(r"\w+",expected.casefold());b=re.findall(r"\w+",actual.casefold());row=list(range(len(b)+1))
    for i,word in enumerate(a,1):
        next_row=[i]
        for j,other in enumerate(b,1):next_row.append(min(next_row[-1]+1,row[j]+1,row[j-1]+(word!=other)))
        row=next_row
    return round(row[-1]/max(1,len(a)),3)


async def main(options):
    from app.voice.stt.whisper import LocalWhisperProvider
    rows=[]
    for model in options.models:
        provider=LocalWhisperProvider(ROOT/"backend/data/models"/f"ggml-{model}.bin",context="Chrome, YouTube, NOVA.",preprocess=True)
        if not await provider.is_available():rows.append({"model":model,"status":"not_installed"});continue
        result=await provider.transcribe(Path(options.audio).read_bytes())
        rows.append({"model":model,"latency_ms":result.latency_ms,"duration_ms":result.duration_ms,"word_error_proxy":word_error_proxy(options.reference,result.text),"peak_child_rss_bytes":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,"language":result.language})
    print(json.dumps({"fixture_results":rows,"models_downloaded":False,"raw_audio_saved":False},indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--audio",required=True);parser.add_argument("--reference",required=True);parser.add_argument("--models",nargs="+",default=["tiny","base","small","medium"],choices=["tiny","base","small","medium"])
    asyncio.run(main(parser.parse_args()))
