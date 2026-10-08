"""Deterministic, no-network benchmark for NOVA's agent boundaries.

The fixtures are synthetic and deliberately do not exercise real applications,
accounts, microphones, or cloud providers. The suite checks the existing
intent contract, multilingual wake normalization, safety allowlist, queue
serialization, and free-only capability routing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


INTENT_CASES = [
    ("computer_open", "Open Safari", "open_application", "Safari"),
    ("computer_open", "Launch Google Chrome", "open_application", "Google Chrome"),
    ("computer_open", "Hey NOVA, open Finder", "open_application", "Finder"),
    ("computer_open", "Open Terminal", "open_application", "Terminal"),
    ("computer_open", "Focus System Settings", "open_application", "System Settings"),
    ("computer_search", "Search for local-first desktop agents", "search", "Google Chrome"),
    ("computer_search", "Look up Python asyncio", "search", "Google Chrome"),
    ("computer_search", "YouTube par ambient music search karo", "search", "Google Chrome"),
    ("computer_search", "Search for macOS accessibility", "search", "Google Chrome"),
    ("files", "List the files in Downloads", "general_computer_goal", ""),
    ("files", "Create a folder called NOVA Test", "general_computer_goal", ""),
    ("files", "Move the report to Desktop", "general_computer_goal", ""),
    ("files", "Read the notes file", "general_computer_goal", ""),
    ("messaging", "Find Priya on WhatsApp", "find_contact", "WhatsApp"),
    ("messaging", "Message Alex on WhatsApp saying hello", "send_message", "WhatsApp"),
    ("messaging", "WhatsApp par Rahul ko message likho", "send_message", "WhatsApp"),
    ("messaging", "Open WhatsApp", "find_contact", "WhatsApp"),
    ("connectors", "Search my Gmail inbox", "general_computer_goal", ""),
    ("connectors", "Read the latest Gmail message", "general_computer_goal", ""),
    ("connectors", "Show my Notion tasks", "general_computer_goal", ""),
    ("connectors", "Check my GitHub pull requests", "general_computer_goal", ""),
    ("calendar", "Open my calendar", "general_computer_goal", ""),
    ("calendar", "Find the next meeting", "general_computer_goal", ""),
    ("multilingual", "NOVA, Safari kholo", "open_application", "Safari"),
    ("multilingual", "NOVA, Chrome kholo", "open_application", "Google Chrome"),
    ("multilingual", "YouTube par NOVA search karo ambient music", "search", "Google Chrome"),
    ("multilingual", "WhatsApp par Neha ko message bhejo saying hi", "send_message", "WhatsApp"),
    ("general", "Prepare my workspace", "general_computer_goal", ""),
    ("general", "Take a screenshot of the current state", "general_computer_goal", ""),
    ("general", "Organize the downloaded files", "general_computer_goal", ""),
    ("general", "Check what is visible on screen", "general_computer_goal", ""),
    ("app_targeting", "Search AI tools in Safari", "search", "Safari"),
    ("app_targeting", "Search YouTube for AI news in Chrome", "search", "Google Chrome"),
    ("missing_info", "Open WhatsApp and message Thinkernest", "send_message", "WhatsApp"),
    ("risk", "Prepare a message for Thinkernest on WhatsApp", "send_message", "WhatsApp"),
    ("control", "Pause", "pause", ""),
    ("control", "Continue", "resume", ""),
    ("control", "Stop", "stop", ""),
    ("connectors", "Check GitHub issues", "general_computer_goal", ""),
    ("connectors", "Search Notion for content ideas", "search", "Google Chrome"),
]


class BenchmarkProvider:
    def __init__(self, name: str, model: str, *, local: bool, pricing: str, capabilities: dict[str, Any]):
        self.provider_name = name
        self.model_name = model
        self.local = local
        self.pricing = pricing
        self.capabilities = capabilities

    async def is_available(self) -> bool:
        return True

    async def health_check(self):
        from app.llm.base import ProviderHealth

        return ProviderHealth(self.provider_name, self.model_name, True, True, True, True)

    async def discover_models(self):
        from app.llm.models import ModelCapabilities, ModelDescriptor

        return [ModelDescriptor(
            provider=self.provider_name,
            model=self.model_name,
            capabilities=ModelCapabilities(text=True, streaming=True, **self.capabilities),
            availability="AVAILABLE",
            pricing_state=self.pricing,
            local=self.local,
        )]

    async def generate(self, messages: Sequence[dict[str, str]], **_: Any) -> str:
        return "benchmark"

    async def generate_stream(self, messages: Sequence[dict[str, str]], **_: Any) -> AsyncIterator[str]:
        yield "benchmark"


async def run_queue_check() -> bool:
    from app.computer.task_manager import TaskManager
    from app.computer.models import AgentMode

    manager = TaskManager()
    settings = SimpleNamespace(task_timeout_seconds=30)
    emit = lambda *_: asyncio.sleep(0)
    first = manager.create("first", AgentMode.SEMI_AUTONOMOUS, settings, emit, owner="one")
    second = manager.create("second", AgentMode.SEMI_AUTONOMOUS, settings, emit, owner="two")
    await manager.acquire(first)
    waiter = asyncio.create_task(manager.acquire(second))
    await asyncio.sleep(0)
    blocked = not waiter.done()
    await manager.release(first)
    await asyncio.wait_for(waiter, 1)
    await manager.release(second)
    return blocked


async def run_routing_check() -> dict[str, Any]:
    from app.core.config import Settings
    from app.llm.errors import NoLLMProviderError
    from app.llm.router import ProviderRouter

    local = BenchmarkProvider(
        "ollama", "local-agent", local=True, pricing="LOCAL_FREE",
        capabilities={"reasoning": True, "tools": True, "structured_output": True, "computer_use": True},
    )
    free_text = BenchmarkProvider(
        "openrouter", "free-text", local=False, pricing="FREE",
        capabilities={"reasoning": True},
    )
    paid = BenchmarkProvider(
        "gemini", "paid-model", local=False, pricing="PAID",
        capabilities={"reasoning": True, "tools": True, "structured_output": True},
    )
    settings = Settings(_env_file=None, model_routing_mode="FREE_ONLY", free_only=True, zero_budget_mode=True)
    router = ProviderRouter(settings, providers={"ollama": local, "openrouter": free_text, "gemini": paid})
    computer = await router.select(request="Open Safari")
    reasoning = await router.select(request="Explain the architecture trade-offs")
    paid_blocked = False
    try:
        paid_only = ProviderRouter(settings, providers={"gemini": paid})
        await paid_only.select(request="Answer plainly")
    except NoLLMProviderError:
        paid_blocked = True
    return {
        "computer_model": computer.descriptor.model if computer.descriptor else "",
        "reasoning_model": reasoning.descriptor.model if reasoning.descriptor else "",
        "paid_model_blocked": paid_blocked,
        "single_model_per_request": computer.descriptor is not None and reasoning.descriptor is not None,
    }


def run_static_checks() -> tuple[list[str], dict[str, int]]:
    from app.computer.intent import parse_goal
    from app.computer.runtime import ComputerRuntime
    from app.tools.safety import CommandSafetyLayer
    from app.voice.stt.whisper import is_wake_only_transcript, normalize_spoken_command

    failures: list[str] = []
    categories: dict[str, int] = {}
    for index, (category, text, expected_action, expected_app) in enumerate(INTENT_CASES, 1):
        intent = parse_goal(text)
        # Connector/calendar goals are intentionally delegated to the planner's
        # registered connector tools; the deterministic parser only needs to
        # admit them as computer tasks.
        if category == "control":
            ok = ComputerRuntime.control(text) == expected_action
        elif category in {"connectors", "calendar"}:
            ok = ComputerRuntime.__new__(ComputerRuntime).likely_task(text)
        else:
            ok = intent.intended_action == expected_action and intent.target_app == expected_app
        if category == "messaging" and expected_action == "send_message":
            ok = ok and (bool(intent.requested_content) or intent.missing_information == "message_text")
        if not ok:
            failures.append(f"intent-{index}")
        categories[category] = categories.get(category, 0) + 1

    if normalize_spoken_command("Hey NOVA, open Safari!") != "Open Safari":
        failures.append("voice-normalization")
    if not is_wake_only_transcript("Hey NOVA"):
        failures.append("wake-only")

    safety = CommandSafetyLayer()
    for command in ("pwd", "date", "whoami", "uname", "sw_vers"):
        try:
            safety.validate([command])
        except Exception:
            failures.append(f"safe-command-{command}")
    for command in ("rm -rf /", "python", "curl", "osascript"):
        try:
            safety.validate([command])
            failures.append(f"blocked-command-{command}")
        except Exception:
            categories["safety"] = categories.get("safety", 0) + 1
    return failures, categories


async def main() -> int:
    started = time.perf_counter()
    failures, categories = run_static_checks()
    queue_ok = await run_queue_check()
    if not queue_ok:
        failures.append("task-queue")
    routing = await run_routing_check()
    if not routing["paid_model_blocked"] or not routing["single_model_per_request"]:
        failures.append("routing-policy")
    result = {
        "suite": "agent_benchmark",
        "status": "PASS" if not failures else "FAIL",
        "intent_cases": len(INTENT_CASES),
        "categories": categories,
        "queue_serialization": queue_ok,
        "routing": routing,
        "failed_checks": failures,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "network": False,
        "raw_fixture_content_saved": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    raise SystemExit(asyncio.run(main()))
