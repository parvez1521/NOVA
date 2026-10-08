"""NOVA FastAPI application and Phase 2 streaming bridge."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import platform
import re
import shutil
import secrets
import time
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent.agent import Agent, AgentStreamEvent
from app.agent.context import ConversationContext
from app.agent.conversations import ConversationManager
from app.api.persistence import persistence_routes
from app.api.computer import computer_routes
from app.api.connectors import connector_routes
from app.api.mcp import mcp_routes
from app.api.models import model_routes
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database.db import DatabaseUnavailable
from app.database.models import new_id
from app.database.privacy import has_secret, safe_text
from app.llm.errors import LLMError
from app.llm.router import ProviderRouter
from app.memory.manager import MemoryResult, MemorySession
from app.memory.retriever import MemoryRetriever
from app.voice.base import STT_PROFILE_MODELS, VoiceError, VoiceSettings
from app.voice.manager import SpeechQueue, VoiceManager
from app.voice.stt.whisper import is_wake_only_transcript, normalize_spoken_command, validate_audio
from app.computer.runtime import ComputerRuntime
from app.computer.models import ComputerError, TaskStatus
from app.connectors.manager import ConnectorManager
from app.connectors.google import GoogleConnector
from app.connectors.github import GitHubConnector
from app.connectors.notion import NotionConnector
from app.connectors.credentials import MacOSKeychainStore

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    service: str
    version: str
    phase: int
    status: str = Field(pattern="^(ok|degraded)$")
    environment: str
    timestamp: datetime
    system: dict[str, str]
    llm: dict[str, Any]
    capabilities: dict[str, Any]


@dataclass(slots=True)
class ActiveRequest:
    request_id: str
    started_at: float
    status: str = "running"
    provider: str | None = None
    model: str | None = None
    task: asyncio.Task[None] | None = None
    voice_options: VoiceSettings | None = None
    conversation_id: str = ""
    user_message_id: str | None = None
    input_type: str = "text"
    generation_completed: bool = False
    memory_command: bool = False
    database_latency_ms: float = 0
    task_id: str | None = None
    speech: SpeechQueue | None = None
    transcript: str = ""
    normalized_command: str = ""
    pipeline_trace: dict[str, Any] = field(default_factory=dict)


def _event(event_type: str, request_id: str | None = None, **payload: Any) -> dict[str, Any]:
    """Build a structured event while keeping a small Phase 1 data envelope."""

    event: dict[str, Any] = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    if request_id:
        event["request_id"] = request_id
    event.update(payload)
    return event


def _valid_request_id(value: object) -> str:
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


def _trace_text(value: object, limit: int = 1600) -> str:
    text = safe_text(str(value))
    return text[:limit]


def _trace_observation(observation: Any) -> dict[str, Any]:
    if not observation:
        return {}
    return {
        "active_app": _trace_text(getattr(observation, "active_app", ""), 200),
        "active_bundle_id": _trace_text(getattr(observation, "active_bundle_id", ""), 200),
        "window_title": _trace_text(getattr(observation, "window_title", ""), 300),
        "source": _trace_text(getattr(observation, "source", ""), 80),
        "blocked_reason": _trace_text(getattr(observation, "blocked_reason", "") or "", 300),
        "fingerprint": _trace_text(getattr(observation, "fingerprint", ""), 200),
        "element_count": len(getattr(observation, "elements", []) or []),
        "focused_editable": bool((getattr(observation, "focused_element", None) or {}).get("editable")),
        "focused_secure": bool((getattr(observation, "focused_element", None) or {}).get("secure")),
    }


def _trace_action(action: Any) -> dict[str, Any]:
    if not action:
        return {}
    arguments = {}
    for key, value in (getattr(action, "arguments", {}) or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            arguments[key] = _trace_text(value, 500) if isinstance(value, str) else value
        else:
            arguments[key] = _trace_text(value, 500)
    return {
        "tool": _trace_text(getattr(action, "tool", ""), 100),
        "arguments": arguments,
        "expected_result": _trace_text(getattr(action, "expected_result", ""), 400),
    }


def _trace_intent(intent: Any) -> dict[str, Any]:
    if not intent:
        return {}
    result = intent.model_dump() if hasattr(intent, "model_dump") else dict(intent)
    return {key: (_trace_text(value, 1600) if isinstance(value, str) else value) for key, value in result.items()}


async def _health_payload(settings: Settings, router: ProviderRouter, voice: VoiceManager) -> HealthResponse:
    llm_snapshot = await router.health_snapshot()
    ollama = llm_snapshot.get("ollama", {})
    openrouter = llm_snapshot.get("openrouter", {})
    return HealthResponse(
        service="nova-backend",
        version=settings.app_version,
        phase=5,
        status="ok",
        environment=settings.nova_env,
        timestamp=datetime.now(timezone.utc),
        system={"os": platform.system().lower(), "architecture": platform.machine()},
        llm=llm_snapshot,
        capabilities={
            "phase": 5,
            "llm": {
                "configured_provider": settings.llm_provider,
                "ollama_enabled": settings.ollama_enabled,
                "ollama_model": settings.ollama_model,
                "ollama_installed": shutil.which("ollama") is not None,
                "ollama_reachable": ollama.get("server_reachable") if isinstance(ollama, dict) else None,
                "openrouter_enabled": settings.openrouter_enabled,
                "openrouter_configured": settings.openrouter_configured,
                "routing_policy": settings.llm_routing_policy,
            },
            "voice": {
                "enabled": settings.voice_enabled,
                "native_tts": shutil.which("say") is not None,
                **await voice.status(),
            },
            "privacy": {
                "wake_word_enabled": settings.wake_word_enabled,
                "screen_context_enabled": settings.screen_context_enabled,
                "memory_enabled": settings.memory_enabled,
            },
            "openrouter": {
                "configured": openrouter.get("configured") if isinstance(openrouter, dict) else False,
            },
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    try:
        await asyncio.to_thread(app.state.persistence.initialize)
        app.state.computer.initialize()
    except DatabaseUnavailable:
        logger.warning("Local persistence unavailable; conversation remains usable")
    try:
        yield
    finally:
        for provider in app.state.wake_registry.providers.values():await provider.stop()
        await app.state.computer.stop_all()
        await app.state.connectors.close()
        await asyncio.to_thread(app.state.persistence.db.close)


def create_app(settings: Settings | None = None, router: ProviderRouter | None = None, voice: VoiceManager | None = None, connectors: ConnectorManager | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    provider_router = router or ProviderRouter(runtime_settings)
    voice_manager = voice or VoiceManager(runtime_settings)
    persistence = ConversationManager(runtime_settings)
    if connectors is None:
        google = GoogleConnector(
            # Secret values are consumed only by the backend connector.
            credentials=MacOSKeychainStore(),
            client_id=runtime_settings.google_client_id,
            client_secret=runtime_settings.google_client_secret.get_secret_value() if runtime_settings.google_client_secret else "",
            redirect_uri=runtime_settings.google_redirect_uri,
            scopes=runtime_settings.google_oauth_scopes,
        )
        github = GitHubConnector(
            credentials=MacOSKeychainStore(),
            client_id=runtime_settings.github_client_id,
            client_secret=runtime_settings.github_client_secret.get_secret_value() if runtime_settings.github_client_secret else "",
            redirect_uri=runtime_settings.github_redirect_uri,
            scopes=runtime_settings.github_oauth_scopes,
        )
        notion = NotionConnector(
            credentials=MacOSKeychainStore(),
            token=runtime_settings.notion_api_token.get_secret_value() if runtime_settings.notion_api_token else "",
            notion_version=runtime_settings.notion_version,
        )
        connector_manager = ConnectorManager(enabled=runtime_settings.connectors_enabled, mcp_enabled=runtime_settings.mcp_enabled, connectors=[google, github, notion])
    else:
        connector_manager = connectors
    computer = ComputerRuntime(runtime_settings, persistence.db, provider_router, connector_manager)
    app = FastAPI(
        title="NOVA Backend",
        version=runtime_settings.app_version,
        description="Local-first orchestration service for NOVA",
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.provider_router = provider_router
    app.state.voice_manager = voice_manager
    app.state.persistence = persistence
    app.state.computer = computer
    app.state.connectors = connector_manager
    app.state.stop_callbacks = set()
    app.include_router(persistence_routes(persistence,computer))
    app.include_router(computer_routes(computer))
    app.include_router(connector_routes(connector_manager))
    app.include_router(mcp_routes(connector_manager.mcp))
    app.include_router(model_routes(provider_router, runtime_settings))
    from pathlib import Path
    from app.voice.wake import WakeProviderRegistry, WhisperKeywordProvider
    from app.api.wake import wake_routes
    wake_registry=WakeProviderRegistry()
    wake_path=Path(runtime_settings.wake_model_path) if runtime_settings.wake_model_path else runtime_settings.project_root/"backend/data/models"/f"ggml-{runtime_settings.wake_whisper_model}.bin"
    wake_registry.register(WhisperKeywordProvider(wake_path,binary=runtime_settings.whisper_binary,model=runtime_settings.wake_whisper_model,lock=voice_manager.stt_lock))
    app.state.wake_registry=wake_registry;app.include_router(wake_routes(wake_registry))

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable(_, __):
        return JSONResponse(status_code=503, content={"detail": "Local persistence temporarily unavailable. Chat can continue without saving."})

    @app.exception_handler(ComputerError)
    async def computer_error(_, error):
        return JSONResponse(status_code=409,content={"detail":error.message,"code":error.code})

    @app.exception_handler(LookupError)
    async def record_not_found(_, __):
        return JSONResponse(status_code=404, content={"detail": "Record not found"})

    @app.exception_handler(ValueError)
    async def invalid_persistence_value(_, __):
        return JSONResponse(status_code=422, content={"detail": "Invalid persistence request"})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def native_session_guard(request, call_next):
        token=runtime_settings.native_session_token
        if request.method!="OPTIONS" and token and not secrets.compare_digest(request.headers.get("x-nova-session", ""),token.get_secret_value()):
            origin = request.headers.get("origin", "")
            headers = {"Vary": "Origin"}
            if origin in runtime_settings.cors_origin_list:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Credentials"] = "true"
            return JSONResponse(status_code=401,content={"detail":"NOVA native session required"},headers=headers)
        return await call_next(request)

    @app.post("/api/runtime/stop", tags=["system"])
    async def emergency_stop():
        for provider in app.state.wake_registry.providers.values():await provider.stop()
        await asyncio.gather(computer.stop_all(),*(callback() for callback in list(app.state.stop_callbacks)),return_exceptions=True)
        return {"stopped":True}

    @app.post("/api/runtime/pause", tags=["system"])
    async def pause_runtime():
        for managed in list(computer.manager.active.values()):await computer.pause(managed.task.id)
        return {"paused":True}

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "nova-backend", "status": "ok", "docs": "/docs"}

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        payload = await _health_payload(runtime_settings, provider_router, voice_manager)
        payload.capabilities["persistence"] = persistence.db.status()
        payload.capabilities["computer_use"] = {**computer.settings.model_dump(), "permissions": await computer.permissions()}
        payload.capabilities["connectors"] = await connector_manager.status()
        payload.capabilities["mcp"] = connector_manager.mcp.list()
        payload.capabilities["privacy"]["memory_enabled"] = persistence.settings.memory_enabled
        return payload

    @app.get("/api/health/live", tags=["system"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config/public", tags=["system"])
    async def public_config() -> dict[str, object]:
        return {**runtime_settings.public_dict(), **persistence.settings.model_dump()}

    @app.get("/api/voice", tags=["voice"])
    async def voice_status() -> dict[str, Any]:
        return await voice_manager.status()

    @app.put("/api/voice", tags=["voice"])
    async def voice_settings(options: VoiceSettings) -> dict[str, Any]:
        # Keep legacy whisper_model clients compatible, while making named
        # profiles canonical for new settings writes.
        if "stt_profile" in options.model_fields_set:
            options.whisper_model = STT_PROFILE_MODELS[options.stt_profile]
        voice_manager.settings = options
        return await voice_manager.status()

    @app.get("/api/voice/voices", tags=["voice"])
    async def voice_names() -> list[dict[str, str]]:
        from app.voice.tts.macos import MacOSTTSProvider
        provider = voice_manager.tts()
        if isinstance(provider, MacOSTTSProvider):
            try:
                return await provider.voices()
            except VoiceError:
                return []
        return []

    @app.get("/api/voice/models", tags=["voice"])
    async def speech_models():return voice_manager.models()

    @app.websocket("/ws")
    async def websocket_bridge(websocket: WebSocket) -> None:
        token=runtime_settings.native_session_token
        if token and not secrets.compare_digest(websocket.query_params.get("session", ""),token.get_secret_value()):
            await websocket.close(code=1008);return
        origin = websocket.headers.get("origin")
        if origin and origin not in runtime_settings.cors_origin_list:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        send_lock = asyncio.Lock()
        closed = asyncio.Event()
        active: dict[str, ActiveRequest] = {}
        agent = Agent(
            provider_router,
            runtime_settings,
            context=ConversationContext(history_limit=runtime_settings.conversation_history_limit),
        )
        conversation_id = ""
        ephemeral_conversation = False
        memory_session = MemorySession()
        computer_owner = new_id()

        async def emit(event_type: str, request_id: str | None = None, **payload: Any) -> None:
            if closed.is_set():
                return
            try:
                async with send_lock:
                    await websocket.send_json(_event(event_type, request_id, **payload))
            except (RuntimeError, WebSocketDisconnect):
                closed.set()

        async def persistence_warning(request_id: str | None = None) -> None:
            await emit("system.error", request_id, code="PERSISTENCE_UNAVAILABLE", message="Local saving is temporarily unavailable; NOVA can still reply.", recoverable=True)

        async def select_conversation(identifier: object = None, *, force: bool = False) -> str | None:
            nonlocal conversation_id, ephemeral_conversation
            try:
                if ephemeral_conversation and identifier == conversation_id:
                    await asyncio.to_thread(persistence.initialize)
                    conversation = await asyncio.to_thread(persistence.conversations.latest)
                elif identifier is not None:
                    selected = str(UUID(str(identifier)))
                    conversation = await asyncio.to_thread(persistence.conversations.select, selected)
                else:
                    conversation = await asyncio.to_thread(persistence.conversations.latest)
                changed = conversation_id != conversation["id"]
                if changed or force:
                    memory_session.pending = None
                conversation_id = conversation["id"]
                ephemeral_conversation = False
                if changed or force or persistence.settings.save_chat_history:
                    history = await asyncio.to_thread(persistence.context_messages, conversation_id, runtime_settings.conversation_history_limit)
                    agent.context.restore(history)
                return conversation_id
            except DatabaseUnavailable:
                if not conversation_id:
                    ephemeral_conversation = True
                conversation_id = conversation_id or new_id()
                await persistence_warning()
                return conversation_id
            except (LookupError, ValueError):
                await emit("system.error", code="CONVERSATION_NOT_FOUND", message="Open an available conversation, or create a new chat.")
                return None

        async def save_user(record: ActiveRequest, content: str) -> None:
            started = time.perf_counter()
            record.memory_command = persistence.memory.is_command(content, memory_session)
            try:
                record.user_message_id = await asyncio.to_thread(persistence.begin, record.conversation_id, content, record.input_type, record.request_id, memory_command=record.memory_command)
            except (DatabaseUnavailable, LookupError):
                await persistence_warning(record.request_id)
            record.database_latency_ms += (time.perf_counter() - started) * 1000

        async def mark_user(record: ActiveRequest, status: str) -> None:
            if record.generation_completed:
                return
            if record.user_message_id:
                try:
                    await asyncio.to_thread(persistence.conversations.message_status, record.user_message_id, status)
                except DatabaseUnavailable:
                    await persistence_warning(record.request_id)

        async def run_request(record: ActiveRequest, content: str, mode: str, *, audio: bytes | None = None, speak: bool = False) -> None:
            full_response = ""
            original_content = content
            speech: SpeechQueue | None = None
            options = record.voice_options or voice_manager.settings.model_copy()

            async def speech_event(event_type: str, **payload: Any) -> None:
                await emit(event_type, record.request_id, **payload)

            async def task_event(event_type: str, task, payload: dict) -> None:
                nonlocal speech
                record.task_id = task.id
                record.pipeline_trace["task_id"] = task.id
                record.pipeline_trace["current_step"] = _trace_text(task.current_step, 500)
                record.pipeline_trace["retry_count"] = task.retry_count
                values={"task_id":task.id,"task_goal":task.goal,"task_status":task.status,
                    "current_step":task.current_step,"step_index":task.step_index,"total_steps":task.total_steps,
                    "task_mode":task.mode,"retry_count":task.retry_count,
                    "conversation_id":record.conversation_id,
                    "simulation":computer.settings.simulation,
                    "task_duration_ms":round((time.perf_counter()-record.started_at)*1000,2),
                    "last_action":task.last_action.model_dump() if task.last_action else None,
                    "last_observation":task.last_observation.model_dump() if task.last_observation else None,
                    "pipeline_trace": record.pipeline_trace.copy()}
                values.update(payload)
                if task.status == TaskStatus.FAILED:
                    values["failure"] = task.metadata.get("failure", {})
                await emit(event_type,record.request_id,**values)
                if task.status in {TaskStatus.PAUSED,TaskStatus.WAITING_FOR_CONFIRMATION,TaskStatus.CANCELLED} and record.speech:
                    await record.speech.stop();record.speech=None;speech=None
                if task.status==TaskStatus.ACTING and speak and options.voice_enabled and options.tts_enabled and record.speech is None:
                    speech=SpeechQueue(voice_manager.tts(options),speech_event);record.speech=speech
                if speech and record.speech and event_type=="task.updated" and task.status==TaskStatus.ACTING:
                    await speech.enqueue(task.current_step)

            try:
                if audio is not None:
                    if not options.voice_enabled or not options.stt_enabled:
                        raise VoiceError("STT_DISABLED", "Speech input is disabled; text input is still available.")
                    await emit("voice.processing", record.request_id)
                    await asyncio.to_thread(validate_audio, audio, options.max_recording_seconds)
                    transcript = await voice_manager.stt(options).transcribe(audio)
                    audio = None
                    record.transcript = _trace_text(transcript.raw_transcript or transcript.text)
                    content = normalize_spoken_command(transcript.text)
                    record.normalized_command = _trace_text(content)
                    record.pipeline_trace.update({
                        "transcript": record.transcript,
                        "normalized_command": record.normalized_command,
                        "wake_phrase_removed": content != transcript.text.strip(),
                        "stt": {"provider": transcript.provider, "language": transcript.language, "duration_ms": transcript.duration_ms, "latency_ms": transcript.latency_ms},
                    })
                    original_content = content
                    transcript_event = transcript.model_dump()
                    transcript_event["text"] = content
                    transcript_event["normalized_transcript"] = content
                    transcript_event["wake_phrase_removed"] = content != transcript.text.strip()
                    if has_secret(record.transcript):
                        for key in ("text", "raw_transcript", "normalized_transcript"):
                            transcript_event[key] = "[Sensitive input omitted]"
                    await emit("voice.transcript", record.request_id, **transcript_event)
                    if not content and is_wake_only_transcript(transcript.text):
                        record.status = "completed"
                        await emit("voice.wake_only", record.request_id, wake_only=True, pipeline_trace=record.pipeline_trace.copy())
                        return
                    if not content:
                        raise VoiceError("EMPTY_TRANSCRIPT", "No speech recognized. Try again or use text input.")
                    await save_user(record, content)
                    if not computer.active_for(computer_owner) and content.strip().lower().rstrip(".!।") in {"stop", "nova stop", "hey nova stop", "नोवा स्टॉप", "नोवा रुको"}:
                        await mark_user(record, "cancelled")
                        await emit("agent.cancelled", record.request_id)
                        return
                active_computer=computer.active_for(computer_owner)
                if active_computer and not record.memory_command:
                    control=computer.control(content)
                    acknowledgement="I've updated the task with your instruction."
                    if control:
                        acknowledgement={"resume":"Task continued.","stop":"Task stopped.","pause":"Task paused.","skip":"Step skipped.","confirm":"Action approved.","deny":"Action cancelled."}[control]
                        if control=="stop":await computer.stop(active_computer.task.id)
                        elif control=="pause":await computer.pause(active_computer.task.id)
                        elif control=="skip":active_computer.skip_requested=True;active_computer.confirm_event.set();await computer.resume(active_computer.task.id,feedback="Skip the proposed action; do not claim it was performed.")
                        elif control=="resume":
                            if active_computer.task.status==TaskStatus.WAITING_FOR_CONFIRMATION:acknowledgement="This action still needs explicit confirmation."
                            else:await computer.resume(active_computer.task.id)
                        elif control in {"confirm","deny"}:
                            if active_computer.task.status==TaskStatus.WAITING_FOR_CONFIRMATION:await computer.confirm(active_computer.task.id,control=="confirm")
                            else:acknowledgement="No action is waiting for approval."
                    else:
                        await computer.resume(active_computer.task.id,feedback=content)
                    try:await asyncio.to_thread(persistence.complete,record.conversation_id,acknowledgement,record.input_type,record.request_id,record.user_message_id,"local","task-control")
                    except (DatabaseUnavailable,LookupError):await persistence_warning(record.request_id)
                    record.generation_completed=True;record.status="completed"
                    await emit("agent.completed",record.request_id,content=acknowledgement,conversation_id=record.conversation_id,provider="local",model="task-control")
                    return
                if speak and options.voice_enabled and options.tts_enabled:
                    read_urls = bool(re.search(r"\b(?:read|spell)\b.*\b(?:url|link|address)\b|\b(?:url|link|address)\b.*\b(?:read|spell)\b", content, re.I))
                    tts = voice_manager.tts(options, read_urls=read_urls)
                    if await tts.is_available():
                        speech = SpeechQueue(tts, speech_event, read_urls=read_urls)
                        record.speech=speech
                    else:
                        await emit("system.error", record.request_id, code="TTS_UNAVAILABLE", message="Speech output is unavailable; the response will appear silently.", recoverable=True)
                if (computer.likely_task(content) or computer.active_for(computer_owner)) and not record.memory_command:
                    content=await computer.retry_goal(content)
                    record.normalized_command = _trace_text(content)
                    record.pipeline_trace["normalized_command"] = record.normalized_command
                    record.pipeline_trace.update({"provider": "local", "model": "computer-task"})
                    try:
                        permission_snapshot = await computer.permissions()
                        record.pipeline_trace["permission_state"] = permission_snapshot.get("permission_state", {})
                        record.pipeline_trace["computer_settings"] = {
                            "simulation": permission_snapshot.get("settings", {}).get("simulation"),
                            "accessibility_access": permission_snapshot.get("settings", {}).get("accessibility_access"),
                            "screen_access": permission_snapshot.get("settings", {}).get("screen_access"),
                            "browser_access": permission_snapshot.get("settings", {}).get("browser_access"),
                            "filesystem_access": permission_snapshot.get("settings", {}).get("filesystem_access"),
                        }
                    except Exception:
                        record.pipeline_trace["permission_state"] = {"status": "unavailable"}
                    await emit("agent.started", record.request_id, conversation_id=record.conversation_id, task_mode=computer.settings.mode)
                    await emit("agent.thinking", record.request_id)
                    memories=await asyncio.to_thread(persistence.memory.retriever.retrieve,content,top_k=persistence.settings.memory_top_k) if persistence.settings.memory_enabled else []
                    task, summary = await computer.run(content, task_event, owner=computer_owner,memory=MemoryRetriever.context(memories),conversation_id=record.conversation_id,trace=record.pipeline_trace)
                    full_response = task.metadata.get("assistant_response", summary)
                    record.provider, record.model = "local", "computer-task"
                    record.generation_completed = task.status==TaskStatus.COMPLETED
                    try:
                        if record.generation_completed:
                            await asyncio.to_thread(persistence.complete, record.conversation_id, full_response, record.input_type,
                                record.request_id, record.user_message_id, record.provider, record.model)
                        else:await mark_user(record,"failed")
                    except (DatabaseUnavailable, LookupError):
                        await persistence_warning(record.request_id)
                    if speech and record.speech:
                        await speech.enqueue(full_response)
                        await speech.finish()
                    await emit("llm.completed", record.request_id, content=full_response, provider=record.provider, model=record.model,
                        thinking_enabled=False, thinking_reason="computer_task", ttft_ms=0, total_generation_latency_ms=0,
                        token_count=1, token_count_kind="computer_task_summary", conversation_id=record.conversation_id)
                    await emit("agent.completed", record.request_id, content=full_response, conversation_id=record.conversation_id,
                        provider=record.provider, model=record.model, task_id=task.id,
                        pipeline_trace=task.metadata.get("pipeline_trace", record.pipeline_trace),
                        failure=task.metadata.get("failure"), latency_ms=round((time.perf_counter() - record.started_at) * 1000, 2))
                    record.status = "completed"
                    return
                content = safe_text(content)
                await emit("agent.started", record.request_id, conversation_id=record.conversation_id)
                await emit("agent.thinking", record.request_id)
                try:
                    # Original text is retained only for deterministic secret rejection.
                    memory = await asyncio.to_thread(persistence.memory.process, original_content, persistence.settings.model_copy(), memory_session)
                    if memory.memory_id and record.user_message_id:
                        await asyncio.to_thread(persistence.conversations.annotate, record.user_message_id, {"memory_id": memory.memory_id})
                except DatabaseUnavailable:
                    await persistence_warning(record.request_id)
                    memory = MemoryResult(answer="I couldn't access saved memories right now. Please try again." if record.memory_command else None)
                for kind, payload in memory.events:
                    await emit(kind, record.request_id, **payload)

                async def local_stream():
                    started = time.perf_counter()
                    yield AgentStreamEvent("llm.started", "local", "memory-command", thinking_reason="local_memory_command")
                    ttft = round((time.perf_counter() - started) * 1000, 3)
                    yield AgentStreamEvent("llm.token", "local", "memory-command", memory.answer)
                    agent.context.commit(content, memory.answer)
                    yield AgentStreamEvent("llm.completed", "local", "memory-command", memory.answer, ttft_ms=ttft,
                        total_generation_latency_ms=round((time.perf_counter() - started) * 1000, 3), token_count=1)

                selected_stream = local_stream() if memory.answer is not None else agent.stream_message(content, mode=mode, memory_context=MemoryRetriever.context(memory.retrieved))
                async with aclosing(selected_stream) as stream:
                    async for stream_event in stream:
                        if stream_event.type == "llm.started":
                            record.provider = stream_event.provider
                            record.model = stream_event.model
                            await emit(
                                "llm.started",
                                record.request_id,
                                provider=stream_event.provider,
                                model=stream_event.model,
                                thinking_enabled=stream_event.thinking_enabled,
                                thinking_reason=stream_event.thinking_reason,
                                conversation_id=record.conversation_id,
                                retrieved_memory_count=len(memory.retrieved),
                                retrieved_memory_ids=[item["memory_id"] for item in memory.retrieved],
                                retrieval_scores=[item["relevance_score"] for item in memory.retrieved],
                                memory_inserted=memory.inserted, memory_updated=memory.updated, memory_deleted=memory.deleted,
                                database_latency_ms=round(record.database_latency_ms + memory.latency_ms, 3),
                            )
                        elif stream_event.type == "llm.token":
                            full_response += stream_event.content
                            await emit(
                                "llm.token",
                                record.request_id,
                                content=stream_event.content,
                                provider=stream_event.provider,
                                model=stream_event.model,
                            )
                            if speech:
                                await speech.feed(stream_event.content)
                        elif stream_event.type == "llm.completed":
                            record.generation_completed = True
                            full_response = stream_event.content
                            record.provider = stream_event.provider
                            record.model = stream_event.model
                            try:
                                await asyncio.to_thread(persistence.complete, record.conversation_id, full_response, record.input_type,
                                    record.request_id, record.user_message_id, record.provider, record.model, memory_command=record.memory_command,
                                    memory_ids=[item["memory_id"] for item in memory.retrieved])
                            except (DatabaseUnavailable, LookupError):
                                await persistence_warning(record.request_id)
                            await emit(
                                "llm.completed",
                                record.request_id,
                                content=full_response,
                                provider=stream_event.provider,
                                model=stream_event.model,
                                thinking_enabled=stream_event.thinking_enabled,
                                ttft_ms=stream_event.ttft_ms,
                                total_generation_latency_ms=stream_event.total_generation_latency_ms,
                                token_count=stream_event.token_count,
                                token_count_kind="visible_content_chunks",
                            )
                if speech:
                    await speech.finish()
                record.status = "completed"
                latency_ms = round((time.perf_counter() - record.started_at) * 1000, 2)
                await emit(
                    "agent.completed",
                    record.request_id,
                    content=full_response,
                    conversation_id=record.conversation_id,
                    provider=record.provider,
                    model=record.model,
                    latency_ms=latency_ms,
                )
                logger.info(
                    "agent request completed request_id=%s provider=%s model=%s latency_ms=%s",
                    record.request_id,
                    record.provider or "unknown",
                    record.model or "unknown",
                    latency_ms,
                )
            except asyncio.CancelledError:
                record.status = "cancelled"
                if speech:
                    await speech.stop()
                await mark_user(record, "cancelled")
                await emit("agent.cancelled", record.request_id)
                logger.info("agent request cancelled request_id=%s", record.request_id)
                raise
            except ComputerError as exc:
                record.status="error";await mark_user(record,"failed")
                await emit("system.error",record.request_id,code=exc.code,message=exc.message,task_id=record.task_id,pipeline_trace=record.pipeline_trace.copy())
            except VoiceError as exc:
                record.status = "error"
                await mark_user(record, "failed")
                await emit("system.error", record.request_id, code=exc.code, message=exc.message,task_id=record.task_id,pipeline_trace=record.pipeline_trace.copy())
            except LLMError as exc:
                record.status = "error"
                await mark_user(record, "failed")
                logger.warning(
                    "agent request failed request_id=%s provider=%s code=%s",
                    record.request_id,
                    record.provider or exc.provider or "unknown",
                    exc.code,
                )
                await emit(
                    "system.error",
                    record.request_id,
                    code=exc.code,
                    message=exc.user_message,
                    provider=record.provider or exc.provider,
                    task_id=record.task_id,
                    pipeline_trace=record.pipeline_trace.copy(),
                )
            except ValueError as exc:
                record.status = "error"
                await mark_user(record, "failed")
                await emit("system.error", record.request_id, code="INVALID_MESSAGE", message=str(exc),task_id=record.task_id,pipeline_trace=record.pipeline_trace.copy())
            except Exception:
                record.status = "error"
                await mark_user(record, "failed")
                logger.exception("unexpected agent failure request_id=%s", record.request_id)
                await emit(
                    "system.error",
                    record.request_id,
                    code="INTERNAL_ERROR",
                    message="NOVA hit an unexpected error while thinking.",
                    task_id=record.task_id,
                    pipeline_trace=record.pipeline_trace.copy(),
                )
            finally:
                audio = None
                if speech and not speech.task.done():
                    await speech.stop()
                active.pop(record.request_id, None)

        async def cancel_record(record: ActiveRequest) -> None:
            if record.task:
                if not record.task.cancelling():record.task.cancel()
                await asyncio.gather(record.task, return_exceptions=True)
            # Includes cancellation before a newly-created coroutine first runs.
            if record.status != "cancelled":
                record.status = "cancelled"
                await mark_user(record, "cancelled")
                await emit("agent.cancelled", record.request_id)
            active.pop(record.request_id, None)

        async def cancel_active() -> None:
            for record in list(active.values()):
                if record.speech:await record.speech.stop();record.speech=None
                await cancel_record(record)

        app.state.stop_callbacks.add(cancel_active)

        async def expire_recording(record: ActiveRequest) -> None:
            try:
                await asyncio.sleep(voice_manager.settings.max_recording_seconds + 10)
                await emit("system.error", record.request_id, code="RECORDING_TIMEOUT", message="Recording timed out. Start a new push-to-talk request.")
                active.pop(record.request_id, None)
            except asyncio.CancelledError:
                raise

        try:
            # Ready remains the first frame; restore before accepting interactions.
            try:
                conversation = await asyncio.to_thread(persistence.conversations.latest)
                conversation_id = conversation["id"]
                agent.context.restore(await asyncio.to_thread(persistence.context_messages, conversation_id, runtime_settings.conversation_history_limit))
            except DatabaseUnavailable:
                conversation_id = new_id()
                ephemeral_conversation = True
            await emit("system.ready", phase=5, service="nova-backend", message="NOVA conversation and task bridge connected", conversation_id=conversation_id)
            while True:
                try:
                    message = await websocket.receive_json()
                except ValueError:
                    await emit("system.error", code="INVALID_JSON", message="Message must be valid JSON")
                    continue

                if not isinstance(message, dict):
                    await emit("system.error", code="INVALID_MESSAGE", message="Message must be an object")
                    continue

                message_type = message.get("type")
                if not isinstance(message_type, str):
                    await emit("system.error", code="INVALID_MESSAGE", message="Message type must be a string.")
                    continue
                data = message.get("data") if isinstance(message.get("data"), dict) else {}
                if message_type in {"agent.message", "chat.message"}:
                    content = message.get("content") or data.get("text")
                    if not isinstance(content, str) or not content.strip():
                        await emit("system.error", code="INVALID_MESSAGE", message="Message content cannot be empty")
                        continue
                    if len(content) > 64000:
                        await emit("system.error", code="INVALID_MESSAGE", message="Message exceeds the local conversation limit.")
                        continue
                    active_computer=computer.active_for(computer_owner)
                    if active and not active_computer:
                        await emit("system.error", code="AGENT_BUSY", message="NOVA is already working on a request.")
                        continue
                    request_id = _valid_request_id(message.get("request_id"))
                    mode = message.get("mode") or data.get("mode") or "normal"
                    selected = await select_conversation(message.get("conversation_id") or conversation_id)
                    if not selected:
                        continue
                    record = ActiveRequest(request_id=request_id, started_at=time.perf_counter(), voice_options=voice_manager.settings.model_copy(), conversation_id=selected)
                    record.normalized_command = _trace_text(content.strip())
                    record.pipeline_trace = {"request_id": request_id, "input_type": "text", "transcript": "", "normalized_command": record.normalized_command}
                    await save_user(record, content.strip())
                    active[request_id] = record
                    record.task = asyncio.create_task(run_request(record, content.strip(), str(mode), speak=message.get("speak") is True))
                elif message_type == "voice.start":
                    active_computer=computer.active_for(computer_owner)
                    if active_computer:
                        if active_computer.task.status!=TaskStatus.WAITING_FOR_CONFIRMATION:await computer.pause(active_computer.task.id)
                        for ongoing in active.values():
                            if ongoing.speech:await ongoing.speech.stop();ongoing.speech=None
                        for ongoing in list(active.values()):
                            if not ongoing.task_id:await cancel_record(ongoing)
                    else:await cancel_active()
                    request_id = _valid_request_id(message.get("request_id"))
                    options = voice_manager.settings.model_copy()
                    if not options.voice_enabled or not options.stt_enabled:
                        await emit("system.error", request_id, code="STT_DISABLED", message="Speech input is disabled; use text input.")
                        continue
                    selected = await select_conversation(message.get("conversation_id") or conversation_id)
                    if not selected:
                        continue
                    record = ActiveRequest(request_id, time.perf_counter(), status="recording", voice_options=options, conversation_id=selected, input_type="voice",
                        pipeline_trace={"request_id": request_id, "input_type": "voice", "transcript": "", "normalized_command": ""})
                    active[request_id] = record
                    record.task = asyncio.create_task(expire_recording(record))
                    await emit("voice.listening", request_id)
                elif message_type == "voice.recording":
                    record = active.get(str(message.get("request_id")))
                    if record and record.status == "recording":
                        await emit("voice.recording", record.request_id)
                elif message_type == "voice.audio":
                    record = active.get(str(message.get("request_id")))
                    if not record or record.status != "recording":
                        await emit("system.error", str(message.get("request_id")), code="INVALID_VOICE_STATE", message="Start push-to-talk before sending audio.")
                        continue
                    encoded = message.get("audio")
                    try:
                        if not isinstance(encoded, str) or len(encoded) > 2_600_000:
                            raise ValueError("Audio too large")
                        audio = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        if record.task:
                            record.task.cancel()
                            await asyncio.gather(record.task, return_exceptions=True)
                        active.pop(record.request_id, None)
                        await emit("system.error", record.request_id, code="INVALID_AUDIO", message="Audio must be bounded base64 PCM WAV.")
                        continue
                    if record.task:
                        record.task.cancel()
                        await asyncio.gather(record.task, return_exceptions=True)
                    record.status = "processing_audio"
                    record.task = asyncio.create_task(run_request(record, "", "normal", audio=audio, speak=True))
                    audio = None
                elif message_type == "conversation.select":
                    await cancel_active()
                    selected = await select_conversation(message.get("conversation_id"), force=True)
                    if selected:
                        await emit("conversation.updated", conversation_id=selected)
                elif message_type in {"agent.stop", "agent.cancel"}:
                    active_computer=computer.active_for(computer_owner)
                    if active_computer:await computer.stop(active_computer.task.id)
                    requested_id = message.get("request_id") or data.get("request_id")
                    target = active.get(str(requested_id)) if requested_id else next(iter(active.values()), None)
                    if target and target.task:
                        await cancel_record(target)
                    else:
                        await emit("agent.cancelled", str(requested_id) if requested_id else None)
                elif message_type == "ping":
                    await emit("system.pong")
                elif message_type == "voice.stop":
                    had_active = bool(active)
                    await cancel_active()
                    if not had_active:
                        await emit("agent.cancelled", str(message.get("request_id")) if message.get("request_id") else None)
                elif message_type in {"task.pause","task.resume","task.confirm","task.stop"}:
                    managed=computer.active_for(computer_owner)
                    if managed:
                        if message_type=="task.pause":await computer.pause(managed.task.id)
                        elif message_type=="task.resume":await computer.resume(managed.task.id,feedback=str(message.get("feedback", ""))[:2000])
                        elif message_type=="task.confirm":
                            if isinstance(message.get("confirmed"),bool):await computer.confirm(managed.task.id,message["confirmed"])
                        elif message_type=="task.stop":await computer.stop(managed.task.id)
                else:
                    await emit("system.ack", received_type=message_type or "invalid", phase=2)
        except WebSocketDisconnect:
            closed.set()
        finally:
            app.state.stop_callbacks.discard(cancel_active)
            closed.set()
            tasks = [record.task for record in active.values() if record.task is not None]
            for task in tasks:
                if not task.cancelling():task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            active.clear()

    return app


app = create_app()
