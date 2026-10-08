import pytest
import httpx
from pathlib import Path
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

from app.connectors.base import Connector
from app.connectors.credentials import MemoryCredentialStore
from app.connectors.manager import ConnectorManager
from app.connectors.models import ConnectorActionResult, ConnectorDescriptor, ConnectorHealth, ConnectorStatus
from app.connectors.google import GoogleConnector
from app.connectors.github import GitHubConnector
from app.connectors.notion import NotionConnector
from app.core.config import Settings
from app.main import create_app
from app.computer.executor import TaskExecutor
from app.computer.models import ComputerSettings


class FakeConnector(Connector):
    descriptor = ConnectorDescriptor(
        name="fake",
        icon="F",
        description="A test connector.",
        auth="api_key",
        permissions=["read"],
        capabilities=["read"],
        implemented=True,
    )

    async def health_check(self):
        return ConnectorHealth(ok=True, detail="Test connector adapter is ready.")

    async def connect(self, **options):
        self.credentials.set(self.name, "opaque-test-secret")
        return ConnectorActionResult(name=self.name, status=ConnectorStatus.CONNECTED, connected=True, detail="Connected in the test store.")


class FakeGoogle(GoogleConnector):
    async def _exchange(self, payload):
        return {"access_token": "google-access-secret", "refresh_token": "google-refresh-secret", "expires_in": 3600, "scope": self.scopes}

    async def _request_json(self, url, *, params, headers):
        if url.endswith("/messages"):
            return {"messages": [{"id": "abc123", "threadId": "thread-1"}], "_status": 200}
        if "calendar" in url:
            return {"items": [{"id": "event-1", "summary": "Review", "start": {"dateTime": "2026-10-07T10:00:00Z"}, "end": {"dateTime": "2026-10-07T11:00:00Z"}}], "_status": 200}
        if "drive" in url:
            return {"files": [{"id": "file-1", "name": "Brief", "mimeType": "text/plain"}], "_status": 200}
        return {"id": "abc123", "threadId": "thread-1", "snippet": "Brand update", "payload": {"headers": [{"name": "Subject", "value": "Brand update"}, {"name": "From", "value": "brand@example.com"}], "body": {"data": "SGVsbG8="}}, "_status": 200}


@pytest.mark.asyncio
async def test_connector_manager_keeps_credentials_out_of_snapshots():
    credentials = MemoryCredentialStore()
    manager = ConnectorManager(enabled=True, credentials=credentials, connectors=[FakeConnector(credentials)])
    before = await manager.get("fake")
    assert before.status == ConnectorStatus.AVAILABLE
    connected = await manager.connect("fake")
    assert connected.connected and connected.status == ConnectorStatus.CONNECTED
    after = (await manager.get("fake")).model_dump()
    assert after["status"] == "CONNECTED"
    assert "opaque-test-secret" not in str(after)
    assert await manager.tools("fake") == []


def test_connector_api_is_feature_flagged_and_does_not_accept_secrets(tmp_path):
    disabled = Settings(_env_file=None, database_path=str(tmp_path / "disabled.db"), connectors_enabled=False)
    with TestClient(create_app(disabled)) as client:
        response = client.get("/api/connectors")
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is False and payload["available"] is False
        assert all(item["status"] == "UNAVAILABLE" for item in payload["connectors"])
        assert client.post("/api/connectors/google/connect", json={"secret": "must-not-be-accepted"}).status_code == 422
        assert client.post("/api/connectors/google/connect").status_code == 409


def test_connector_api_exposes_metadata_and_explicit_unimplemented_boundary(tmp_path):
    credentials = MemoryCredentialStore()
    manager = ConnectorManager(enabled=True, credentials=credentials, connectors=[FakeConnector(credentials)])
    config = Settings(_env_file=None, database_path=str(tmp_path / "enabled.db"), connectors_enabled=True)
    with TestClient(create_app(config, connectors=manager)) as client:
        payload = client.get("/api/connectors").json()
        fake = next(item for item in payload["connectors"] if item["name"] == "fake")
        assert fake["status"] == "AVAILABLE"
        assert client.post("/api/connectors/fake/connect").json()["connected"] is True
        assert client.get("/api/connectors/fake").json()["status"] == "CONNECTED"
        response = client.post("/api/connectors/google/connect")
        assert response.status_code == 501
        assert response.headers["x-nova-error-code"] == "CONNECTOR_NOT_IMPLEMENTED"
        assert "opaque-test-secret" not in response.text


