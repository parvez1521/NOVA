import sys

import pytest
from fastapi.testclient import TestClient

from app.connectors.mcp import MCPManager
from app.connectors.models import ConnectorError
from app.core.config import Settings
from app.main import create_app


SERVER = """import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get('id') is None:
        continue
    method = message.get('method')
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {}, 'serverInfo': {'name': 'test'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'description': 'Echo safe test data', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False}}]}
    else:
        result = {'content': [{'type': 'text', 'text': 'echoed'}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
"""


@pytest.mark.asyncio
async def test_mcp_requires_explicit_registration_enable_and_execution_confirmation(tmp_path):
    script = tmp_path / "mcp_server.py"
    script.write_text(SERVER)
    manager = MCPManager(enabled=True)
    with pytest.raises(ConnectorError) as error:
        manager.register("demo", [sys.executable, str(script)])
    assert error.value.code == "MCP_REGISTRATION_CONFIRMATION_REQUIRED"
    registered = manager.register("demo", [sys.executable, str(script)], confirmed=True)
    assert registered["trusted"] is True and registered["enabled"] is False
    await manager.enable("demo", confirmed=True)
    discovered = await manager.discover("demo")
    assert discovered["tools"][0]["name"] == "echo"
    with pytest.raises(ConnectorError) as error:
        await manager.execute("demo", "echo", {"text": "hello"})
    assert error.value.code == "MCP_TOOL_CONFIRMATION_REQUIRED"
    result = await manager.execute("demo", "echo", {"text": "hello"}, confirmed=True)
    assert result["result"]["content"][0]["text"] == "echoed"
    await manager.disable("demo")
    assert manager.inspect("demo")["enabled"] is False
    await manager.close()


def test_mcp_api_reports_feature_flag(tmp_path):
    disabled = Settings(_env_file=None, database_path=str(tmp_path / "mcp-disabled.db"), mcp_enabled=False)
    with TestClient(create_app(disabled)) as client:
        assert client.get("/api/mcp").json()["enabled"] is False
    enabled = Settings(_env_file=None, database_path=str(tmp_path / "mcp-enabled.db"), mcp_enabled=True)
    with TestClient(create_app(enabled)) as client:
        assert client.get("/api/mcp").json()["enabled"] is True
