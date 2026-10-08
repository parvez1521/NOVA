import json
from unittest.mock import AsyncMock

import pytest

from app.computer.executor import TaskExecutor
from app.computer.models import Action,ComputerSettings,Task,ScreenObservation
from app.computer.permissions import ConfirmationRequired
from app.computer.planner import TaskPlanner


@pytest.mark.asyncio
async def test_llm_planner_proposes_structured_action_without_executing(tmp_path):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    provider=AsyncMock();provider.provider_name="ollama";provider.model_name="qwen3:4b-q4_K_M";provider.is_available.return_value=True
    provider.generate_structured.return_value=json.dumps({"decision":"act","steps":["Open editor"],"action":{"tool":"app.open","arguments":{"name":"TextEdit"},"expected_result":"TextEdit active","verification":{}},"message":""})
    router=type("Router",(),{"providers":{"ollama":provider}})()
    decision=await TaskPlanner(executor.registry,router).decide(Task(goal="Prepare my workspace"),ScreenObservation(),ComputerSettings(),evidence=[])
    assert decision.action.tool=="app.open"
    assert provider.generate_structured.call_args.kwargs["max_tokens"]==1400


@pytest.mark.asyncio
async def test_complex_planner_uses_task_aware_router_when_cloud_planning_is_enabled(tmp_path):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    local=AsyncMock();local.provider_name="ollama";local.model_name="qwen3:4b";local.is_available.return_value=True
    stronger=AsyncMock();stronger.provider_name="openrouter";stronger.model_name="free-reasoning";stronger.generate_structured.return_value=json.dumps({"decision":"act","steps":["Open Safari","Search","Verify"],"action":{"tool":"app.open","arguments":{"name":"Safari"},"expected_result":"Safari active","verification":{}},"message":""})
    selection=type("Selection",(),{"provider":stronger})()
    router=type("Router",(),{"providers":{"ollama":local},"select":AsyncMock(return_value=selection),"fallback":AsyncMock(return_value=None)})()
    task=Task(goal="Open Safari, research the latest AI video tools, compare the first three results, and summarize the best one")
    decision=await TaskPlanner(executor.registry,router).decide(task,ScreenObservation(),ComputerSettings(allow_cloud_planning=True),evidence=[])
    assert decision.action and decision.action.tool=="app.open"
    router.select.assert_awaited_once()
    assert task.metadata["planner_provider"]=="openrouter" and task.metadata["planner_model"]=="free-reasoning"


@pytest.mark.asyncio
async def test_invalid_planner_output_rejected(tmp_path):
    provider=AsyncMock();provider.is_available.return_value=True;provider.generate_structured.return_value='{"decision":"act","action":{"tool":"shell.run","arguments":{},"expected_result":"unsafe"}}'
    router=type("Router",(),{"providers":{"ollama":provider}})()
    from app.computer.models import ComputerError
    with pytest.raises(ComputerError):await TaskPlanner(TaskExecutor(tmp_path,ComputerSettings()).registry,router).decide(Task(goal="Do task"),None,ComputerSettings(),evidence=[])


@pytest.mark.asyncio
async def test_permissions_cannot_be_bypassed_in_autonomous_mode(tmp_path):
    settings=ComputerSettings(enabled=True,mode="AUTONOMOUS",simulation=True,filesystem_access=True)
    executor=TaskExecutor(tmp_path,settings)
    task=Task(goal="Delete file",mode="AUTONOMOUS",allowed_scope={"filesystem_roots":[str(tmp_path)]})
    with pytest.raises(ConfirmationRequired):await executor.execute(task,Action(tool="filesystem.delete",arguments={"path":str(tmp_path/"file")},expected_result="deleted"),simulation=True)


@pytest.mark.asyncio
async def test_simulation_never_calls_real_controller(tmp_path):
    settings=ComputerSettings(enabled=True,simulation=True)
    executor=TaskExecutor(tmp_path,settings)
    executor.registry.get("app.open").definition.execute=AsyncMock(side_effect=AssertionError("Real action"))
    result,_=await executor.execute(Task(goal="Open Chrome"),Action(tool="app.open",arguments={"name":"Google Chrome"},expected_result="active"),simulation=True)
    assert result.simulated


def test_strict_schemas_reject_unregistered_keys(tmp_path):
    from app.computer.models import ComputerError
    registry=TaskExecutor(tmp_path,ComputerSettings()).registry
    with pytest.raises(ComputerError):registry.validate("app.open",{"name":"Chrome","shell":"rm -rf /"})


@pytest.mark.asyncio
async def test_recovery_excludes_failed_tool_from_structured_choices(tmp_path):
    provider=AsyncMock();provider.provider_name="ollama";provider.model_name="qwen3:4b-q4_K_M";provider.is_available.return_value=True
    provider.generate_structured.return_value=json.dumps({"decision":"act","action":{"tool":"filesystem.read","arguments":{"path":"~/Desktop/results.txt"},"expected_result":"Content checked"}})
    planner=TaskPlanner(TaskExecutor(tmp_path,ComputerSettings()).registry,type("Router",(),{"providers":{"ollama":provider}})())
    await planner.decide(Task(goal="Save results on Desktop"),None,ComputerSettings(),evidence=[],excluded_tools={"filesystem.write"})
    options=provider.generate_structured.call_args.args[1]["$defs"]["Action"]["anyOf"]
    assert not any(option["properties"]["tool"]["const"] in {"filesystem.write","terminal.run","keyboard.type_text"} for option in options)
    read=next(option for option in options if option["properties"]["tool"]["const"]=="filesystem.read")
    assert read["properties"]["arguments"]["required"]==["path"]


@pytest.mark.parametrize("text",["My OTP is 123456","Type verification code 345678","My PIN: 4321","The CVV is 456"])
def test_one_time_and_payment_codes_never_enter_saved_or_planned_text(text):
    from app.database.privacy import has_secret,safe_text
    assert has_secret(text) and safe_text(text)=="[Sensitive input omitted]"
