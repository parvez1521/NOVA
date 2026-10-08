import asyncio
from unittest.mock import AsyncMock

import pytest

from app.computer.models import Action, ComputerError, ComputerSettings, ScreenObservation, Task, TaskDecision, TaskStatus, ToolResult, UIElement
from app.computer.runtime import ComputerRuntime
from app.computer.executor import TaskExecutor
from app.computer.scope import ScopeManager
from app.computer.verification import RetryEngine, VerificationEngine
from app.core.config import Settings
from app.database.db import Database
from app.tools.safety import CommandSafetyLayer


@pytest.fixture
def runtime(tmp_path):
    database=Database(tmp_path/"tasks.db");database.initialize()
    result=ComputerRuntime(Settings(_env_file=None,database_path=str(tmp_path/"tasks.db")),database,None)
    result.settings=ComputerSettings(enabled=True,simulation=True)
    result.executor.settings=result.settings;result.executor.permissions.settings=result.settings
    yield result
    database.close()


@pytest.mark.asyncio
async def test_task_loop_plans_each_step_and_persists_simulation_truth(runtime):
    runtime.planner.decide=AsyncMock(side_effect=[TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"TextEdit"},expected_result="active")),TaskDecision(decision="complete")])
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    runtime.executor.native.call=AsyncMock(side_effect=AssertionError("Simulation must not inspect real machine"))
    events=[]
    async def emit(kind,task,payload):events.append((task.status,payload))
    task,summary=await runtime.run("Prepare workspace",emit)
    assert task.status==TaskStatus.COMPLETED and summary.startswith("[SIMULATION]")
    assert len(runtime.planner.decide.call_args_list)==1
    runtime.planner.verify_goal.assert_awaited_once()
    assert runtime.history.get(task.id)["metadata"]["simulation"]
    assert "No real actions" in summary


@pytest.mark.asyncio
async def test_computer_tasks_queue_and_run_serially(runtime):
    first_started=asyncio.Event();release_first=asyncio.Event();second_started=asyncio.Event()
    calls={"first":0,"second":0}
    async def decide(task,*args,**kwargs):
        calls[task.goal]+=1
        if task.goal=="first":
            if calls[task.goal]==1:
                first_started.set();await release_first.wait()
                return TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Finder"},expected_result="active"))
            return TaskDecision(decision="complete")
        else:
            second_started.set()
            return TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Finder"},expected_result="active")) if calls[task.goal]==1 else TaskDecision(decision="complete")
    runtime.planner.decide=decide
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    first=asyncio.create_task(runtime.run("first",AsyncMock(),owner="one"));await first_started.wait()
    second=asyncio.create_task(runtime.run("second",AsyncMock(),owner="two"));await asyncio.sleep(0.02)
    assert not second_started.is_set() and len(runtime.manager.active)==2
    release_first.set();await first;await asyncio.wait_for(second,1)
    assert second_started.is_set() and not runtime.manager.active


@pytest.mark.asyncio
async def test_destructive_confirmation_same_in_simulation_and_real(runtime):
    runtime.planner.decide=AsyncMock(side_effect=[TaskDecision(decision="act",action=Action(tool="filesystem.delete",arguments={"path":"~/Desktop/NOVA-Test"},expected_result="deleted")),TaskDecision(decision="complete")])
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    waiting=asyncio.Event()
    async def emit(kind,task,payload):
        if task.status==TaskStatus.WAITING_FOR_CONFIRMATION:waiting.set()
    run=asyncio.create_task(runtime.run("Delete NOVA-Test on Desktop",emit,owner="one"))
    await asyncio.wait_for(waiting.wait(),1)
    managed=runtime.active_for("one")
    assert managed and not run.done()
    await runtime.confirm(managed.task.id,False)
    with pytest.raises(asyncio.CancelledError):await run
    assert runtime.history.get(managed.task.id)["status"]=="CANCELLED"


@pytest.mark.asyncio
async def test_kill_switch_cancels_pending_planner_immediately(runtime):
    waiting=asyncio.Event()
    async def decide(*args,**kwargs):waiting.set();await asyncio.Event().wait()
    runtime.planner.decide=decide
    run=asyncio.create_task(runtime.run("Research tools",AsyncMock(),owner="one"))
    await waiting.wait();managed=runtime.active_for("one")
    await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(run,1)
    assert not runtime.manager.active


