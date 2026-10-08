import asyncio
from unittest.mock import AsyncMock

import pytest

from app.computer.executor import TaskExecutor
from app.computer.intent import entity_resolution_state, parse_goal, resolve_application
from app.computer.models import Action, ComputerError, ComputerSettings, ScreenObservation, Task, TaskDecision, TaskStatus, ToolResult, UIElement
from app.computer.runtime import ComputerRuntime
from app.core.config import Settings
from app.database.db import Database


@pytest.fixture
def physical_runtime(tmp_path):
    database = Database(tmp_path / "tasks.db")
    database.initialize()
    runtime = ComputerRuntime(Settings(_env_file=None, database_path=str(tmp_path / "tasks.db")), database, None)
    runtime.settings = ComputerSettings(enabled=True, simulation=False, browser_access=True, accessibility_access=True)
    runtime.executor.settings = runtime.settings
    runtime.executor.permissions.settings = runtime.settings
    runtime.executor.native.watch_escape = AsyncMock()
    runtime.executor.keyboard.release_all = AsyncMock()
    runtime.executor.mouse.release_all = AsyncMock()
    yield runtime
    database.close()


@pytest.mark.parametrize(("text", "app", "bundle"), [
    ("Open Safari", "Safari", "com.apple.Safari"),
    ("Open Chrome", "Google Chrome", "com.google.Chrome"),
    ("Finder kholo aur Downloads folder me ja", "Finder", "com.apple.finder"),
    ("Open WhatsApp", "WhatsApp", "net.whatsapp.WhatsApp"),
])
def test_goal_contract_resolves_explicit_application(text, app, bundle):
    intent = parse_goal(text)
    assert intent.target_app == app
    assert intent.target_bundle_id == bundle
    assert resolve_application(app) == (app, bundle)
    assert intent.ordered_steps[0] == f"Open or focus {app}"


def test_goal_contract_decomposes_safari_search_without_chrome_substitution():
    intent = parse_goal("Safari khol ke Google pe AI tools search kar")
    assert intent.target_app == "Safari"
    assert intent.target_object == "AI tools"
    assert intent.intended_action == "search"
    assert len(intent.ordered_steps) >= 6
    assert "Verify search results exist" in intent.ordered_steps


def test_whatsapp_missing_message_is_explicit_and_risky():
    intent = parse_goal("WhatsApp open karke Thinkernest ko message kar")
    assert intent.target_app == "WhatsApp"
    assert intent.target_object == "Thinkernest"
    assert intent.missing_information == "message_text"
    assert intent.confirmation_requirement == "before final Send"
    assert intent.ordered_steps[-1].startswith("Ask for the missing message")


def test_whatsapp_message_contract_contains_type_confirm_send_verify():
    intent = parse_goal("Open WhatsApp, message Thinkernest saying hello")
    assert intent.missing_information == ""
    assert intent.ordered_steps[-4:] == [
        "Type the requested message", "Ask for confirmation before Send", "Send the message", "Verify the sent state",
    ]


@pytest.mark.asyncio
async def test_follow_up_message_text_updates_goal_contract_without_inventing_content(physical_runtime):
    runtime = physical_runtime
    managed = runtime.manager.create("Open WhatsApp and message Thinkernest", runtime.settings.mode, runtime.settings, AsyncMock())
    managed.task.intent = parse_goal(managed.task.goal)
    await runtime.resume(managed.task.id, "hello")
    assert managed.task.intent.target_object == "Thinkernest"
    assert managed.task.intent.missing_information == ""
    assert managed.task.intent.requested_content == "hello"


@pytest.mark.asyncio
async def test_planner_asks_for_message_after_exact_contact_is_verified(tmp_path):
    executor = TaskExecutor(tmp_path, ComputerSettings())
    provider = AsyncMock()
    provider.provider_name = "ollama"
    provider.model_name = "qwen3:4b-q4_K_M"
    provider.is_available.return_value = True
    router = type("Router", (), {"providers": {"ollama": provider}})()
    from app.computer.planner import TaskPlanner

    task = Task(goal="Open WhatsApp and message Thinkernest")
    task.intent = parse_goal(task.goal)
    decision = await TaskPlanner(executor.registry, router).decide(
        task,
        ScreenObservation(active_app="WhatsApp"),
        ComputerSettings(),
    evidence=[{"tool": "ui.click", "arguments": {}, "verified": True, "result": {}, "observed": {"visible_text": "Thinkernest"}}],
    )
    assert decision.decision == "ask"
    assert "Thinkernest" in decision.message
    provider.generate_structured.assert_not_called()


@pytest.mark.asyncio
async def test_planner_asks_for_disambiguation_on_multiple_entity_matches(tmp_path):
    executor = TaskExecutor(tmp_path, ComputerSettings())
    provider = AsyncMock()
    provider.provider_name = "ollama"
    provider.model_name = "qwen3:4b-q4_K_M"
    provider.is_available.return_value = True
    router = type("Router", (), {"providers": {"ollama": provider}})()
    from app.computer.planner import TaskPlanner

    task = Task(goal="Open WhatsApp and find Thinkernest")
    task.intent = parse_goal(task.goal)
    evidence = [{"tool": "ui.click", "arguments": {}, "verified": True, "result": {"matches": ["Thinkernest work", "Thinkernest personal"]}, "observed": {"visible_text": "Thinkernest"}}]
    assert entity_resolution_state(task.intent, evidence) == "ambiguous"
    decision = await TaskPlanner(executor.registry, router).decide(task, ScreenObservation(active_app="WhatsApp"), ComputerSettings(), evidence=evidence)
    assert decision.decision == "ask"
    assert "multiple" in decision.message.casefold()


