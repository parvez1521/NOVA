"""Sanitized HTTP/WebSocket connection diagnostic for NOVA's local backend.

Pass NATIVE_SESSION through the environment when testing a packaged session.
The value is used for auth but is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse

import httpx
from websockets.asyncio.client import connect


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8742")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    session = os.environ.get("NATIVE_SESSION", "")
    headers = {"x-nova-session": session} if session else {}
    result: dict[str, object] = {"backend_url": base, "http": {}, "websocket": {}}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{base}/api/health", headers=headers)
            result["http"] = {"status": response.status_code, "authenticated": bool(session), "ok": response.is_success}
    except Exception as error:
        result["http"] = {"status": None, "authenticated": bool(session), "ok": False, "reason": type(error).__name__}

    parsed = urlparse(base)
    ws_base = ("wss" if parsed.scheme == "https" else "ws") + "://" + parsed.netloc + "/ws"
    if session:
        ws_base += "?session=" + session
    try:
        async with connect(ws_base, open_timeout=5, close_timeout=2) as socket:
            ready = json.loads(await asyncio.wait_for(socket.recv(), 5))
            result["websocket"] = {"ok": ready.get("type") == "system.ready", "ready_type": ready.get("type"), "authenticated": bool(session)}
    except Exception as error:
        result["websocket"] = {"ok": False, "authenticated": bool(session), "reason": type(error).__name__}

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