@pytest.mark.asyncio
async def test_pause_prevents_next_action(runtime):
    pending=asyncio.Event();release=asyncio.Event()
    async def decide(*args,**kwargs):
        pending.set();await release.wait()
        return TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Chrome"},expected_result="active"))
    runtime.planner.decide=decide
    runtime.executor.execute=AsyncMock()
    run=asyncio.create_task(runtime.run("Open Chrome",AsyncMock(),owner="one"))
    await pending.wait();managed=runtime.active_for("one");await runtime.pause(managed.task.id);release.set()
    await asyncio.sleep(0.02);runtime.executor.execute.assert_not_called()
    await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await run


def test_scope_resolves_symlinks_and_blocks_credential_stores(tmp_path):
    task=Task(goal="files",allowed_scope={"filesystem_roots":[str(tmp_path)]})
    outside=tmp_path.parent/"external";link=tmp_path/"link";link.symlink_to(outside)
    with pytest.raises(ComputerError):ScopeManager().validate(task,str(link/"private"))
    with pytest.raises(ComputerError):ScopeManager().validate(task,str(tmp_path/".env"))


def test_retry_engine_never_blindly_repeats_consequential_actions():
    retry=RetryEngine()
    assert retry.may_retry(Action(tool="browser.read",arguments={},expected_result="read"),1)
    assert not retry.may_retry(Action(tool="browser.read",arguments={},expected_result="read"),2)
    for tool in ["browser.click","filesystem.delete","filesystem.write","keyboard.type_text"]:
        assert not retry.may_retry(Action(tool=tool,arguments={},expected_result="done"),0)


@pytest.mark.asyncio
async def test_verification_rejects_fake_active_app():
    engine=VerificationEngine()
    result=await engine.verify(Action(tool="app.open",arguments={"name":"Chrome"},expected_result="active"),ToolResult(success=True,data={"name":"Chrome"}),ScreenObservation(active_app="Finder"),ScreenObservation(active_app="Finder"))
    assert not result["success"]


def test_uncertain_coordinates_and_terminal_keyboard_blocked(tmp_path):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    with pytest.raises(ComputerError):executor.validate_target(Action(tool="mouse.click",arguments={"x":50,"y":50},expected_result="clicked"),ScreenObservation())
    with pytest.raises(ComputerError):executor.validate_target(Action(tool="keyboard.type_text",arguments={"text":"rm file"},expected_result="typed"),ScreenObservation(active_app="Terminal"))
    with pytest.raises(ComputerError):executor.validate_target(Action(tool="ui.type",arguments={"text":"rm file"},expected_result="typed"),ScreenObservation(active_app="Terminal",focused_element={"editable":True}))


@pytest.mark.parametrize("command",[["sh","-c","ls"],["rm"],["python"],["osascript"],["curl"],["pwd","&&","rm"]])
def test_terminal_safety_never_shell(command):
    with pytest.raises(ComputerError):CommandSafetyLayer().validate(command)


@pytest.mark.asyncio
async def test_completion_without_action_never_success(runtime):
    runtime.planner.decide=AsyncMock(return_value=TaskDecision(decision="complete"))
    task,summary=await runtime.run("Research three tools",AsyncMock())
    assert task.status==TaskStatus.FAILED and "NO_VERIFIED_ACTION"==task.metadata["error_code"]


@pytest.mark.asyncio
async def test_unobserved_research_is_never_written_even_after_replanning(runtime):
    runtime.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(
        tool="filesystem.write",arguments={"path":"~/Desktop/results.txt","content":"Invented — https://unobserved.example"},expected_result="Saved")))
    runtime.executor.execute=AsyncMock(side_effect=AssertionError("Unverified research must never reach a tool"))
    task,_=await runtime.run("Research tools and write names and URLs to results.txt on Desktop",AsyncMock())
    assert task.status==TaskStatus.FAILED and task.metadata["error_code"]=="UNVERIFIED_RESEARCH"
    assert runtime.planner.decide.await_count==3
    runtime.executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_completion_does_not_accept_youtube_lookalike_domain(runtime):
    runtime.settings.simulation=False
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    result=await runtime.completion_review(Task(goal="Open YouTube"),[
        {"tool":"browser.navigate","arguments":{"url":"https://youtube.com.example"},"observed":{"current_url":"https://youtube.com.example"}}])
    assert not result["complete"]


