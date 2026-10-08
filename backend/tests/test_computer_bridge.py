import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.computer.models import Action, ComputerError, TaskDecision
from app.core.config import Settings
from app.llm.router import ProviderRouter
from app.main import create_app
from app.voice.manager import VoiceManager
from tests.test_voice import MockTTS,MockSTT,wav
from tests.test_websocket_streaming import WebSocketFakeProvider


def task_app(tmp_path):
    config=Settings(_env_file=None,database_path=str(tmp_path/"phase5.db"),computer_use_enabled=True,computer_use_simulation=True,openrouter_enabled=False)
    provider=WebSocketFakeProvider();app=create_app(config,router=ProviderRouter(config,providers={"ollama":provider}),voice=VoiceManager(config,stt=MockSTT(),tts=MockTTS()))
    runtime=app.state.computer
    runtime.permissions=AsyncMock(return_value={"accessibility":False,"screen_recording":False,"automation":"not_required"})
    runtime.planner.decide=AsyncMock(side_effect=[TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Chrome"},expected_result="active")),TaskDecision(decision="complete")])
    runtime.planner.verify_goal=AsyncMock(return_value={"complete":True,"message":""})
    return app


def test_computer_websocket_simulation_preserves_history_and_original_events(tmp_path):
    app=task_app(tmp_path)
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        conversation=ws.receive_json()["conversation_id"]
        ws.send_json({"type":"agent.message","content":"Open Chrome","speak":True})
        events=[]
        while not events or events[-1]["type"]!="agent.completed":events.append(ws.receive_json())
        assert any(event["type"]=="task.updated" for event in events)
        assert events[-1]["content"].startswith("[SIMULATION]")
        assert any(event["type"]=="tts.completed" for event in events)
        assert client.get(f"/api/conversations/{conversation}/messages").json()[-1]["content"]==events[-1]["content"]
        assert client.get("/api/computer/tasks").json()[0]["status"]=="COMPLETED"
        assert client.put("/api/computer",json={"enabled":True,"max_task_steps":0}).status_code==422


def test_voice_uses_identical_task_runtime(tmp_path):
    import base64
    app=task_app(tmp_path)
    async def transcribe(audio):
        result=await MockSTT().transcribe(audio);result.text="Nova, open Chrome.";return result
    app.state.voice_manager.stt_override.transcribe=transcribe
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        conversation=ws.receive_json()["conversation_id"]
        identifier="33333333-3333-4333-8333-333333333333"
        ws.send_json({"type":"voice.start","request_id":identifier});ws.receive_json()
        ws.send_json({"type":"voice.audio","request_id":identifier,"audio":base64.b64encode(wav()).decode()})
        events=[]
        while not events or events[-1]["type"]!="agent.completed":events.append(ws.receive_json())
        assert any(event["type"]=="task.updated" for event in events)
        messages=client.get(f"/api/conversations/{conversation}/messages").json()
        assert messages[0]["input_type"]==messages[1]["input_type"]=="voice"


def test_voice_and_text_commands_have_equivalent_normalized_goal_and_intent(tmp_path):
    import base64
    app=task_app(tmp_path)
    app.state.computer.planner.decide=AsyncMock(side_effect=[
        TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Safari"},expected_result="active")),
        TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Safari"},expected_result="active")),
    ])
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type":"agent.message","content":"Open Safari"})
        text_events=[]
        while not text_events or text_events[-1]["type"]!="agent.completed":text_events.append(ws.receive_json())
        text_task=client.get("/api/computer/tasks").json()[0]
        app.state.voice_manager.stt_override.transcribe=AsyncMock(side_effect=lambda audio: MockSTT().transcribe(audio))
        async def voice_transcribe(audio):
            result=await MockSTT().transcribe(audio)
            result.text=result.raw_transcript=result.normalized_transcript="Hey Nova, open Safari"
            return result
        app.state.voice_manager.stt_override.transcribe=voice_transcribe
        request_id="33333333-3333-4333-8333-333333333333"
        ws.send_json({"type":"voice.start","request_id":request_id});ws.receive_json()
        ws.send_json({"type":"voice.audio","request_id":request_id,"audio":base64.b64encode(wav()).decode()})
        voice_events=[]
        while not voice_events or voice_events[-1]["type"]!="agent.completed":voice_events.append(ws.receive_json())
        voice_task=client.get("/api/computer/tasks").json()[0]
        transcript=next(event for event in voice_events if event["type"]=="voice.transcript")
        assert transcript["text"]=="Open Safari"
        assert transcript["wake_phrase_removed"] is True
        assert text_task["goal"]==voice_task["goal"]=="Open Safari"
        assert text_task["metadata"]["intent"]["target_app"]==voice_task["metadata"]["intent"]["target_app"]=="Safari"
        assert text_task["id"]!=voice_task["id"]
        assert next(event for event in voice_events if event.get("task_id")) ["task_id"]==voice_task["id"]


def test_wake_only_transcript_creates_no_task_and_emits_no_agent_request(tmp_path):
    import base64
    app=task_app(tmp_path)
    class WakeOnlySTT(MockSTT):
        async def transcribe(self,audio):
            result=await super().transcribe(audio)
            result.text=result.raw_transcript=result.normalized_transcript="Hey Nova"
            return result
    app.state.voice_manager.stt_override=WakeOnlySTT()
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();request_id="33333333-3333-4333-8333-333333333333"
        ws.send_json({"type":"voice.start","request_id":request_id});ws.receive_json()
        ws.send_json({"type":"voice.audio","request_id":request_id,"audio":base64.b64encode(wav()).decode()})
        events=[]
        while not events or events[-1]["type"]!="voice.wake_only":events.append(ws.receive_json())
        assert any(event["type"]=="voice.transcript" and event["text"]=="" for event in events)
        assert not any(event["type"].startswith("task.") for event in events)
        assert client.get("/api/computer/tasks").json()==[]


def test_duplicate_final_voice_audio_creates_one_task(tmp_path):
    import base64
    app=task_app(tmp_path)
    class CommandSTT(MockSTT):
        async def transcribe(self,audio):
            result=await super().transcribe(audio)
            result.text=result.raw_transcript=result.normalized_transcript="Open Chrome"
            return result
    app.state.voice_manager.stt_override=CommandSTT()
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();request_id="33333333-3333-4333-8333-333333333333";payload={"type":"voice.audio","request_id":request_id,"audio":base64.b64encode(wav()).decode()}
        ws.send_json({"type":"voice.start","request_id":request_id});ws.receive_json();ws.send_json(payload);ws.send_json(payload)
        events=[]
        while not events or events[-1]["type"]!="agent.completed":events.append(ws.receive_json())
        assert len(client.get("/api/computer/tasks").json())==1
        assert any(event.get("code")=="INVALID_VOICE_STATE" for event in events)


def test_failed_task_retains_sanitized_pipeline_trace_and_exact_error(tmp_path):
    app=task_app(tmp_path)
    app.state.computer.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Safari"},expected_result="active")))
    app.state.computer.executor.execute=AsyncMock(side_effect=ComputerError("APP_NOT_INSTALLED","Safari was not found."))
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();ws.send_json({"type":"agent.message","content":"Open Safari"})
        events=[]
        while not events or events[-1]["type"]!="agent.completed":events.append(ws.receive_json())
        task=client.get("/api/computer/tasks").json()[0];trace=task["metadata"]["pipeline_trace"]
        assert task["status"]=="FAILED" and task["metadata"]["error_code"]=="APP_NOT_INSTALLED"
        assert trace["task_id"]==task["id"] and trace["normalized_command"]=="Open Safari"
        assert trace["requested_application"]=="Safari" and trace["selected_tool"]=="app.open"
        assert trace["final_error"]=="Safari was not found."
        assert events[-1]["task_id"]==task["id"]


@pytest.mark.asyncio
async def test_explicit_safari_browser_action_inherits_application_and_uses_native_path():
    from pathlib import Path
    from app.computer.browser import BrowserProvider

    native=AsyncMock()
    native.call.side_effect=[
        {"active_app":"Safari","bundle_id":"com.apple.Safari","pid":1234},
        {"pressed":True},
        {"typed":True},
    ]
    browser=BrowserProvider(native,Path("/tmp"))
    result=await browser.type("com.apple.Safari.addressbar","AI tools","Safari")
    assert result["application"]=="Safari" and result["native"] and result["typed"]
    assert native.call.await_args_list[1].args[0]=="keyboard.hotkey"
    assert native.call.await_args_list[2].args[0]=="keyboard.type_text"


def test_task_history_survives_db_reopen_and_approval_rejects_string_bools(tmp_path):
    app=task_app(tmp_path)
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();ws.send_json({"type":"agent.message","content":"Open Chrome"})
        while (event:=ws.receive_json())["type"]!="agent.completed":pass
        saved=client.get("/api/computer/tasks").json()[0]
        assert client.post("/api/computer/tasks/33333333-3333-4333-8333-333333333333/confirm",json={"confirmed":"false"}).status_code==422
    with TestClient(task_app(tmp_path)) as client:
        restored=client.get(f"/api/computer/tasks/{saved['id']}").json()
        assert restored["status"]=="COMPLETED" and restored["summary"]==saved["summary"]


def test_typed_task_controls_persist_and_continue_does_not_approve(tmp_path):
    app=task_app(tmp_path)
    app.state.computer.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(
        tool="filesystem.delete",arguments={"path":"~/Desktop/NOVA-Test"},expected_result="Deleted")))
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        conversation=ws.receive_json()["conversation_id"]
        ws.send_json({"type":"agent.message","content":"Delete NOVA-Test on Desktop"})
        while (event:=ws.receive_json()).get("task_status")!="WAITING_FOR_CONFIRMATION":pass
        identifier=event["task_id"]
        for command in ["pause","continue"]:
            ws.send_json({"type":"agent.message","content":command})
            while (event:=ws.receive_json()).get("model")!="task-control":pass
        assert app.state.computer.manager.get(identifier).task.status=="WAITING_FOR_CONFIRMATION"
        messages=client.get(f"/api/conversations/{conversation}/messages").json()
        assert [row["content"] for row in messages if row["role"]=="user"]==["Delete NOVA-Test on Desktop","pause","continue"]
        assert len([row for row in messages if row["role"]=="assistant"])==2
        ws.send_json({"type":"agent.message","content":"no"})
        while (event:=ws.receive_json()).get("model")!="task-control":pass


