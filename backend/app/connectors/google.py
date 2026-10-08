"""Google OAuth and read-only Gmail connector.

Only Gmail search/read is exposed in this first provider adapter. OAuth
credentials and tokens remain backend-only and are stored as a small filtered
JSON record in the configured secure credential store.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlencode

import httpx

from app.connectors.base import Connector
from app.connectors.credentials import CredentialStore
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorError, ConnectorHealth, ConnectorSnapshot, ConnectorStatus
from app.database.privacy import has_secret, safe_text


class GoogleConnector(Connector):
    descriptor = ConnectorDescriptor(
        name="google",
        icon="G",
        description="Read-only Gmail access through explicit Google OAuth.",
        auth="oauth2",
        permissions=["gmail.readonly", "calendar.readonly", "drive.readonly"],
        capabilities=["gmail.search", "gmail.read", "calendar.list", "drive.search"],
        implemented=True,
    )
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    gmail_endpoint = "https://gmail.googleapis.com/gmail/v1/users/me"
    calendar_endpoint = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    drive_endpoint = "https://www.googleapis.com/drive/v3/files"
    default_scope = "https://www.googleapis.com/auth/gmail.readonly"

    def __init__(self, credentials: CredentialStore, *, client_id: str = "", client_secret: str = "", redirect_uri: str = "", scopes: str = default_scope, request: Callable | None = None) -> None:
        super().__init__(credentials)
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri.strip()
        self.scopes = scopes.strip() or self.default_scope
        self._states: dict[str, tuple[str, float]] = {}
        self._request = request

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    async def health_check(self) -> ConnectorHealth:
        if not self.configured:
            return ConnectorHealth(ok=False, detail="Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI to enable Google OAuth.")
        token = self._load_token()
        if token is None:
            return ConnectorHealth(ok=False, detail="Google is ready for authorization; no account is connected.")
        if not token.get("access_token") and not token.get("refresh_token"):
            return ConnectorHealth(ok=False, detail="The stored Google credential is invalid.")
        return ConnectorHealth(ok=True, detail="Google OAuth credential is stored in the local secure store.")

    async def connect(self, **options) -> ConnectorActionResult:
        if not self.configured:
            raise ConnectorError("GOOGLE_OAUTH_NOT_CONFIGURED", "Google OAuth is not configured. Set the backend Google OAuth environment values first.")
        redirect_uri = str(options.get("redirect_uri") or self.redirect_uri).strip()
        if redirect_uri != self.redirect_uri:
            raise ConnectorError("GOOGLE_REDIRECT_URI_INVALID", "The requested redirect URI does not match NOVA's configured Google OAuth redirect URI.")
        state = secrets.token_urlsafe(32)
        self._states[state] = (redirect_uri, time.monotonic() + 600)
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.NEEDS_SETUP, detail="Authorize Google in the browser, then return to NOVA.", authorization_url=f"{self.authorization_endpoint}?{urlencode(params)}")

    async def callback(self, *, code: str | None = None, state: str | None = None, error: str | None = None) -> ConnectorActionResult:
        if error:
            raise ConnectorError("GOOGLE_OAUTH_DENIED", "Google authorization was cancelled or denied.")
        if not code or not state:
            raise ConnectorError("GOOGLE_OAUTH_CALLBACK_INVALID", "Google authorization did not return the required callback values.", status_code=400)
        stored = self._states.pop(state, None)
        if not stored or time.monotonic() > stored[1] or not secrets.compare_digest(stored[0], self.redirect_uri):
            raise ConnectorError("GOOGLE_OAUTH_STATE_INVALID", "The Google authorization session expired or is invalid.", status_code=400)
        if len(code) > 4096 or has_secret(code):
            raise ConnectorError("GOOGLE_OAUTH_CODE_INVALID", "The Google authorization code was rejected.", status_code=400)
        token = await self._exchange({"code": code, "client_id": self.client_id, "client_secret": self.client_secret, "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"})
        self._save_token(token)
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.CONNECTED, connected=True, detail="Google Gmail is connected with read-only access.")

    async def refresh(self) -> ConnectorSnapshot:
        token = self._load_token()
        if token and token.get("refresh_token") and float(token.get("expires_at", 0) or 0) <= time.time() + 30:
            try:
                refreshed = await self._exchange({"client_id": self.client_id, "client_secret": self.client_secret, "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
                refreshed.setdefault("refresh_token", token["refresh_token"])
                self._save_token(refreshed)
            except ConnectorError:
                pass
        return await self.snapshot()

    async def tools(self) -> list[dict[str, object]]:
        return [
            {"name": "gmail.search", "description": "Search the connected Gmail mailbox without sending or changing messages.", "permission": "safe", "capability": "connector"},
            {"name": "gmail.read", "description": "Read one authorized Gmail message by observed message ID.", "permission": "safe", "capability": "connector"},
            {"name": "calendar.list", "description": "List events from the authorized primary Google Calendar.", "permission": "safe", "capability": "connector"},
            {"name": "drive.search", "description": "Search metadata for files in the authorized Google Drive.", "permission": "safe", "capability": "connector"},
        ]

    async def execute(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool == "gmail.search":
            return await self.gmail_search(str(arguments.get("query", "")), int(arguments.get("max_results", 5)))
        if tool == "gmail.read":
            return await self.gmail_read(str(arguments.get("message_id", "")))
        if tool == "calendar.list":
            return await self.calendar_list(str(arguments.get("time_min", "")), str(arguments.get("time_max", "")), int(arguments.get("max_results", 10)))
        if tool == "drive.search":
            return await self.drive_search(str(arguments.get("query", "")), int(arguments.get("max_results", 10)))
        raise ConnectorError("CONNECTOR_TOOL_UNAVAILABLE", "That Google connector tool is not available.")

    async def gmail_search(self, query: str, max_results: int = 5) -> dict[str, object]:
        if not query.strip() or len(query) > 500 or has_secret(query):
            raise ConnectorError("GMAIL_QUERY_INVALID", "Gmail search needs a non-sensitive query under 500 characters.", status_code=422)
        if not 1 <= max_results <= 20:
            raise ConnectorError("GMAIL_LIMIT_INVALID", "Gmail search can return between 1 and 20 messages.", status_code=422)
        payload = await self._gmail_get("/messages", {"q": query, "maxResults": max_results})
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        return {"query": safe_text(query), "messages": [{"id": safe_text(item.get("id", "")), "thread_id": safe_text(item.get("threadId", ""))} for item in messages if isinstance(item, dict)][:max_results]}

    async def gmail_read(self, message_id: str) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
            raise ConnectorError("GMAIL_MESSAGE_ID_INVALID", "The Gmail message ID is invalid.", status_code=422)
        payload = await self._gmail_get(f"/messages/{message_id}", {"format": "full"})
        headers = {str(item.get("name", "")).casefold(): safe_text(str(item.get("value", "")))[:500] for item in payload.get("payload", {}).get("headers", []) if isinstance(item, dict)}
        body = self._body(payload.get("payload", {}))
        return {"id": message_id, "thread_id": safe_text(str(payload.get("threadId", ""))), "subject": headers.get("subject", "(no subject)"), "from": headers.get("from", ""), "to": headers.get("to", ""), "date": headers.get("date", ""), "snippet": safe_text(str(payload.get("snippet", "")))[:1000], "body": body[:6000]}

    async def calendar_list(self, time_min: str = "", time_max: str = "", max_results: int = 10) -> dict[str, object]:
        if not 1 <= max_results <= 50:
            raise ConnectorError("CALENDAR_LIMIT_INVALID", "Calendar listing can return between 1 and 50 events.", status_code=422)
        for value in (time_min, time_max):
            if value:
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ConnectorError("CALENDAR_TIME_INVALID", "Calendar times must be ISO-8601 values.", status_code=422) from error
        params: dict[str, object] = {"maxResults": max_results, "singleEvents": "true", "orderBy": "startTime"}
        if time_min: params["timeMin"] = time_min
        if time_max: params["timeMax"] = time_max
        payload = await self._api_get(self.calendar_endpoint, params)
        events = []
        for event in payload.get("items", []) if isinstance(payload, dict) else []:
            if not isinstance(event, dict): continue
            events.append({"id": safe_text(str(event.get("id", ""))), "summary": safe_text(str(event.get("summary", "(untitled)")))[:300], "status": safe_text(str(event.get("status", ""))), "start": event.get("start", {}), "end": event.get("end", {}), "html_link": safe_text(str(event.get("htmlLink", "")))})
        return {"events": events[:max_results], "time_min": time_min, "time_max": time_max}

    async def drive_search(self, query: str, max_results: int = 10) -> dict[str, object]:
        if not query.strip() or len(query) > 500 or has_secret(query):
            raise ConnectorError("DRIVE_QUERY_INVALID", "Drive search needs a non-sensitive query under 500 characters.", status_code=422)
        if not 1 <= max_results <= 50:
            raise ConnectorError("DRIVE_LIMIT_INVALID", "Drive search can return between 1 and 50 files.", status_code=422)
        escaped = query.replace("\\", "\\\\").replace("'", "\\'")
        payload = await self._api_get(self.drive_endpoint, {"q": f"name contains '{escaped}' and trashed = false", "pageSize": max_results, "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)"})
        files = []
        for item in payload.get("files", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict): continue
            files.append({key: safe_text(str(item.get(key, "")))[:500] for key in ("id", "name", "mimeType", "modifiedTime", "size", "webViewLink")})
        return {"query": safe_text(query), "files": files[:max_results]}

    async def _gmail_get(self, path: str, params: dict[str, object]) -> dict:
        return await self._api_get(f"{self.gmail_endpoint}{path}", params)

    async def _api_get(self, url: str, params: dict[str, object]) -> dict:
        token = await self._access_token()
        response = await self._request_json(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if response.get("_status") == 401:
            token = await self._access_token(force_refresh=True)
            response = await self._request_json(url, params=params, headers={"Authorization": f"Bearer {token}"})
        status = int(response.pop("_status", 200))
        if status >= 400:
            raise ConnectorError("GMAIL_API_ERROR", "Google Gmail rejected the authorized read request.", status_code=502)
        return response

    async def _access_token(self, *, force_refresh: bool = False) -> str:
        token = self._load_token()
        if not token:
            raise ConnectorError("GOOGLE_NOT_CONNECTED", "Connect Google before reading Gmail.")
        if not force_refresh and token.get("access_token") and float(token.get("expires_at", 0) or 0) > time.time() + 30:
            return str(token["access_token"])
        if not token.get("refresh_token"):
            raise ConnectorError("GOOGLE_TOKEN_EXPIRED", "Google authorization expired. Reconnect the account.")
        refreshed = await self._exchange({"client_id": self.client_id, "client_secret": self.client_secret, "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
        refreshed.setdefault("refresh_token", token["refresh_token"])
        self._save_token(refreshed)
        return str(refreshed["access_token"])

    async def _exchange(self, payload: dict[str, str]) -> dict[str, object]:
        if self._request:
            response = await self._request("POST", self.token_endpoint, data=payload)
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(self.token_endpoint, data=payload)
        if response.status_code >= 400:
            raise ConnectorError("GOOGLE_TOKEN_EXCHANGE_FAILED", "Google authorization could not be completed.", status_code=502)
        try:
            value = response.json()
        except ValueError as error:
            raise ConnectorError("GOOGLE_TOKEN_RESPONSE_INVALID", "Google returned an invalid authorization response.", status_code=502) from error
        if not isinstance(value, dict) or not value.get("access_token"):
            raise ConnectorError("GOOGLE_TOKEN_RESPONSE_INVALID", "Google did not return an access token.", status_code=502)
        return value

    async def _request_json(self, url: str, *, params: dict[str, object], headers: dict[str, str]) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params, headers=headers)
        try:
            value = response.json()
        except ValueError as error:
            raise ConnectorError("GOOGLE_API_RESPONSE_INVALID", "Google returned an invalid Gmail response.", status_code=502) from error
        return {**value, "_status": response.status_code} if isinstance(value, dict) else {"_status": response.status_code}

    def _load_token(self) -> dict[str, object] | None:
        raw = self.credentials.get(self.name)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _save_token(self, token: dict[str, object]) -> None:
        filtered = {key: token[key] for key in ("access_token", "refresh_token", "token_type", "scope") if token.get(key)}
        filtered["expires_at"] = time.time() + max(0, int(token.get("expires_in", 3600)))
        self.credentials.set(self.name, json.dumps(filtered, separators=(",", ":")))

    @staticmethod
    def _body(payload: dict) -> str:
        parts = payload.get("parts") or []
        if parts:
            for part in parts:
                if isinstance(part, dict) and part.get("mimeType") == "text/plain":
                    return GoogleConnector._decode(part.get("body", {}).get("data", ""))
            for part in parts:
                if isinstance(part, dict):
                    body = GoogleConnector._body(part)
                    if body:
                        return body
        return GoogleConnector._decode(payload.get("body", {}).get("data", ""))

    @staticmethod
    def _decode(value: str) -> str:
        if not value:
            return ""
        try:
            return safe_text(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", errors="replace"))
        except (ValueError, base64.binascii.Error):
            return ""