@pytest.mark.asyncio
async def test_existing_research_file_is_read_and_verified_without_overwrite(runtime,tmp_path):
    path=tmp_path/"results.txt";original="Tool A — https://example.com/tool\n";path.write_text(original)
    runtime.settings.simulation=False
    runtime.executor.native.watch_escape=AsyncMock();runtime.executor.keyboard.release_all=AsyncMock()
    runtime.executor.vision.observe=AsyncMock(return_value=ScreenObservation(current_url="https://example.com/search"))
    def scope_for(task):task.allowed_scope={"filesystem_roots":[str(tmp_path)]}
    runtime.executor.scope.scope_for=scope_for
    runtime.executor.registry.get("browser.read").definition.execute=AsyncMock(return_value={"url":"https://example.com/search","visible_text":"Tool A","links":[{"title":"Tool A","url":"https://example.com/tool"}]})
    runtime.executor.verification.verify=AsyncMock(return_value={"success":True,"reason":"Checked"})
    runtime.planner.decide=AsyncMock(side_effect=[
        TaskDecision(decision="act",action=Action(tool="browser.read",arguments={},expected_result="Read")),
        TaskDecision(decision="act",action=Action(tool="filesystem.write",arguments={"path":str(path),"content":"Different — https://example.com/tool"},expected_result="Saved"))])
    runtime.planner.verify_goal=AsyncMock(side_effect=[{"complete":False,"message":"Check saved results"},{"complete":True,"message":""}])
    task,_=await runtime.run("Write research tool names and URLs to results.txt",AsyncMock())
    assert task.status==TaskStatus.COMPLETED and path.read_text()==original
    assert [item["tool"] for item in runtime.last_evidence]==["browser.read","filesystem.read"]


@pytest.mark.asyncio
async def test_textedit_type_recovers_by_opening_new_document(runtime):
    runtime.settings.simulation=False
    runtime.executor.native.watch_escape=AsyncMock();runtime.executor.keyboard.release_all=AsyncMock()
    runtime.executor.vision.observe=AsyncMock(return_value=ScreenObservation(active_app="TextEdit",focused_element={"editable":True},visible_text="Hello from NOVA"))
    runtime.executor.execute=AsyncMock(side_effect=[ComputerError("TEXT_FIELD_NOT_FOCUSED","focus it"),(ToolResult(success=True,data={}),{}),(ToolResult(success=True,data={}),{})])
    runtime.executor.verification.verify=AsyncMock(return_value={"success":True,"reason":"Checked"})
    runtime.planner.decide=AsyncMock(side_effect=[TaskDecision(decision="act",action=Action(tool="keyboard.type_text",arguments={"text":"Hello from NOVA"},expected_result="typed")),TaskDecision(decision="act",action=Action(tool="keyboard.type_text",arguments={"text":"Hello from NOVA"},expected_result="typed")),TaskDecision(decision="complete")])
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    task,_=await runtime.run("Open TextEdit, create a new document, and type Hello from NOVA",AsyncMock())
    assert task.status==TaskStatus.COMPLETED
    assert runtime.executor.execute.call_args_list[1].args[1].tool=="keyboard.hotkey"


@pytest.mark.asyncio
async def test_compound_screenshot_goal_cannot_complete_after_capture_alone(runtime):
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":False,"message":"The requested file has not been created"})
    result=await runtime.completion_review(Task(goal="Take a screenshot and create a folder on Desktop"),[
        {"tool":"screen.capture","arguments":{},"verified":True,"result":{"visible_text":"Observed screen"}}])
    assert not result["complete"]


