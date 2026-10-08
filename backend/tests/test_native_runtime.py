from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app


def test_packaged_session_blocks_untrusted_http_and_websocket(tmp_path):
    config=Settings(_env_file=None,database_path=str(tmp_path/"native.db"),native_session_token="test-session",native_app_enabled=True,cors_origins="tauri://localhost")
    with TestClient(create_app(config)) as client:
        assert client.get("/api/health/live").status_code==401
        assert client.get("/api/health/live",headers={"x-nova-session":"test-session"}).status_code==200
        response=client.options("/api/health",headers={"origin":"tauri://localhost","access-control-request-method":"GET","access-control-request-headers":"x-nova-session"})
        assert response.status_code==200 and response.headers["access-control-allow-origin"]=="tauri://localhost"
        assert client.post("/api/runtime/stop",headers={"x-nova-session":"test-session"}).json()=={"stopped":True}
        from starlette.websockets import WebSocketDisconnect
        import pytest
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):pass
        with client.websocket_connect("/ws?session=test-session") as ws:
            assert ws.receive_json()["type"]=="system.ready"
        assert "test-session" not in str(config.public_dict())


def test_native_data_root_is_explicit_and_development_is_unchanged(tmp_path):
    assert Settings(_env_file=None,native_data_root=str(tmp_path)).project_root==tmp_path
    assert Settings(_env_file=None).project_root.name=="NOVA"


def test_native_restart_marks_unfinished_tasks_interrupted_without_execution(tmp_path):
    from app.computer.models import Task
    config=Settings(_env_file=None,database_path=str(tmp_path/"native.db"),native_app_enabled=True)
    app=create_app(config)
    with TestClient(app):
        task=Task(goal="Delete a file",status="WAITING_FOR_CONFIRMATION")
        app.state.computer.history.save(task)
    with TestClient(create_app(config)) as client:
        history=client.get(f"/api/computer/tasks/{task.id}").json()
        assert history["status"]=="INTERRUPTED" and "no actions resumed" in history["summary"]


def test_retry_reuses_goal_but_not_task_id_or_approval(tmp_path):
    import asyncio
    from app.computer.models import Task
    config=Settings(_env_file=None,database_path=str(tmp_path/"native.db"))
    app=create_app(config)
    with TestClient(app):
        task=Task(goal="Delete a test file",status="CANCELLED")
        app.state.computer.history.save(task)
        assert asyncio.run(app.state.computer.retry_goal("Nova, retry that task"))==task.goal
        assert not app.state.computer.manager.active
