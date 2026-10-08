"""Explicitly trusted MCP stdio server manager.

MCP servers are never discovered or launched automatically. Registration and
each tool execution require explicit local control, and the transport uses a
fixed executable invocation without a shell.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.connectors.models import ConnectorError
from app.database.privacy import has_secret, safe_text


@dataclass
class MCPServer:
    name: str
    command: list[str]
    cwd: str | None = None
    enabled: bool = False
    trusted: bool = False
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    session: "MCPStdioSession | None" = None
    error: str = ""


class MCPStdioSession:
    def __init__(self, command: list[str], cwd: str | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self.next_id = 0
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(*self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        await self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "NOVA", "version": "0.2.0"}})
        await self.notify("notifications/initialized", {})

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if not self.process or not self.process.stdin or not self.process.stdout:
                raise ConnectorError("MCP_SESSION_UNAVAILABLE", "The MCP server session is not running.")
            self.next_id += 1
            identifier = self.next_id
            await self._write({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            while True:
                line = await asyncio.wait_for(self.process.stdout.readline(), 30)
                if not line:
                    raise ConnectorError("MCP_SERVER_CLOSED", "The MCP server closed its local session.")
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if value.get("id") != identifier:
                    continue
                if "error" in value:
                    raise ConnectorError("MCP_REQUEST_FAILED", "The MCP server rejected the request.")
                result = value.get("result", {})
                return result if isinstance(result, dict) else {"value": result}

    async def _write(self, value: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise ConnectorError("MCP_SESSION_UNAVAILABLE", "The MCP server session is not running.")
        encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > 256_000:
            raise ConnectorError("MCP_MESSAGE_TOO_LARGE", "The MCP message is too large.")
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()

    async def close(self) -> None:
        if self.process:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 1)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None


class MCPManager:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self.servers: dict[str, MCPServer] = {}

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ConnectorError("MCP_DISABLED", "MCP is disabled in NOVA settings.")

    def _get(self, name: str) -> MCPServer:
        server = self.servers.get(name)
        if not server:
            raise ConnectorError("MCP_SERVER_NOT_FOUND", "That MCP server is not registered.", status_code=404)
        return server

    def register(self, name: str, command: list[str], *, cwd: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        self._require_enabled()
        if not confirmed:
            raise ConnectorError("MCP_REGISTRATION_CONFIRMATION_REQUIRED", "Confirm registration before NOVA stores an MCP server definition.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", name):
            raise ConnectorError("MCP_NAME_INVALID", "MCP server names must be lowercase letters, numbers, hyphens or underscores.", status_code=422)
        if not command or len(command) > 16 or any(not isinstance(item, str) or not item or len(item) > 500 for item in command):
            raise ConnectorError("MCP_COMMAND_INVALID", "MCP needs a bounded executable command and argument list.", status_code=422)
        if any(any(marker in item for marker in ("|", ";", "&", ">", "<", "`", "$")) for item in command):
            raise ConnectorError("MCP_SHELL_SYNTAX_BLOCKED", "Shell syntax is not accepted in MCP commands.", status_code=422)
        executable = command[0] if os.path.isabs(command[0]) else shutil.which(command[0])
        if not executable or not Path(executable).is_file():
            raise ConnectorError("MCP_EXECUTABLE_UNAVAILABLE", "The MCP executable is not installed or cannot be resolved.", status_code=422)
        resolved_cwd = str(Path(cwd).expanduser().resolve()) if cwd else None
        if resolved_cwd and not Path(resolved_cwd).is_dir():
            raise ConnectorError("MCP_WORKDIR_INVALID", "The MCP working directory does not exist.", status_code=422)
        if name in self.servers:
            raise ConnectorError("MCP_SERVER_EXISTS", "An MCP server with that name is already registered.")
        self.servers[name] = MCPServer(name=name, command=[executable, *command[1:]], cwd=resolved_cwd, trusted=True)
        return self.inspect(name)

    def inspect(self, name: str) -> dict[str, Any]:
        server = self._get(name)
        return {"name": server.name, "command": [server.command[0], *["[argument]" for _ in server.command[1:]]], "cwd": server.cwd, "enabled": server.enabled, "trusted": server.trusted, "tools": list(server.tools.values()), "error": server.error}

    def list(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "servers": [self.inspect(name) for name in sorted(self.servers)]}

    async def enable(self, name: str, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_enabled()
        if not confirmed:
            raise ConnectorError("MCP_ENABLE_CONFIRMATION_REQUIRED", "Confirm before launching this MCP server.")
        server = self._get(name)
        server.enabled = True
        server.error = ""
        return self.inspect(name)

    async def disable(self, name: str) -> dict[str, Any]:
        server = self._get(name)
        server.enabled = False
        if server.session:
            await server.session.close()
            server.session = None
        return self.inspect(name)

    async def discover(self, name: str) -> dict[str, Any]:
        self._require_enabled()
        server = self._get(name)
        if not server.enabled or not server.trusted:
            raise ConnectorError("MCP_NOT_ENABLED", "Explicitly enable the registered MCP server before discovery.")
        try:
            if not server.session:
                server.session = MCPStdioSession(server.command, server.cwd)
                await server.session.start()
            result = await server.session.request("tools/list", {})
            tools = result.get("tools", [])
            if not isinstance(tools, list) or len(tools) > 100:
                raise ConnectorError("MCP_TOOL_LIST_INVALID", "The MCP server returned an invalid tool list.")
            server.tools = {}
            for item in tools:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", item["name"]):
                    continue
                schema = item.get("inputSchema", {"type": "object", "additionalProperties": False})
                if not isinstance(schema, dict) or len(json.dumps(schema)) > 50_000:
                    continue
                server.tools[item["name"]] = {"name": item["name"], "description": safe_text(str(item.get("description", "MCP tool")))[:300], "input_schema": schema, "permission": "confirm"}
            return self.inspect(name)
        except (ConnectorError, OSError, asyncio.TimeoutError) as error:
            server.error = safe_text(getattr(error, "message", "MCP discovery failed"))[:240]
            await self.disable(name)
            raise ConnectorError("MCP_DISCOVERY_FAILED", server.error, status_code=502) from error

    @staticmethod
    def _validate_arguments(tool: dict[str, Any], arguments: dict[str, Any]) -> None:
        schema = tool.get("input_schema", {})
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 50_000 or has_secret(json.dumps(arguments)):
            raise ConnectorError("MCP_ARGUMENTS_BLOCKED", "MCP arguments are invalid or contain sensitive credentials.", status_code=422)
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if any(key not in arguments for key in required):
            raise ConnectorError("MCP_ARGUMENTS_INVALID", "Required MCP tool arguments are missing.", status_code=422)
        if schema.get("additionalProperties") is False and isinstance(schema.get("properties"), dict) and any(key not in schema["properties"] for key in arguments):
            raise ConnectorError("MCP_ARGUMENTS_INVALID", "Unknown MCP tool arguments are blocked.", status_code=422)

    async def execute(self, name: str, tool_name: str, arguments: dict[str, Any], *, confirmed: bool = False) -> dict[str, Any]:
        self._require_enabled()
        server = self._get(name)
        if not server.enabled or not server.trusted:
            raise ConnectorError("MCP_NOT_ENABLED", "Enable the MCP server before execution.")
        if not confirmed:
            raise ConnectorError("MCP_TOOL_CONFIRMATION_REQUIRED", "Confirm this MCP tool execution before NOVA sends it.")
        if tool_name not in server.tools:
            await self.discover(name)
        tool = server.tools.get(tool_name)
        if not tool:
            raise ConnectorError("MCP_TOOL_NOT_FOUND", "That MCP tool was not discovered.", status_code=404)
        self._validate_arguments(tool, arguments)
        if not server.session:
            await self.discover(name)
        result = await server.session.request("tools/call", {"name": tool_name, "arguments": arguments})
        return {"server": name, "tool": tool_name, "result": result}

    async def close(self) -> None:
        for server in self.servers.values():
            if server.session:
                await server.session.close()
                server.session = None