@pytest.mark.asyncio
async def test_timeout_pauses_pending_planner_until_continue(runtime):
    import time
    entered=asyncio.Event();paused=asyncio.Event()
    async def decide(*args,**kwargs):entered.set();await asyncio.Event().wait()
    async def emit(kind,task,payload):
        if payload.get("code")=="TASK_TIMEOUT":paused.set()
    runtime.planner.decide=decide
    runner=asyncio.create_task(runtime.run("Research tools",emit,owner="timeout"))
    await entered.wait();managed=runtime.active_for("timeout")
    managed.created_monotonic=time.monotonic()-runtime.settings.task_timeout_seconds-1
    await asyncio.wait_for(paused.wait(),2)
    assert managed.task.status==TaskStatus.PAUSED
    await runtime.resume(managed.task.id)
    assert managed.pause_event.is_set()
    await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await runner


def test_task_history_never_retains_raw_screen_description(runtime):
    task=Task(goal="Take a screenshot",status="COMPLETED")
    runtime.history.save(task,"Verified: Looking at the screen.\nVisible in Editor:\nRaw screen content")
    assert runtime.history.get(task.id)["summary"]=="Verified: Looking at the screen."


def test_dom_viewport_coordinates_cannot_be_used_as_screen_coordinates(tmp_path):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    observation=ScreenObservation(elements=[UIElement(id="dom-1",role="button",source="dom",x=10,y=10,width=100,height=20)])
    with pytest.raises(ComputerError):executor.validate_target(Action(tool="mouse.click",arguments={"x":20,"y":15},expected_result="clicked"),observation)


@pytest.mark.asyncio
async def test_typing_verification_cannot_use_text_from_another_document():
    action=Action(tool="keyboard.type_text",arguments={"text":"Hello from NOVA"},expected_result="Typed")
    result=await VerificationEngine().verify(action,ToolResult(success=True),ScreenObservation(),ScreenObservation(
        visible_text="Other document: Hello from NOVA",focused_element={"editable":True,"label":""}))
    assert not result["success"]


@pytest.mark.asyncio
async def test_app_verification_requires_the_requested_bundle():
    action=Action(tool="app.open",arguments={"name":"Safari"},expected_result="Safari active")
    result=await VerificationEngine().verify(action,ToolResult(success=True),ScreenObservation(),ScreenObservation(
        active_app="Safari",active_bundle_id="com.google.Chrome"))
    assert not result["success"]
    result=await VerificationEngine().verify(action,ToolResult(success=True),ScreenObservation(),ScreenObservation(
        active_app="Safari",active_bundle_id="com.apple.Safari"))
    assert result["success"]


@pytest.mark.asyncio
async def test_topic_nouns_do_not_require_unrequested_screenshot_or_typing(runtime):
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    result=await runtime.completion_review(Task(goal="Research screenshot tools and write notes.txt, a file of type text"),[
        {"tool":"filesystem.write","arguments":{"path":"~/Desktop/notes.txt"},"result":{},"verified":True}])
    assert result["complete"]


@pytest.mark.asyncio
async def test_user_goal_change_reaches_completion_review(runtime):
    runtime.settings.simulation=False
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    feedback="Actually, open Google instead of YouTube."
    result=await runtime.completion_review(Task(goal="Open YouTube"),[
        {"tool":"browser.navigate","arguments":{"url":"https://www.google.com"},"observed":{"current_url":"https://www.google.com"},"verified":True}],feedback=feedback)
    assert result["complete"]
    assert runtime.planner.verify_goal.call_args.kwargs["feedback"]==feedback


@pytest.mark.asyncio
async def test_credential_followup_is_rejected_before_planning(runtime):
    managed=runtime.manager.create("Open Chrome","SEMI_AUTONOMOUS",runtime.settings,AsyncMock())
    with pytest.raises(ComputerError,match="credentials"):
        await runtime.resume(managed.task.id,feedback="My OTP is 123456; type it")
    assert not managed.user_updates and not managed.feedback


