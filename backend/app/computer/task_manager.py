import asyncio
import time
from collections.abc import Awaitable, Callable

from app.computer.models import KillSwitch, Task, TaskStatus
from app.database.privacy import safe_text


TaskEmitter = Callable[[str, Task, dict], Awaitable[None]]


class ManagedTask:
    def __init__(self, task: Task, emit: TaskEmitter, owner: str = ""):
        self.task = task
        self.emit = emit
        self.owner = owner
        self.kill = KillSwitch()
        self.pause_event = asyncio.Event(); self.pause_event.set()
        self.confirm_event = asyncio.Event()
        self.confirmed = False
        self.pending_action = None
        self.created_monotonic = time.monotonic()
        self.runner: asyncio.Task | None=None
        self.evidence: list[dict]=[]
        self.last_result=None
        self.verification=None
        self.feedback=""
        self.user_updates=[]
        self.skip_requested=False
        self.paused_step=""

    async def update(self, status: TaskStatus, step: str, **payload):
        if not self.pause_event.is_set() and status in {TaskStatus.PLANNING,TaskStatus.OBSERVING,TaskStatus.ACTING,TaskStatus.VERIFYING}:return
        self.task.status=status; self.task.current_step=step; self.task.updated_at=__import__("app.database.models",fromlist=["now"]).now()
        await self.emit("task.updated", self.task, payload)
        event={TaskStatus.QUEUED:"task.started",TaskStatus.PLANNING:"task.planning",TaskStatus.OBSERVING:"task.observing",TaskStatus.ACTING:"task.action.started",TaskStatus.VERIFYING:"task.verification.started",TaskStatus.WAITING_FOR_CONFIRMATION:"task.waiting_confirmation",TaskStatus.PAUSED:"task.paused",TaskStatus.COMPLETED:"task.completed",TaskStatus.FAILED:"task.failed",TaskStatus.CANCELLED:"task.cancelled"}.get(status)
        if status==TaskStatus.VERIFYING and step in {"Step verified","[SIMULATION] Step simulated"}:
            event="task.verification.completed"
            await self.emit("task.action.completed",self.task,{"step_id":f"{self.task.id}:{max(0,self.task.step_index-1)}","summary":safe_text(step),"metadata":{"verified":True}})
        if event:await self.emit(event,self.task,{"step_id":f"{self.task.id}:{self.task.step_index}","summary":safe_text(step),"metadata":{"simulated":self.task.metadata.get("simulation",False)}})

    def check(self):
        self.kill.check()

    async def wait_if_paused(self):
        self.check()
        await self.pause_event.wait()
        self.check()

    async def pause(self):
        self.paused_step=self.task.current_step
        self.pause_event.clear(); await self.update(TaskStatus.PAUSED, "Paused")

    async def resume(self):
        self.created_monotonic=time.monotonic()
        self.pause_event.set()
        status=TaskStatus.WAITING_FOR_CONFIRMATION if self.pending_action and not self.confirm_event.is_set() else TaskStatus.ACTING
        await self.update(status, self.paused_step or "Resuming", **({"confirmation":{"tool":self.pending_action.tool,"arguments":self.pending_action.arguments}} if status==TaskStatus.WAITING_FOR_CONFIRMATION else {}))
        await self.emit("task.resumed",self.task,{"step_id":f"{self.task.id}:{self.task.step_index}","summary":"Task resumed","metadata":{}})

    async def stop(self):
        self.kill.stop(); self.pause_event.set(); self.confirm_event.set()
        if self.runner and self.runner is not asyncio.current_task() and not self.runner.done() and not self.runner.cancelling():self.runner.cancel()


class TaskManager:
    def __init__(self):
        self.active: dict[str, ManagedTask] = {}
        self.running_id: str | None = None
        self.draining = False
        self.condition = asyncio.Condition()

    def create(self, goal: str, mode, settings, emit: TaskEmitter, owner: str = "") -> ManagedTask:
        task=Task(goal=goal,mode=mode,metadata={"timeout_seconds":settings.task_timeout_seconds})
        managed=ManagedTask(task,emit,owner);self.active[task.id]=managed;return managed

    async def acquire(self, managed: ManagedTask):
        async with self.condition:
            while self.running_id is not None and self.running_id != managed.task.id:
                await self.condition.wait()
            self.running_id = managed.task.id

    async def release(self, managed: ManagedTask):
        async with self.condition:
            if self.running_id == managed.task.id:
                self.running_id = None
                self.condition.notify_all()

    def running(self) -> ManagedTask | None:
        return self.active.get(self.running_id) if self.running_id else None

    def get(self, identifier: str) -> ManagedTask | None: return self.active.get(identifier)
    async def stop(self, identifier: str):
        task=self.get(identifier)
        if task: await task.stop()
        return task
