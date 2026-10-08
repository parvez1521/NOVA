"""Official Notion API adapter using an internal integration token.

The token is backend configuration only. This first adapter is deliberately
read-only: search, page/database retrieval and page block content inspection.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx

from app.connectors.base import Connector
from app.connectors.credentials import CredentialStore
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorError, ConnectorHealth, ConnectorSnapshot, ConnectorStatus
from app.database.privacy import has_secret, safe_text


_NOTION_ID = re.compile(r"^[0-9a-fA-F]{32}(?:-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?$")


class NotionConnector(Connector):
    descriptor = ConnectorDescriptor(
        name="notion", icon="N",
        description="Official Notion integration access for searchable workspace content.",
        auth="api_key", permissions=["pages.read", "databases.read"],
        capabilities=["search", "pages.read", "databases.read", "blocks.read"], implemented=True,
    )
    endpoint = "https://api.notion.com/v1"

    def __init__(self, credentials: CredentialStore, *, token: str = "", notion_version: str = "2022-06-28",
                 request: Callable | None = None) -> None:
        super().__init__(credentials)
        self.token = token.strip()
        self.notion_version = notion_version.strip() or "2022-06-28"
        self._request = request

    @property
    def configured(self) -> bool:
        return bool(self.token or self.credentials.has(self.name))

    def _token(self) -> str:
        if self.token: return self.token
        value = self.credentials.get(self.name)
        return value or ""

    async def health_check(self) -> ConnectorHealth:
        if not self.configured:
            return ConnectorHealth(ok=False, detail="Set NOTION_API_TOKEN to enable the official Notion integration.")
        try:
            payload, status = await self._request_json("GET", f"{self.endpoint}/users/me")
            if status == 401: return ConnectorHealth(ok=False, detail="Notion authorization requires re-authentication.")
            if status == 403: return ConnectorHealth(ok=False, detail="Notion integration permission was denied.")
            if status >= 400: return ConnectorHealth(ok=False, detail="Notion rejected the integration request.")
            return ConnectorHealth(ok=isinstance(payload, dict) and bool(payload.get("id")), detail="Notion official integration is authorized for read access.")
        except ConnectorError as error:
            return ConnectorHealth(ok=False, detail=error.message)

    async def connect(self, **options) -> ConnectorActionResult:
        if not self.configured:
            raise ConnectorError("NOTION_NOT_CONFIGURED", "Set the backend-only NOTION_API_TOKEN before connecting Notion.")
        health = await self.health_check()
        if not health.ok:
            raise ConnectorError("NOTION_AUTH_INVALID", health.detail, status_code=401)
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.CONNECTED, connected=True, detail=health.detail)

    async def disconnect(self) -> ConnectorActionResult:
        if self.token:
            return ConnectorActionResult(name=self.name, status=ConnectorStatus.NEEDS_SETUP, detail="Remove NOTION_API_TOKEN from backend configuration to disconnect the environment integration.")
        self.credentials.delete(self.name)
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.NEEDS_SETUP, detail="The Notion integration is disconnected.")

    async def snapshot(self) -> ConnectorSnapshot:
        health = await self.health_check()
        if health.ok:
            status, connected, detail = ConnectorStatus.CONNECTED, True, health.detail
        elif self.configured and "re-authentication" in health.detail.casefold():
            status, connected, detail = ConnectorStatus.REQUIRES_REAUTH, False, health.detail
        elif self.configured and "permission" in health.detail.casefold():
            status, connected, detail = ConnectorStatus.PERMISSION_DENIED, False, health.detail
        else:
            status, connected, detail = ConnectorStatus.NEEDS_SETUP, False, health.detail
        return ConnectorSnapshot(**self.descriptor.model_dump(), status=status, connected=connected, detail=detail, health=health)

    async def tools(self) -> list[dict[str, object]]:
        return [
            {"name": "notion.search", "description": "Search pages and databases visible to the official Notion integration.", "permission": "safe", "capability": "connector"},
            {"name": "notion.page", "description": "Retrieve metadata for one authorized Notion page or database.", "permission": "safe", "capability": "connector"},
            {"name": "notion.page_content", "description": "Read block content from one authorized Notion page.", "permission": "safe", "capability": "connector"},
        ]

    async def execute(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool == "notion.search": return await self.search(str(arguments.get("query", "")), int(arguments.get("max_results", 20)))
        identifier = self._id(str(arguments.get("page_id", arguments.get("database_id", ""))))
        if tool == "notion.page": return await self.page(identifier)
        if tool == "notion.page_content": return await self.page_content(identifier, int(arguments.get("max_results", 100)))
        raise ConnectorError("NOTION_TOOL_UNAVAILABLE", "That Notion connector tool is not available.")

    async def search(self, query: str = "", max_results: int = 20):
        if len(query) > 500 or has_secret(query): raise ConnectorError("NOTION_QUERY_INVALID", "Notion search must be non-sensitive and under 500 characters.", status_code=422)
        limit = self._limit(max_results)
        payload, status = await self._request_json("POST", f"{self.endpoint}/search", json={"query": safe_text(query), "page_size": limit})
        self._check(status)
        results = []
        for item in payload.get("results", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict): continue
            results.append({"id": safe_text(str(item.get("id", ""))), "object": safe_text(str(item.get("object", ""))), "url": safe_text(str(item.get("url", ""))), "archived": bool(item.get("archived")), "title": self._title(item)})
        return {"query": safe_text(query), "results": results[:limit]}

    async def page(self, identifier: str):
        payload, status = await self._request_json("GET", f"{self.endpoint}/pages/{identifier}")
        self._check(status)
        return self._page_summary(payload)

    async def page_content(self, identifier: str, max_results: int = 100):
        limit = self._limit(max_results, 1, 100)
        payload, status = await self._request_json("GET", f"{self.endpoint}/blocks/{identifier}/children", params={"page_size": limit})
        self._check(status)
        return {"page_id": identifier, "blocks": [self._block_summary(item) for item in payload.get("results", []) if isinstance(item, dict)][:limit]}

    async def _request_json(self, method: str, url: str, *, params=None, json=None):
        headers = {"Authorization": f"Bearer {self._token()}", "Notion-Version": self.notion_version, "Content-Type": "application/json"}
        if self._request:
            response = await self._request(method, url, params=params, json=json, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15) as client: response = await client.request(method, url, params=params, json=json, headers=headers)
        try: payload = response.json()
        except ValueError as error: raise ConnectorError("NOTION_RESPONSE_INVALID", "Notion returned an invalid response.", status_code=502) from error
        return payload if isinstance(payload, dict) else {}, response.status_code

    @staticmethod
    def _check(status: int):
        if status == 401: raise ConnectorError("NOTION_REAUTH_REQUIRED", "Notion authorization requires re-authentication.", status_code=401)
        if status == 403: raise ConnectorError("NOTION_PERMISSION_DENIED", "The Notion integration cannot access that content.", status_code=403)
        if status >= 400: raise ConnectorError("NOTION_API_ERROR", "Notion rejected the authorized request.", status_code=502)

    @staticmethod
    def _id(value: str) -> str:
        if not _NOTION_ID.fullmatch(value.replace("-", "")) and not _NOTION_ID.fullmatch(value):
            raise ConnectorError("NOTION_ID_INVALID", "The Notion page or database ID is invalid.", status_code=422)
        return value

    @staticmethod
    def _limit(value: int, low: int = 1, high: int = 100) -> int:
        if not low <= value <= high: raise ConnectorError("NOTION_LIMIT_INVALID", f"Notion result limits must be between {low} and {high}.", status_code=422)
        return value

    @staticmethod
    def _title(item: dict) -> str:
        properties = item.get("properties", {})
        for prop in properties.values() if isinstance(properties, dict) else []:
            values = prop.get("title") or prop.get("rich_text") if isinstance(prop, dict) else []
            if values and isinstance(values, list): return safe_text("".join(str(value.get("plain_text", "")) for value in values if isinstance(value, dict)))[:500]
        return safe_text(str(item.get("url", "")))[:500]

    @classmethod
    def _page_summary(cls, item: dict) -> dict:
        return {"id": safe_text(str(item.get("id", ""))), "url": safe_text(str(item.get("url", ""))), "object": safe_text(str(item.get("object", ""))), "archived": bool(item.get("archived")), "title": cls._title(item)}

    @staticmethod
    def _block_summary(item: dict) -> dict:
        kind = str(item.get("type", "")); data = item.get(kind, {}) if isinstance(item.get(kind), dict) else {}
        rich = data.get("rich_text") or data.get("title") or []
        text = "".join(str(value.get("plain_text", "")) for value in rich if isinstance(value, dict)) if isinstance(rich, list) else ""
        return {"id": safe_text(str(item.get("id", ""))), "type": safe_text(kind), "text": safe_text(text)[:3000], "has_children": bool(item.get("has_children"))}
