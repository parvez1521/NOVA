from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.mcp import MCPManager
from app.connectors.models import ConnectorError


class MCPServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=40)
    command: list[str] = Field(min_length=1, max_length=16)
    cwd: str | None = Field(default=None, max_length=500)
    confirmed: bool = False


class MCPConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False


class MCPExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict = Field(default_factory=dict)
    confirmed: bool = False


def mcp_routes(manager: MCPManager) -> APIRouter:
    router = APIRouter(prefix="/api/mcp", tags=["mcp"])

    def failure(error: ConnectorError) -> Exception:
        from fastapi import HTTPException
        return HTTPException(error.status_code, detail=error.message, headers={"X-NOVA-Error-Code": error.code})

    @router.get("")
    def status():
        return manager.list()

    @router.post("/servers")
    def register(options: MCPServerRequest):
        try:
            return manager.register(options.name, options.command, cwd=options.cwd, confirmed=options.confirmed)
        except ConnectorError as error:
            raise failure(error) from error

    @router.get("/servers/{name}")
    def inspect(name: str):
        try:
            return manager.inspect(name)
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/servers/{name}/enable")
    async def enable(name: str, options: MCPConfirmRequest):
        try:
            return await manager.enable(name, confirmed=options.confirmed)
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/servers/{name}/disable")
    async def disable(name: str):
        try:
            return await manager.disable(name)
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/servers/{name}/discover")
    async def discover(name: str):
        try:
            return await manager.discover(name)
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/servers/{name}/execute/{tool_name}")
    async def execute(name: str, tool_name: str, options: MCPExecuteRequest):
        try:
            return await manager.execute(name, tool_name, options.arguments, confirmed=options.confirmed)
        except ConnectorError as error:
            raise failure(error) from error

    return router
