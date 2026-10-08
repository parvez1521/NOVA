"""Registry and lifecycle manager for first-class connectors."""

from __future__ import annotations

import asyncio

from app.connectors.base import Connector, PlannedConnector
from app.connectors.credentials import CredentialStore, MacOSKeychainStore
from app.connectors.mcp import MCPManager
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorError, ConnectorHealth, ConnectorStatus, ConnectorSnapshot


def planned_descriptors():
    return [
        {"name": "google", "icon": "G", "description": "Gmail, Calendar and Drive when authorized.", "auth": "oauth2", "permissions": ["gmail.read", "calendar.read", "drive.read"], "capabilities": ["search", "read", "draft", "create"]},
        {"name": "github", "icon": "GH", "description": "Repositories, files, issues, branches and pull requests.", "auth": "oauth2", "permissions": ["repo.read", "issues.read"], "capabilities": ["repositories", "files", "issues", "pull_requests"]},
        {"name": "notion", "icon": "N", "description": "Search, read and create notes in authorized workspaces.", "auth": "oauth2", "permissions": ["pages.read", "pages.write"], "capabilities": ["search", "read", "create", "append", "update"]},
        {"name": "telegram", "icon": "TG", "description": "Bot or authorized Telegram workflows with explicit send approval.", "auth": "bot_token", "permissions": ["messages.read"], "capabilities": ["read", "search", "draft", "send"]},
        {"name": "linkedin", "icon": "in", "description": "Profile and owned content where the official API permits it.", "auth": "oauth2", "permissions": ["profile.read", "content.read"], "capabilities": ["profile", "owned_content", "publish"]},
        {"name": "instagram", "icon": "IG", "description": "Official Meta/Instagram capabilities only; browser fallback may apply.", "auth": "oauth2", "permissions": ["media.read"], "capabilities": ["profile", "media", "publish"]},
        {"name": "youtube", "icon": "YT", "description": "Search and metadata now remain separate from publishing approval.", "auth": "oauth2", "permissions": ["youtube.read"], "capabilities": ["search", "metadata", "comments", "upload_preparation"]},
        {"name": "slack", "icon": "S", "description": "Workspace messages and channels through an authorized API.", "auth": "oauth2", "permissions": ["channels.read", "messages.read"], "capabilities": ["search", "read", "draft", "send"]},
        {"name": "discord", "icon": "D", "description": "Authorized Discord workspace access with send confirmation.", "auth": "bot_token", "permissions": ["guilds.read", "messages.read"], "capabilities": ["read", "search", "draft", "send"]},
        {"name": "mcp", "icon": "M", "description": "Explicitly registered MCP servers with per-tool permissions.", "auth": "mcp", "permissions": [], "capabilities": ["register", "discover", "inspect", "execute"]},
    ]


class ConnectorManager:
    def __init__(self, *, enabled: bool = False, mcp_enabled: bool = False, credentials: CredentialStore | None = None, connectors: list[Connector] | None = None) -> None:
        self.enabled = enabled
        self.mcp = MCPManager(enabled=mcp_enabled)
        self.credentials = credentials or MacOSKeychainStore()
        self.connectors: dict[str, Connector] = {}
        custom_names = {connector.name for connector in connectors or []}
        for descriptor in planned_descriptors():
            if descriptor["name"] not in custom_names:
                self.register(PlannedConnector(ConnectorDescriptor.model_validate(descriptor), self.credentials))
        for connector in connectors or []:
            self.register(connector)

    def register(self, connector: Connector) -> None:
        if connector.name in self.connectors:
            raise ValueError(f"Connector already registered: {connector.name}")
        self.connectors[connector.name] = connector

    def _get(self, name: str) -> Connector:
        connector = self.connectors.get(name)
        if connector is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND", "That connector is not registered.", status_code=404)
        return connector

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ConnectorError("CONNECTORS_DISABLED", "Connectors are disabled in NOVA settings.")

    async def list(self) -> list[ConnectorSnapshot]:
        if not self.enabled:
            return [self._disabled_snapshot(connector) for connector in self.connectors.values()]
        return list(await asyncio.gather(*(connector.snapshot() for connector in self.connectors.values())))

    async def get(self, name: str) -> ConnectorSnapshot:
        connector = self._get(name)
        return self._disabled_snapshot(connector) if not self.enabled else await connector.snapshot()

    async def connect(self, name: str, **options) -> ConnectorActionResult:
        self._require_enabled()
        return await self._get(name).connect(**options)

    async def disconnect(self, name: str) -> ConnectorActionResult:
        self._require_enabled()
        return await self._get(name).disconnect()

    async def refresh(self, name: str) -> ConnectorSnapshot:
        self._require_enabled()
        return await self._get(name).refresh()

    async def callback(self, name: str, **options) -> ConnectorActionResult:
        self._require_enabled()
        return await self._get(name).callback(**options)

    async def tools(self, name: str) -> list[dict[str, object]]:
        self._require_enabled()
        return await self._get(name).tools()

    async def execute(self, name: str, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        self._require_enabled()
        connector = self._get(name)
        execute = getattr(connector, "execute", None)
        if execute is None:
            raise ConnectorError("CONNECTOR_TOOL_UNAVAILABLE", "This connector has no executable tools yet.")
        return await execute(tool, arguments)

    async def status(self) -> dict[str, object]:
        snapshots = await self.list()
        return {"enabled": self.enabled, "connected": sum(item.connected for item in snapshots), "available": self.enabled, "connectors": [item.model_dump() for item in snapshots]}

    async def close(self) -> None:
        await self.mcp.close()

    @staticmethod
    def _disabled_snapshot(connector: Connector) -> ConnectorSnapshot:
        return ConnectorSnapshot(**connector.descriptor.model_dump(), status=ConnectorStatus.UNAVAILABLE, connected=False,
            detail="Connectors are disabled in NOVA settings.", health=ConnectorHealth(ok=False, detail="Enable connectors before connecting an account."))
