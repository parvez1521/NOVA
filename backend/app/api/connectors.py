"""Safe connector-management routes.

These routes expose metadata and lifecycle controls only. Connector secrets
stay in the backend credential boundary and are never accepted from the UI.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.manager import ConnectorManager
from app.connectors.models import ConnectorError


class ConnectorConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redirect_uri: str | None = Field(default=None, max_length=500)


def connector_routes(manager: ConnectorManager) -> APIRouter:
    router = APIRouter(prefix="/api/connectors", tags=["connectors"])

    def failure(error: ConnectorError) -> HTTPException:
        return HTTPException(error.status_code, detail=error.message, headers={"X-NOVA-Error-Code": error.code})

    @router.get("")
    async def connectors():
        return await manager.status()

    @router.get("/{name}")
    async def connector(name: str):
        try:
            return (await manager.get(name)).model_dump()
        except ConnectorError as error:
            raise failure(error) from error

    @router.get("/{name}/tools")
    async def tools(name: str):
        try:
            return await manager.tools(name)
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/{name}/connect")
    async def connect(name: str, options: ConnectorConnectRequest | None = None):
        try:
            return (await manager.connect(name, **(options.model_dump(exclude_none=True) if options else {}))).model_dump()
        except ConnectorError as error:
            raise failure(error) from error

    @router.post("/{name}/disconnect")
    async def disconnect(name: str):
        try:
            return (await manager.disconnect(name)).model_dump()
        except ConnectorError as error:
            raise failure(error) from error

    @router.get("/{name}/callback")
    async def callback(name: str, code: str | None = None, state: str | None = None, error: str | None = None):
        try:
            return (await manager.callback(name, code=code, state=state, error=error)).model_dump()
        except ConnectorError as connector_error:
            raise failure(connector_error) from connector_error

    @router.post("/{name}/refresh")
    async def refresh(name: str):
        try:
            return (await manager.refresh(name)).model_dump()
        except ConnectorError as error:
            raise failure(error) from error

    return router