def test_executor_rejects_silent_safari_to_chrome_substitution(tmp_path):
    executor = TaskExecutor(tmp_path, ComputerSettings())
    task = Task(goal="Open Safari and search AI tools", intent=parse_goal("Open Safari and search AI tools"))
    with pytest.raises(ComputerError) as error:
        executor.validate_application_target(task, Action(tool="app.open", arguments={"name": "Google Chrome"}, expected_result="active"))
    assert error.value.code == "REQUESTED_APP_MISMATCH"


@pytest.mark.asyncio
async def test_shared_executor_resolves_exact_whatsapp_entity_and_reports_multiple_matches(tmp_path):
    executor = TaskExecutor(tmp_path, ComputerSettings(enabled=True, accessibility_access=True))
    executor.apps.active = AsyncMock(return_value={"active_app": "WhatsApp", "bundle_id": "net.whatsapp.WhatsApp"})
    executor.vision.observe = AsyncMock(return_value=ScreenObservation(active_app="WhatsApp", elements=[
        UIElement(id="ax.1", role="AXStaticText", label="Thinkernest Work"),
        UIElement(id="ax.2", role="AXStaticText", label="Thinkernest Personal"),
    ]))
    result = await executor.find_entity("WhatsApp", "Thinkernest")
    assert result["found"] is True
    assert len(result["matches"]) == 2
    assert {match["id"] for match in result["matches"]} == {"ax.1", "ax.2"}


@pytest.mark.asyncio
async def test_safari_search_runs_as_multi_step_goal_and_does_not_finish_after_open(physical_runtime):
    runtime = physical_runtime
    runtime.executor.vision.observe = AsyncMock(side_effect=[
        ScreenObservation(active_app="Finder", fingerprint="before"),
        ScreenObservation(active_app="Safari", fingerprint="safari"),
        ScreenObservation(active_app="Safari", fingerprint="safari"),
        ScreenObservation(active_app="Safari", fingerprint="safari"),
        ScreenObservation(active_app="Safari", visible_text="Google results for AI tools", fingerprint="results"),
    ])
    runtime.executor.execute = AsyncMock(return_value=(ToolResult(success=True, data={"submitted": True}), {}))
    runtime.executor.verification.verify = AsyncMock(return_value={"success": True, "reason": "Observed"})
    runtime.planner.decide = AsyncMock(side_effect=[
        TaskDecision(decision="act", action=Action(tool="app.open", arguments={"name": "Safari"}, expected_result="Safari active")),
        TaskDecision(decision="act", action=Action(tool="browser.search", arguments={"query": "AI tools", "site": "web", "application": "Safari"}, expected_result="Safari shows search results")),
    ])
    runtime.planner.verify_goal = AsyncMock(return_value={"complete": True, "message": ""})
    events = []

    async def emit(kind, task, payload):
        events.append(kind)

    task, _ = await runtime.run("Open Safari and search AI tools", emit)
    assert task.status == TaskStatus.COMPLETED, task.metadata.get("failure")
    assert runtime.planner.decide.await_count == 2
    assert [entry["tool"] for entry in runtime.last_evidence] == ["app.open", "browser.search"]
    assert "task.action.completed" in events


@pytest.mark.asyncio
async def test_whatsapp_send_completion_requires_confirmation_and_sent_observation(physical_runtime):
    runtime = physical_runtime
    task = Task(goal="Open WhatsApp, message Thinkernest saying hello", intent=parse_goal("Open WhatsApp, message Thinkernest saying hello"))
    runtime.planner.verify_goal = AsyncMock(return_value={"complete": True, "message": ""})
    evidence = [
        {"tool": "app.open", "verified": True, "result": {}, "observed": {"active_app": "WhatsApp", "visible_text": ""}},
        {"tool": "ui.click", "verified": True, "result": {}, "observed": {"active_app": "WhatsApp", "visible_text": "Thinkernest"}},
        {"tool": "ui.type", "arguments": {"text": "hello", "element_id": "composer"}, "verified": True, "result": {}, "observed": {"active_app": "WhatsApp", "visible_text": "Thinkernest hello"}},
        {"tool": "ui.click", "verified": True, "result": {}, "observed": {"active_app": "WhatsApp", "visible_text": "Message sent"}},
    ]
    assert (await runtime.completion_review(task, evidence))["complete"] is False
    task.metadata["final_action_confirmed"] = True
    assert (await runtime.completion_review(task, evidence))["complete"] is True


@pytest.mark.asyncio
async def test_user_stop_cancels_goal_before_next_step(physical_runtime):
    runtime = physical_runtime
    runtime.executor.vision.observe = AsyncMock(return_value=ScreenObservation(active_app="Finder"))
    entered = asyncio.Event()

    async def decide(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    runtime.planner.decide = decide
    runner = asyncio.create_task(runtime.run("Open Safari and search AI tools", AsyncMock(), owner="test"))
    await entered.wait()
    managed = runtime.active_for("test")
    await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, 1)
    assert not runtime.manager.active