def test_clear_everything_waits_for_task_stop_before_clearing_history(tmp_path):
    app=task_app(tmp_path)
    app.state.computer.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(
        tool="filesystem.delete",arguments={"path":"~/Desktop/NOVA-Test"},expected_result="Deleted")))
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();ws.send_json({"type":"agent.message","content":"Delete NOVA-Test on Desktop"})
        while (event:=ws.receive_json()).get("task_status")!="WAITING_FOR_CONFIRMATION":pass
        response=client.post("/api/data/clear",json={"scope":"everything","confirmed":True})
        assert response.status_code==200
        assert not app.state.computer.manager.active
        assert client.get("/api/computer/tasks").json()==[]


def test_task_stop_followed_by_agent_stop_finishes_cleanup_and_accepts_new_task(tmp_path):
    app=task_app(tmp_path)
    app.state.computer.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(
        tool="filesystem.delete",arguments={"path":"~/Desktop/NOVA-Test"},expected_result="Deleted")))
    with TestClient(app) as client,client.websocket_connect("/ws") as ws:
        ws.receive_json();identifier="11111111-1111-4111-8111-111111111111"
        ws.send_json({"type":"agent.message","request_id":identifier,"content":"Delete NOVA-Test on Desktop"})
        while (event:=ws.receive_json()).get("task_status")!="WAITING_FOR_CONFIRMATION":pass
        task_id=event["task_id"]
        ws.send_json({"type":"task.stop"});ws.send_json({"type":"agent.stop","request_id":identifier});ws.send_json({"type":"ping"})
        while (event:=ws.receive_json())["type"]!="system.pong":pass
        assert not app.state.computer.manager.active
        assert client.get(f"/api/computer/tasks/{task_id}").json()["status"]=="CANCELLED"
        app.state.computer.planner.decide=AsyncMock(return_value=TaskDecision(decision="act",action=Action(tool="app.open",arguments={"name":"Chrome"},expected_result="Opened")))
        ws.send_json({"type":"agent.message","content":"Open Chrome"})
        while (event:=ws.receive_json())["type"]!="agent.completed":
            assert event["type"]!="system.error"
        assert event["content"].startswith("[SIMULATION]")
