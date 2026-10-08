import base64
import binascii
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.voice.base import VoiceError
from app.voice.wake import WakeProviderRegistry, WhisperKeywordProvider


class WakeAudio(BaseModel):
    audio: str = Field(max_length=180000)


def wake_routes(registry: WakeProviderRegistry):
    router=APIRouter(prefix="/api/voice/wake",tags=["wake-word"])
    @router.get("/providers")
    async def providers():return await registry.discover()
    @router.post("/{name}/start")
    async def start(name: str):
        provider=registry.providers.get(name)
        if not provider:raise HTTPException(404,"Unknown wake provider")
        try:await provider.start()
        except VoiceError as error:raise HTTPException(409,error.message) from error
        return {"enabled":True}
    @router.post("/{name}/stop")
    async def stop(name: str):
        provider=registry.providers.get(name)
        if provider:await provider.stop()
        return {"enabled":False}
    @router.post("/{name}/inspect")
    async def inspect(name: str,body: WakeAudio):
        provider=registry.providers.get(name)
        if not isinstance(provider,WhisperKeywordProvider):raise HTTPException(404,"Unknown local keyword provider")
        try:return await provider.inspect(base64.b64decode(body.audio,validate=True))
        except (ValueError,binascii.Error):raise HTTPException(422,"Invalid bounded PCM audio")
        except VoiceError as error:raise HTTPException(409,error.message) from error
    return router