@pytest.mark.asyncio
async def test_github_and_notion_are_available_through_shared_computer_executor(tmp_path):
    class SharedManager:
        enabled = True
        connectors = {"github": object(), "notion": object()}

        async def execute(self, connector, tool, arguments):
            return {"connector": connector, "tool": tool, "arguments": arguments}

    executor = TaskExecutor(tmp_path, ComputerSettings(enabled=True), SharedManager())
    assert executor.registry.get("connector.github.issues")
    assert executor.registry.get("connector.notion.search")
    github = await executor.github_issues("org", "repo")
    notion = await executor.notion_search("content ideas")
    assert github["connector"] == "github" and github["tool"] == "github.issues"
    assert notion["connector"] == "notion" and notion["tool"] == "notion.search"


@pytest.mark.asyncio
async def test_google_oauth_state_token_storage_and_read_only_gmail():
    credentials = MemoryCredentialStore()
    google = FakeGoogle(credentials, client_id="client-id", client_secret="client-secret", redirect_uri="http://localhost/callback")
    start = await google.connect()
    query = parse_qs(urlparse(start.authorization_url).query)
    connected = await google.callback(code="one-time-code", state=query["state"][0])
    assert connected.connected and credentials.has("google")
    snapshot = (await google.snapshot()).model_dump()
    assert snapshot["status"] == "CONNECTED"
    assert "google-access-secret" not in str(snapshot)
    search = await google.execute("gmail.search", {"query": "from:brand@example.com", "max_results": 3})
    assert search["messages"] == [{"id": "abc123", "thread_id": "thread-1"}]
    message = await google.execute("gmail.read", {"message_id": "abc123"})
    assert message["subject"] == "Brand update" and message["body"] == "Hello"
    assert (await google.execute("calendar.list", {"max_results": 2}))["events"][0]["summary"] == "Review"
    assert (await google.execute("drive.search", {"query": "Brief"}))["files"][0]["name"] == "Brief"
    with pytest.raises(Exception):
        await google.callback(code="reused", state=query["state"][0])


