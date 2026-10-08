"""Provider-independent connector contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.connectors.credentials import CredentialStore
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorError, ConnectorHealth, ConnectorSnapshot, ConnectorStatus


class Connector(ABC):
    descriptor: ConnectorDescriptor

    def __init__(self, credentials: CredentialStore) -> None:
        self.credentials = credentials

    @property
    def name(self) -> str:
        return self.descriptor.name

    @abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """Check local readiness without performing an account action."""

    @abstractmethod
    async def connect(self, **options) -> ConnectorActionResult:
        """Begin a provider-specific authorization flow."""

    async def disconnect(self) -> ConnectorActionResult:
        self.credentials.delete(self.name)
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.NEEDS_SETUP, detail="The connector is disconnected.")

    async def refresh(self) -> ConnectorSnapshot:
        return await self.snapshot()

    async def callback(self, *, code: str | None = None, state: str | None = None, error: str | None = None) -> ConnectorActionResult:
        raise ConnectorError("CONNECTOR_OAUTH_UNAVAILABLE", "This connector has no OAuth callback flow.", status_code=501)

    async def tools(self) -> list[dict[str, object]]:
        return []

    async def snapshot(self) -> ConnectorSnapshot:
        connected = self.credentials.has(self.name)
        health = await self.health_check()
        if connected and health.ok:
            status = ConnectorStatus.CONNECTED
            detail = health.detail or "Connected through the local secure credential store."
        elif connected:
            status = ConnectorStatus.EXPIRED
            detail = health.detail or "The saved credential needs to be refreshed."
        elif not self.descriptor.implemented:
            status = ConnectorStatus.UNAVAILABLE
            detail = health.detail or "This connector is not available in the current build."
        elif health.ok:
            status = ConnectorStatus.AVAILABLE
            detail = health.detail or "The connector is ready to connect."
        else:
            status = ConnectorStatus.NEEDS_SETUP
            detail = health.detail or "Connector setup is required before connecting an account."
        return ConnectorSnapshot(**self.descriptor.model_dump(), status=status, connected=connected, detail=detail, health=health)


class PlannedConnector(Connector):
    """Descriptor-only connector until its provider adapter is implemented."""

    def __init__(self, descriptor: ConnectorDescriptor, credentials: CredentialStore) -> None:
        self.descriptor = descriptor
        super().__init__(credentials)

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(ok=False, detail="The provider adapter is planned but not enabled yet.")

    async def connect(self, **options) -> ConnectorActionResult:
        raise ConnectorError("CONNECTOR_NOT_IMPLEMENTED", f"{self.name.title()} connection is not available yet; no credentials were requested.", status_code=501)
