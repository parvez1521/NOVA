"""Local conversation API. Destructive routes require explicit confirmation."""

from dataclasses import replace
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.agent.conversations import ConversationManager
from app.database.models import ClearData, Confirmation, ConversationCreate, ConversationPatch, MemoryCreate, MemoryPatch, MemorySettings


def persistence_routes(manager: ConversationManager, computer=None) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["persistence"])
    repository = manager.conversations
    memories = manager.memory.repository

    def candidate(options: MemoryCreate, source: str):
        fact = manager.memory.extractor.extract(options.content, explicit=True, source=source)
        if fact is None:
            raise HTTPException(422, "Credentials, private secrets and unsafe instructions cannot be saved as memory")
        return replace(fact, category=options.category) if options.category else fact

    @router.get("/persistence")
    def state():
        return manager.state()

    @router.get("/settings/memory")
    def settings():
        return manager.settings.model_dump()

    @router.put("/settings/memory")
    def update_settings(options: MemorySettings):
        return manager.update_settings(options)

    @router.get("/conversations")
    def conversations(archived: bool = False, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
        return repository.list(archived=archived, limit=limit, offset=offset)

    @router.get("/conversations/search")
    def search(q: str = Query(min_length=1, max_length=200), archived: bool = False, limit: int = Query(50, ge=1, le=100)):
        return repository.search(q, archived=archived, limit=limit)

    @router.post("/conversations", status_code=201)
    def create(options: ConversationCreate):
        return repository.create(options.title)

    @router.get("/conversations/{identifier}")
    def conversation(identifier: UUID):
        return repository.get(str(identifier))

    @router.patch("/conversations/{identifier}")
    def rename(identifier: UUID, options: ConversationPatch):
        return repository.rename(str(identifier), options.title)

    @router.delete("/conversations/{identifier}")
    def delete(identifier: UUID, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(409, "Confirm permanent conversation deletion first")
        repository.delete(str(identifier))
        return {"deleted": True, "active_conversation": repository.latest()}

    @router.get("/conversations/{identifier}/messages")
    def messages(identifier: UUID, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        return repository.messages(str(identifier), limit=limit, offset=offset)

    @router.post("/conversations/{identifier}/archive")
    def archive(identifier: UUID):
        return repository.archive(str(identifier))

    @router.post("/conversations/{identifier}/restore")
    def restore(identifier: UUID):
        return repository.archive(str(identifier), False)

    @router.get("/memories")
    def memory_list(include_inactive: bool = False, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0)):
        return memories.list(include_inactive=include_inactive, limit=limit, offset=offset)

    @router.get("/memories/search")
    def memory_search(q: str = Query(min_length=1, max_length=200), limit: int = Query(5, ge=1, le=20)):
        return manager.memory.retriever.retrieve(q, top_k=limit, preferences=False, mark_used=False)

    @router.post("/memories", status_code=201)
    def create_memory(options: MemoryCreate):
        if not manager.settings.memory_enabled:
            raise HTTPException(409, "Memory is disabled")
        memory, _ = memories.upsert(candidate(options, "manual"))
        return memory

    @router.patch("/memories/{identifier}")
    def edit_memory(identifier: UUID, options: MemoryPatch):
        memory, _ = memories.edit(str(identifier), candidate(options, "edit"))
        return memory

    @router.delete("/memories/{identifier}")
    def delete_memory(identifier: UUID, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(409, "Confirm permanent memory deletion first")
        memories.delete(str(identifier))
        return {"deleted": True}

    @router.post("/memories/clear")
    def clear_memories(options: Confirmation):
        return {"deleted": memories.clear()}

    @router.post("/data/clear")
    async def clear_data(options: ClearData):
        if options.scope=="everything" and computer:await computer.stop_all()
        import asyncio
        return await asyncio.to_thread(clear_local_data,options)

    def clear_local_data(options: ClearData):
        with manager.db.session():
            if options.scope in {"chats", "everything"}:
                repository.clear()
            if options.scope in {"memories", "everything"}:
                memories.clear()
            if options.scope == "everything":
                with manager.db.session() as connection:
                    connection.execute("DELETE FROM tasks")
        return manager.state()

    @router.get("/export/chats")
    def export_chats(format: Literal["json", "markdown"] = "json"):
        data = repository.export()
        if format == "json":
            return JSONResponse({"version": 1, "conversations": data}, headers={"Content-Disposition": 'attachment; filename="nova-chat-history.json"'})
        lines = ["# NOVA chat history", ""]
        for conversation in data:
            lines.extend(["## " + conversation["title"], ""])
            for message in conversation["messages"]:
                lines.extend([f"### {'You' if message['role'] == 'user' else 'NOVA'} · {message['timestamp']}", "", message["content"], ""])
        return Response("\n".join(lines), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="nova-chat-history.md"'})

    @router.get("/export/memories")
    def export_memories(format: Literal["json", "markdown"] = "json"):
        data = memories.export()
        if format == "json":
            return JSONResponse({"version": 1, "memories": data}, headers={"Content-Disposition": 'attachment; filename="nova-memories.json"'})
        lines = ["# NOVA memories", ""]
        for memory in data:
            lines.append(f"- [{memory['category']}{'' if memory['is_active'] else ', inactive'}] {memory['content']}")
        return Response("\n".join(lines), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="nova-memories.md"'})

    return router