@pytest.mark.asyncio
async def test_github_oauth_read_tools_and_secret_boundary():
    credentials = MemoryCredentialStore()

    async def request(method, url, **kwargs):
        if url.endswith("/login/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "github-secret", "token_type": "bearer", "scope": "read:user,public_repo"})
        assert kwargs["headers"]["Authorization"] == "Bearer github-secret"
        if url.endswith("/user"):
            return httpx.Response(200, json={"login": "nova-user", "name": "NOVA User", "email": "user@example.com", "html_url": "https://github.com/nova-user"})
        if url.endswith("/user/repos"):
            return httpx.Response(200, json=[{"full_name": "nova-user/demo", "name": "demo", "description": "Demo", "html_url": "https://github.com/nova-user/demo", "default_branch": "main"}])
        if "/search/repositories" in url:
            return httpx.Response(200, json={"items": [{"full_name": "org/tool", "name": "tool", "html_url": "https://github.com/org/tool"}]})
        if "/contents/README.md" in url:
            return httpx.Response(200, json={"type": "file", "sha": "abc", "content": "SGVsbG8gR2l0SHVi"})
        if url.endswith("/issues"):
            return httpx.Response(200, json=[{"number": 1, "title": "Bug", "state": "open", "html_url": "https://github.com/org/tool/issues/1"}])
        if url.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": 2, "title": "Change", "state": "open", "html_url": "https://github.com/org/tool/pull/2"}])
        if url.endswith("/branches"):
            return httpx.Response(200, json=[{"name": "main", "protected": True, "commit": {"sha": "abc"}}])
        return httpx.Response(404, json={"message": "missing"})

    github = GitHubConnector(credentials, client_id="client", client_secret="secret", redirect_uri="http://localhost/callback", request=request)
    start = await github.connect()
    state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
    connected = await github.callback(code="one-time-code", state=state)
    assert connected.connected and credentials.has("github")
    snapshot = (await github.snapshot()).model_dump()
    assert snapshot["status"] == "CONNECTED" and "github-secret" not in str(snapshot)
    assert (await github.execute("github.profile", {}))["login"] == "nova-user"
    assert (await github.execute("github.repositories", {"max_results": 2}))["repositories"][0]["full_name"] == "nova-user/demo"
    assert (await github.execute("github.search", {"query": "tool"}))["repositories"][0]["full_name"] == "org/tool"
    assert (await github.execute("github.read_file", {"owner": "org", "repo": "tool", "path": "README.md"}))["content"] == "Hello GitHub"
    assert (await github.execute("github.issues", {"owner": "org", "repo": "tool"}))["issues"][0]["number"] == "1"
    assert (await github.execute("github.pull_requests", {"owner": "org", "repo": "tool"}))["pull_requests"][0]["number"] == "2"
    assert (await github.execute("github.branches", {"owner": "org", "repo": "tool"}))["branches"][0]["name"] == "main"
    with pytest.raises(Exception):
        await github.execute("github.delete_repository", {"owner": "org", "repo": "tool"})


@pytest.mark.asyncio
async def test_notion_official_read_adapter_and_capability_truth():
    credentials = MemoryCredentialStore()

    async def request(method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer notion-secret"
        if url.endswith("/users/me"):
            return httpx.Response(200, json={"id": "a" * 32, "name": "NOVA integration"})
        if url.endswith("/search"):
            return httpx.Response(200, json={"results": [{"id": "b" * 32, "object": "page", "url": "https://notion.so/page", "properties": {"title": {"title": [{"plain_text": "Brief"}]}}}]})
        if "/pages/" in url:
            return httpx.Response(200, json={"id": "b" * 32, "object": "page", "url": "https://notion.so/page", "properties": {"title": {"title": [{"plain_text": "Brief"}]}}})
        if "/blocks/" in url:
            return httpx.Response(200, json={"results": [{"id": "c" * 32, "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Hello Notion"}]}, "has_children": False}]})
        return httpx.Response(404, json={})

    notion = NotionConnector(credentials, token="notion-secret", request=request)
    assert (await notion.connect()).connected
    snapshot = (await notion.snapshot()).model_dump()
    assert snapshot["status"] == "CONNECTED" and "notion-secret" not in str(snapshot)
    assert (await notion.execute("notion.search", {"query": "Brief"}))["results"][0]["title"] == "Brief"
    assert (await notion.execute("notion.page", {"page_id": "b" * 32}))["title"] == "Brief"
    assert (await notion.execute("notion.page_content", {"page_id": "b" * 32}))["blocks"][0]["text"] == "Hello Notion"
    assert all("write" not in str(tool).casefold() for tool in await notion.tools())


def test_google_read_tools_are_registered_only_when_connector_feature_is_enabled(tmp_path):
    credentials = MemoryCredentialStore()
    google = FakeGoogle(credentials, client_id="id", client_secret="secret", redirect_uri="http://localhost/callback")
    manager = ConnectorManager(enabled=True, credentials=credentials, connectors=[google])
    executor = TaskExecutor(Path(tmp_path), ComputerSettings(enabled=True), manager)
    names = {item["name"] for item in executor.registry.catalog()}
    assert {"connector.google.gmail.search", "connector.google.gmail.read", "connector.google.calendar.list", "connector.google.drive.search"} <= names

    disabled = ConnectorManager(enabled=False, credentials=MemoryCredentialStore(), connectors=[google])
    disabled_executor = TaskExecutor(Path(tmp_path), ComputerSettings(enabled=True), disabled)
    assert not any(name.startswith("connector.google") for name in {item["name"] for item in disabled_executor.registry.catalog()})
