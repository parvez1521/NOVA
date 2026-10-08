"""Official GitHub OAuth and read-focused API adapter.

The adapter intentionally exposes only read tools in this milestone. OAuth
tokens remain filtered JSON in the backend credential store and are never
returned in snapshots, errors or frontend responses.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from app.connectors.base import Connector
from app.connectors.credentials import CredentialStore
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorError, ConnectorHealth, ConnectorStatus
from app.database.privacy import has_secret, safe_text


_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class GitHubConnector(Connector):
    descriptor = ConnectorDescriptor(
        name="github", icon="GH",
        description="Official GitHub access for profile, repositories, code, issues, pull requests and branches.",
        auth="oauth2", permissions=["profile.read", "repo.read", "issues.read", "pull_requests.read", "branches.read"],
        capabilities=["profile", "repositories", "search", "files", "issues", "pull_requests", "branches"],
        implemented=True,
    )
    authorization_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    api_endpoint = "https://api.github.com"

    def __init__(self, credentials: CredentialStore, *, client_id: str = "", client_secret: str = "",
                 redirect_uri: str = "", scopes: str = "read:user public_repo", request: Callable | None = None) -> None:
        super().__init__(credentials)
        self.client_id, self.client_secret, self.redirect_uri = client_id.strip(), client_secret, redirect_uri.strip()
        self.scopes = scopes.strip() or "read:user public_repo"
        self._states: dict[str, tuple[str, float]] = {}
        self._request = request

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    async def health_check(self) -> ConnectorHealth:
        if not self.configured:
            return ConnectorHealth(ok=False, detail="Set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and GITHUB_REDIRECT_URI to enable GitHub OAuth.")
        token = self._load_token()
        if not token or not token.get("access_token"):
            return ConnectorHealth(ok=False, detail="GitHub is ready for authorization; no account is connected.")
        return ConnectorHealth(ok=True, detail="GitHub OAuth credential is stored in the local secure store.")

    async def connect(self, **options) -> ConnectorActionResult:
        if not self.configured:
            raise ConnectorError("GITHUB_OAUTH_NOT_CONFIGURED", "GitHub OAuth is not configured. Set the backend GitHub OAuth values first.")
        redirect_uri = str(options.get("redirect_uri") or self.redirect_uri).strip()
        if redirect_uri != self.redirect_uri:
            raise ConnectorError("GITHUB_REDIRECT_URI_INVALID", "The requested redirect URI does not match NOVA's configured GitHub redirect URI.")
        state = secrets.token_urlsafe(32)
        self._states[state] = (redirect_uri, time.monotonic() + 600)
        params = {"client_id": self.client_id, "redirect_uri": redirect_uri, "scope": self.scopes, "state": state}
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.NEEDS_SETUP,
            detail="Authorize GitHub in the browser, then return to NOVA.",
            authorization_url=f"{self.authorization_endpoint}?{urlencode(params)}")

    async def callback(self, *, code: str | None = None, state: str | None = None, error: str | None = None) -> ConnectorActionResult:
        if error:
            raise ConnectorError("GITHUB_OAUTH_DENIED", "GitHub authorization was cancelled or denied.")
        if not code or not state:
            raise ConnectorError("GITHUB_OAUTH_CALLBACK_INVALID", "GitHub authorization did not return the required callback values.", status_code=400)
        stored = self._states.pop(state, None)
        if not stored or time.monotonic() > stored[1] or not secrets.compare_digest(stored[0], self.redirect_uri):
            raise ConnectorError("GITHUB_OAUTH_STATE_INVALID", "The GitHub authorization session expired or is invalid.", status_code=400)
        if len(code) > 4096 or has_secret(code):
            raise ConnectorError("GITHUB_OAUTH_CODE_INVALID", "The GitHub authorization code was rejected.", status_code=400)
        token = await self._exchange({"client_id": self.client_id, "client_secret": self.client_secret, "code": code, "redirect_uri": self.redirect_uri})
        self._save_token(token)
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.CONNECTED, connected=True,
            detail="GitHub is connected with the configured read permissions.")

    async def tools(self) -> list[dict[str, object]]:
        return [
            {"name": "github.profile", "description": "Read the connected GitHub profile.", "permission": "safe", "capability": "connector"},
            {"name": "github.repositories", "description": "List repositories visible to the connected GitHub account.", "permission": "safe", "capability": "connector"},
            {"name": "github.search", "description": "Search GitHub repositories through the official API.", "permission": "safe", "capability": "connector"},
            {"name": "github.read_file", "description": "Read a file from an authorized repository.", "permission": "safe", "capability": "connector"},
            {"name": "github.issues", "description": "List issues from an authorized repository.", "permission": "safe", "capability": "connector"},
            {"name": "github.pull_requests", "description": "List pull requests from an authorized repository.", "permission": "safe", "capability": "connector"},
            {"name": "github.branches", "description": "List branches from an authorized repository.", "permission": "safe", "capability": "connector"},
        ]

    async def execute(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool == "github.profile": return await self.profile()
        if tool == "github.repositories": return await self.repositories(int(arguments.get("max_results", 30)))
        if tool == "github.search": return await self.search(str(arguments.get("query", "")), int(arguments.get("max_results", 10)))
        owner, repo = self._repo(arguments)
        if tool == "github.read_file": return await self.read_file(owner, repo, str(arguments.get("path", "")), str(arguments.get("ref", "")))
        if tool == "github.issues": return await self.issues(owner, repo, str(arguments.get("state", "open")), int(arguments.get("max_results", 20)))
        if tool == "github.pull_requests": return await self.pull_requests(owner, repo, str(arguments.get("state", "open")), int(arguments.get("max_results", 20)))
        if tool == "github.branches": return await self.branches(owner, repo, int(arguments.get("max_results", 30)))
        raise ConnectorError("GITHUB_TOOL_UNAVAILABLE", "That GitHub connector tool is not available.")

    async def profile(self):
        payload = await self._api_get("/user")
        return {key: safe_text(str(payload.get(key, "")))[:500] for key in ("login", "name", "email", "html_url", "avatar_url", "bio")}

    async def repositories(self, max_results: int = 30):
        limit = self._limit(max_results, 1, 100)
        payload = await self._api_get("/user/repos", {"per_page": limit, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
        return {"repositories": [self._repo_summary(item) for item in self._items(payload) if isinstance(item, dict)][:limit]}

    async def search(self, query: str, max_results: int = 10):
        if not query.strip() or len(query) > 300 or has_secret(query):
            raise ConnectorError("GITHUB_QUERY_INVALID", "GitHub search needs a non-sensitive query under 300 characters.", status_code=422)
        limit = self._limit(max_results, 1, 50)
        payload = await self._api_get("/search/repositories", {"q": safe_text(query), "per_page": limit})
        return {"query": safe_text(query), "repositories": [self._repo_summary(item) for item in payload.get("items", []) if isinstance(item, dict)][:limit]}

    async def read_file(self, owner: str, repo: str, path: str, ref: str = ""):
        self._path(path)
        params = {"ref": ref} if ref else None
        payload = await self._api_get(f"/repos/{owner}/{repo}/contents/{path}", params)
        if payload.get("type") != "file":
            raise ConnectorError("GITHUB_NOT_A_FILE", "The selected GitHub path is not a regular file.", status_code=422)
        encoded = payload.get("content", "").replace("\n", "")
        try:
            content = base64.b64decode(encoded).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            raise ConnectorError("GITHUB_FILE_INVALID", "GitHub returned a file that NOVA could not decode.", status_code=502)
        return {"owner": owner, "repository": repo, "path": path, "sha": safe_text(str(payload.get("sha", ""))), "content": safe_text(content)[:100_000]}

    async def issues(self, owner: str, repo: str, state: str = "open", max_results: int = 20):
        if state not in {"open", "closed", "all"}: raise ConnectorError("GITHUB_STATE_INVALID", "Issue state must be open, closed or all.", status_code=422)
        limit = self._limit(max_results, 1, 100)
        payload = await self._api_get(f"/repos/{owner}/{repo}/issues", {"state": state, "per_page": limit})
        return {"issues": [self._issue_summary(item) for item in self._items(payload) if isinstance(item, dict) and not item.get("pull_request")][:limit]}

    async def pull_requests(self, owner: str, repo: str, state: str = "open", max_results: int = 20):
        if state not in {"open", "closed", "all"}: raise ConnectorError("GITHUB_STATE_INVALID", "Pull request state must be open, closed or all.", status_code=422)
        limit = self._limit(max_results, 1, 100)
        payload = await self._api_get(f"/repos/{owner}/{repo}/pulls", {"state": state, "per_page": limit})
        return {"pull_requests": [self._issue_summary(item) for item in self._items(payload) if isinstance(item, dict)][:limit]}

    async def branches(self, owner: str, repo: str, max_results: int = 30):
        limit = self._limit(max_results, 1, 100)
        payload = await self._api_get(f"/repos/{owner}/{repo}/branches", {"per_page": limit})
        return {"branches": [{"name": safe_text(str(item.get("name", ""))), "protected": bool(item.get("protected")), "sha": safe_text(str(item.get("commit", {}).get("sha", "")))} for item in self._items(payload) if isinstance(item, dict)][:limit]}

    async def _api_get(self, path: str, params: dict[str, object] | None = None):
        token = self._access_token()
        response = await self._request_json("GET", f"{self.api_endpoint}{path}", params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        status = response.pop("_status", 200)
        if status == 401: raise ConnectorError("GITHUB_REAUTH_REQUIRED", "GitHub authorization expired. Reconnect the account.", status_code=401)
        if status == 403: raise ConnectorError("GITHUB_PERMISSION_DENIED", "GitHub rejected the requested permission or rate limit.", status_code=403)
        if status >= 400: raise ConnectorError("GITHUB_API_ERROR", "GitHub rejected the authorized read request.", status_code=502)
        return response

    async def _request_json(self, method: str, url: str, *, params=None, headers=None, data=None):
        if self._request: response = await self._request(method, url, params=params, headers=headers, data=data)
        else:
            async with httpx.AsyncClient(timeout=15) as client: response = await client.request(method, url, params=params, headers=headers, data=data)
        try: payload = response.json()
        except ValueError as error: raise ConnectorError("GITHUB_RESPONSE_INVALID", "GitHub returned an invalid response.", status_code=502) from error
        return {**payload, "_status": response.status_code} if isinstance(payload, dict) else {"_data": payload, "_status": response.status_code}

    async def _exchange(self, payload: dict[str, str]):
        response = await self._request_json("POST", self.token_endpoint, headers={"Accept": "application/json"}, data=payload)
        if response.get("_status", 200) >= 400 or not response.get("access_token"):
            raise ConnectorError("GITHUB_TOKEN_EXCHANGE_FAILED", "GitHub authorization could not be completed.", status_code=502)
        return response

    def _access_token(self) -> str:
        token = self._load_token()
        if not token or not token.get("access_token"): raise ConnectorError("GITHUB_NOT_CONNECTED", "Connect GitHub before using GitHub tools.")
        return str(token["access_token"])

    def _load_token(self):
        raw = self.credentials.get(self.name)
        try: value = json.loads(raw) if raw else None
        except (TypeError, ValueError): return None
        return value if isinstance(value, dict) else None

    def _save_token(self, token):
        filtered = {key: token[key] for key in ("access_token", "token_type", "scope") if token.get(key)}
        self.credentials.set(self.name, json.dumps(filtered, separators=(",", ":")))

    @staticmethod
    def _items(payload):
        return payload.get("_data", payload.get("items", [])) if isinstance(payload, dict) else []

    @staticmethod
    def _repo(arguments):
        owner, repo = str(arguments.get("owner", "")), str(arguments.get("repo", ""))
        if not _SLUG.fullmatch(owner) or not _SLUG.fullmatch(repo): raise ConnectorError("GITHUB_REPOSITORY_INVALID", "GitHub owner and repository names are invalid.", status_code=422)
        return owner, repo

    @staticmethod
    def _path(path):
        if not path or len(path) > 500 or path.startswith("/") or ".." in path.split("/"): raise ConnectorError("GITHUB_PATH_INVALID", "GitHub file paths must stay inside the selected repository.", status_code=422)

    @staticmethod
    def _limit(value, low, high):
        if not low <= value <= high: raise ConnectorError("GITHUB_LIMIT_INVALID", f"GitHub result limits must be between {low} and {high}.", status_code=422)
        return value

    @staticmethod
    def _repo_summary(item):
        return {key: safe_text(str(item.get(key, "")))[:500] for key in ("full_name", "name", "description", "html_url", "default_branch", "language", "updated_at")}

    @staticmethod
    def _issue_summary(item):
        return {key: safe_text(str(item.get(key, "")))[:500] for key in ("number", "title", "state", "html_url", "user", "created_at", "updated_at")}