@pytest.mark.parametrize("app,focused",[("Google Chrome",{}),("Safari",{}),("Code",{"description":"Terminal"}),("Editor",{"title":"Shell prompt"})])
def test_native_input_cannot_bypass_browser_or_embedded_terminal_tools(tmp_path,app,focused):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    for tool,arguments in [("keyboard.type_text",{"text":"pwd"}),("keyboard.hotkey",{"key":"l","modifiers":["command"]}),("ui.type",{"text":"pwd"})]:
        with pytest.raises(ComputerError) as error:
            executor.validate_target(Action(tool=tool,arguments=arguments,expected_result="Input"),ScreenObservation(active_app=app,focused_element={"editable":True,**focused}))
        assert error.value.code=="KEYBOARD_TARGET_BLOCKED"


def test_system_settings_interaction_is_blocked_even_with_observed_target(tmp_path):
    executor=TaskExecutor(tmp_path,ComputerSettings())
    observation=ScreenObservation(active_app="System Settings",elements=[UIElement(id="button",role="AXButton",x=0,y=0,width=100,height=50)])
    with pytest.raises(ComputerError) as error:executor.validate_target(Action(tool="mouse.click",arguments={"x":20,"y":20},expected_result="Clicked"),observation)
    assert error.value.code=="SECURITY_SETTINGS_BLOCKED"


@pytest.mark.parametrize("marker",["Read content: ","Clipboard: "])
def test_task_history_omits_raw_readout_on_save_and_restart(runtime,marker):
    task=Task(goal="Read requested content",status="COMPLETED")
    runtime.history.save(task,"Verified: Read.\n"+marker+"Private document prose")
    assert runtime.history.get(task.id)["summary"]=="Verified: Read."
    with runtime.db.session() as connection:
        connection.execute("UPDATE tasks SET summary=? WHERE id=?",("Verified: Read.\n"+marker+"Older private prose",task.id))
    runtime.initialize()
    assert runtime.history.get(task.id)["summary"]=="Verified: Read."


@pytest.mark.asyncio
async def test_repeated_stop_waits_for_key_cleanup_before_next_task(runtime):
    runtime.settings.simulation=False
    runtime.executor.native.watch_escape=AsyncMock()
    runtime.executor.vision.observe=AsyncMock(return_value=ScreenObservation())
    planning=asyncio.Event();cleanup=asyncio.Event();release=asyncio.Event()
    async def decide(*args,**kwargs):planning.set();await asyncio.Event().wait()
    async def release_keys():cleanup.set();await release.wait()
    runtime.planner.decide=decide;runtime.executor.keyboard.release_all=AsyncMock(side_effect=release_keys)
    runner=asyncio.create_task(runtime.run("Open Chrome",AsyncMock(),owner="owner"))
    await planning.wait();managed=runtime.active_for("owner")
    await runtime.stop(managed.task.id);await asyncio.wait_for(cleanup.wait(),1)
    await runtime.stop(managed.task.id)
    stopping=asyncio.create_task(runtime.stop_all());await asyncio.sleep(0)
    assert not runner.done() and runtime.active_for("owner") is managed
    with pytest.raises(ComputerError,match="Another desktop task"):
        await runtime.run("Open TextEdit",AsyncMock())
    release.set();await asyncio.wait_for(stopping,1)
    with pytest.raises(asyncio.CancelledError):await runner
    runtime.executor.keyboard.release_all.assert_awaited_once()
    assert not runtime.manager.active and runtime.owner is None and managed.pending_action is None
    assert runtime.history.get(managed.task.id)["status"]=="CANCELLED"


@pytest.mark.asyncio
async def test_stop_during_initial_history_save_releases_task_ownership(runtime):
    entered=asyncio.Event();original_save=runtime._save
    async def save(task,summary=""):
        if not summary:entered.set();await asyncio.Event().wait()
        await original_save(task,summary)
    runtime._save=save
    runner=asyncio.create_task(runtime.run("Open Chrome",AsyncMock(),owner="owner"))
    await entered.wait();managed=runtime.active_for("owner");await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(runner,1)
    assert not runtime.manager.active and runtime.owner is None
    assert runtime.history.get(managed.task.id)["status"]=="CANCELLED"


