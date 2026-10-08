from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from fastapi.responses import Response

from app.computer.models import ComputerSettings
from app.computer.runtime import ComputerRuntime

class TaskApproval(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    confirmed: bool


def computer_routes(runtime: ComputerRuntime) -> APIRouter:
    router=APIRouter(prefix="/api/computer",tags=["computer-use"])

    @router.get("")
    def status(): return runtime.settings.model_dump()

    @router.put("")
    async def update(settings: ComputerSettings): return await runtime.update_settings(settings)

    @router.get("/permissions")
    async def permissions(): return await runtime.permissions()

    @router.post("/permissions/{kind}/request")
    async def request_permission(kind: str):
        if kind not in {"accessibility","screen_recording"}: raise HTTPException(422,"Unsupported permission")
        if runtime.settings.simulation: return {"requested":False,"simulation":True}
        try: return await runtime.executor.native.call("permissions.request",kind=kind)
        except AttributeError:
            return {"requested":False,"message":"Restart NOVA after enabling this permission."}

    @router.get("/tools")
    def tools(): return runtime.executor.registry.catalog()

    @router.get("/tasks")
    def tasks(limit: int=Query(50,ge=1,le=200)): return runtime.history.list(limit)

    @router.get("/tasks/{identifier}")
    def task(identifier: UUID):
        managed=runtime.manager.get(str(identifier))
        return managed.task.model_dump() if managed else runtime.history.get(str(identifier))

    @router.post("/tasks/{identifier}/confirm")
    async def confirm(identifier: UUID, body: TaskApproval):
        return (await runtime.confirm(str(identifier),body.confirmed)).model_dump()

    @router.post("/tasks/{identifier}/pause")
    async def pause(identifier: UUID): return (await runtime.pause(str(identifier))).model_dump()

    @router.post("/tasks/{identifier}/resume")
    async def resume(identifier: UUID): return (await runtime.resume(str(identifier))).model_dump()

    @router.post("/tasks/{identifier}/stop")
    async def stop(identifier: UUID): return (await runtime.stop(str(identifier))).model_dump()

    @router.get("/tasks/{identifier}/preview")
    async def preview(identifier: UUID):
        image=await runtime.preview(str(identifier))
        return Response(image,media_type="image/png",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

    return router
