"""Optional installed local image model; no downloads, cloud calls or persistent images."""

import base64
import json
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.computer.models import ComputerError, UIElement
from app.database.privacy import safe_text


def is_loopback_url(value: str) -> bool:
    parsed=urlparse(value)
    if parsed.scheme not in {"http","https"} or parsed.username or parsed.password:return False
    if parsed.hostname=="localhost":return True
    try:return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:return False


class LocalVisionProvider:
    def __init__(self, base_url: str):self.base_url=base_url.rstrip("/")
    async def inspect(self,path:Path,model:str,width:float,height:float) -> dict:
        if not is_loopback_url(self.base_url):raise ComputerError("VISION_REMOTE_BLOCKED","Screenshot images may only be processed by a loopback-local vision provider.")
        async with httpx.AsyncClient(timeout=45) as client:
            info=await client.post(self.base_url+"/api/show",json={"model":model});info.raise_for_status()
            if "vision" not in info.json().get("capabilities",[]):raise ComputerError("VISION_MODEL_UNAVAILABLE","Select an already-installed vision-capable Ollama model. No model is downloaded automatically.")
            response=await client.post(self.base_url+"/api/chat",json={"model":model,"stream":False,"format":"json","messages":[{"role":"user","content":f"Describe visible public UI only. Do not transcribe passwords, codes, private tokens, payment information. Screen bounds {width}x{height}. Return JSON {{visible_text:string,elements:[{{id:string,role:string,label:string,x:number,y:number,width:number,height:number,confidence:number}}]}}. Coordinates must be grounded in visible elements. No actions or reasoning.","images":[base64.b64encode(path.read_bytes()).decode()]}],"options":{"num_predict":800,"temperature":0}})
            response.raise_for_status();result=json.loads(response.json()["message"]["content"])
            elements=[]
            for index,item in enumerate(result.get("elements",[])[:40]):
                item["id"]=f"vision-{index}";item["source"]="local_vision";item["confidence"]=min(0.79,float(item.get("confidence",0.65)))
                item["label"]=safe_text(item.get("label",""))
                elements.append(UIElement.model_validate(item).model_dump())
            return {"elements":elements,"visible_text":safe_text(result.get("visible_text","")),"source":"local_vision"}