@pytest.mark.asyncio
async def test_foreground_change_while_waiting_for_approval_does_not_type(runtime):
    runtime.settings.simulation=False;runtime.settings.accessibility_access=True
    runtime.executor.native.watch_escape=AsyncMock();runtime.executor.keyboard.release_all=AsyncMock()
    before=ScreenObservation(active_app="TextEdit",active_pid=11,fingerprint="before",focused_element={"editable":True})
    after=ScreenObservation(active_app="TextEdit",active_pid=22,fingerprint="different-process",focused_element={"editable":True})
    runtime.executor.vision.observe=AsyncMock(return_value=before)
    type_text=AsyncMock();runtime.executor.registry.get("keyboard.type_text").definition.execute=type_text
    runtime.planner.decide=AsyncMock(side_effect=[TaskDecision(decision="act",action=Action(tool="keyboard.type_text",arguments={"text":"Hello"},expected_result="Typed")),TaskDecision(decision="ask",message="Select the intended document")])
    waiting=asyncio.Event();replanned=asyncio.Event()
    async def emit(kind,task,payload):
        if task.status==TaskStatus.WAITING_FOR_CONFIRMATION:waiting.set()
        if task.status==TaskStatus.PAUSED:replanned.set()
    runner=asyncio.create_task(runtime.run("Type Hello",emit,owner="owner"))
    await asyncio.wait_for(waiting.wait(),1);managed=runtime.active_for("owner")
    runtime.executor.vision.observe.return_value=after
    await runtime.confirm(managed.task.id,True);await asyncio.wait_for(replanned.wait(),1)
    type_text.assert_not_awaited()
    assert managed.pending_action is None
    await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await runner


@pytest.mark.asyncio
@pytest.mark.parametrize("tool",["keyboard.type_text","ui.type","browser.type"])
async def test_typing_requires_actual_field_value_not_matching_placeholder(tool):
    executor=AsyncMock();engine=VerificationEngine(executor)
    arguments={"text":"Hello from NOVA"}
    if tool!="keyboard.type_text":arguments["element_id"]="field"
    action=Action(tool=tool,arguments=arguments,expected_result="Typed")
    element=UIElement(id="field",role="text",label="Hello from NOVA",value="",editable=True)
    after=ScreenObservation(elements=[element],focused_element={"label":"Hello from NOVA","value":"","editable":True})
    executor.browser.read_page.return_value={"url":"https://example.com","elements":[element.model_dump()]}
    result=await engine.verify(action,ToolResult(success=True),ScreenObservation(),after)
    assert not result["success"]
    after.elements[0].value="Hello from NOVA";after.focused_element["value"]="Hello from NOVA"
    executor.browser.read_page.return_value["elements"][0]["value"]="Hello from NOVA"
    result=await engine.verify(action,ToolResult(success=True),ScreenObservation(),after)
    assert result["success"]


@pytest.mark.asyncio
async def test_stop_cancels_pending_live_preview(runtime):
    runtime.settings.simulation=False
    managed=runtime.manager.create("Observe screen","SEMI_AUTONOMOUS",runtime.settings,AsyncMock());managed.task.status=TaskStatus.PLANNING
    entered=asyncio.Event();cleaned=asyncio.Event()
    async def preview(settings):
        entered.set()
        try:await asyncio.Event().wait()
        finally:cleaned.set()
    runtime.executor.vision.preview=preview
    task=asyncio.create_task(runtime.preview(managed.task.id));await entered.wait();await runtime.stop(managed.task.id)
    with pytest.raises(asyncio.CancelledError):await task
    assert cleaned.is_set() and not runtime.preview_runners


@pytest.mark.asyncio
async def test_rich_task_timeline_uses_same_id_without_raw_result_data(runtime):
    runtime.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Chrome"},expected_result="Opened")))
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    events=[]
    async def emit(kind,task,payload):events.append((kind,task.id,payload))
    task,_=await runtime.run("Open Chrome",emit)
    kinds={kind for kind,_,_ in events}
    assert {"task.started","task.planning","task.observing","task.action.started","task.action.completed","task.verification.started","task.verification.completed","task.completed"}<=kinds
    assert all(identifier==task.id for _,identifier,_ in events)
    assert all("raw_audio" not in payload and "screenshot_path" not in payload for _,_,payload in events)
