"""Bounded observe/plan/approve/execute/observe/verify loop for shared text/voice goals."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from app.computer.executor import TaskExecutor
from app.computer.history import TaskHistoryRepository
from app.computer.intent import entity_is_verified, parse_goal
from app.computer.models import Action, AgentMode, ComputerError, ComputerSettings, ScreenObservation, Task, TaskDecision, TaskStatus
from app.computer.permissions import ConfirmationRequired
from app.computer.planner import TaskPlanner
from app.computer.task_manager import TaskManager
from app.computer.verification import RetryEngine
from app.database.db import DatabaseUnavailable
from app.database.privacy import has_secret, safe_text
from app.llm.errors import LLMError


class ComputerRuntime:
    def __init__(self,config,database,router,connector_manager=None):
        self.config=config;self.db=database
        self.settings=ComputerSettings(enabled=config.computer_use_enabled,simulation=config.computer_use_simulation,mode=config.computer_agent_mode,
            screen_access=config.computer_screen_access,accessibility_access=config.computer_accessibility_access,browser_access=config.computer_browser_access,
            filesystem_access=config.computer_filesystem_access,terminal_access=config.computer_terminal_access,vision_enabled=config.computer_vision_enabled,
            require_confirmation=config.computer_require_confirmation,task_timeout_seconds=config.task_timeout_seconds,max_task_steps=config.max_task_steps,
            allow_cloud_planning=config.computer_allow_cloud_planning,watch_nova=config.live_task_view_enabled)
        self.manager=TaskManager();self.history=TaskHistoryRepository(database);self.executor=TaskExecutor(config.project_root,self.settings,connector_manager)
        self.planner=TaskPlanner(self.executor.registry,router);self.retry=RetryEngine();self.owner=None;self.last_task=None;self.last_evidence=[]
        from app.computer.vision import LocalVisionProvider
        self.executor.vision.screen.local_vision=LocalVisionProvider(config.ollama_base_url)
        self.escape_monitor=None
        self.preview_runners={}

    def initialize(self):
        from app.database.repository import SettingsRepository
        stored=SettingsRepository(self.db).get("computer_settings")
        if stored:self.settings=ComputerSettings.model_validate(stored)
        self.executor.settings=self.settings;self.executor.permissions.settings=self.settings
        with self.db.session() as connection:
            connection.execute("UPDATE tasks SET status=?,summary='Interrupted by restart; no actions resumed. Ask NOVA to retry to create a fresh plan.' WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED','INTERRUPTED')",("INTERRUPTED" if self.config.native_app_enabled else "CANCELLED",))
            for marker in ("Visible in ","Read content: ","Clipboard: "):
                connection.execute("UPDATE tasks SET summary=substr(summary,1,instr(summary,?)-1) WHERE instr(summary,?)>0",("\n"+marker,"\n"+marker))

    async def update_settings(self,settings):
        if self.manager.active:await self.stop_all()
        from app.database.repository import SettingsRepository
        await asyncio.to_thread(SettingsRepository(self.db).set,"computer_settings",settings.model_dump())
        self.settings=settings;self.executor.settings=settings;self.executor.permissions.settings=settings
        return settings.model_dump()

    async def permissions(self):
        try:
            native = await self.executor.native.call("permissions")
            state = {
                "accessibility": "GRANTED" if native.get("accessibility") else "DISABLED_IN_NOVA" if not self.settings.accessibility_access else "REQUIRED",
                "screen_recording": "GRANTED" if native.get("screen_recording") else "DISABLED_IN_NOVA" if not self.settings.screen_access else "REQUIRED",
                "browser_access": "ENABLED" if self.settings.browser_access else "DISABLED",
                "filesystem_access": "ENABLED" if self.settings.filesystem_access else "DISABLED",
            }
            return {**native,"helper_path":str(self.executor.native.bundle),"helper_available":True,
                "settings": self.settings.model_dump(), "permission_state": state}
        except (ComputerError,OSError):
            return {"accessibility":False,"screen_recording":False,"automation":"not_required","helper_available":False,
                "settings": self.settings.model_dump(), "permission_state": {
                    "accessibility": "DISABLED_IN_NOVA" if not self.settings.accessibility_access else "REQUIRED",
                    "screen_recording": "DISABLED_IN_NOVA" if not self.settings.screen_access else "REQUIRED",
                    "browser_access": "ENABLED" if self.settings.browser_access else "DISABLED",
                    "filesystem_access": "ENABLED" if self.settings.filesystem_access else "DISABLED",
                }}

    @staticmethod
    def control(content):
        value=re.sub(r"^(?:hey\s+)?(?:nova|नोवा)[\s,:.!-]*","",content.strip(),flags=re.I).casefold().strip(" .!?।")
        return {"stop":"stop","cancel task":"stop","cancel":"stop","pause":"pause","continue":"resume","resume":"resume","skip that":"skip","try again":"resume",
            "yes":"confirm","confirm":"confirm","yes do it":"confirm","no":"deny","cancel that":"deny",
            "रुको":"stop","रुक जाओ":"stop","स्टॉप":"stop","ruko":"stop","ruk jao":"stop","pause karo":"pause","पॉज़":"pause",
            "continue karo":"resume","जारी रखो":"resume","हाँ":"confirm","haan":"confirm","नहीं":"deny","nahin":"deny"}.get(value)

    def likely_task(self,content):
        # Intent gate only, never a plan. General goals are planned by the LLM.
        text=re.sub(r"^(?:hey\s+)?nova[\s,:.!-]*","",content.strip(),flags=re.I)
        if re.fullmatch(r"retry (?:that|the last|previous) task[.!]?",text,re.I):return True
        if re.match(r"^(?:remember|forget|what|how|why|tell me|explain)\b",text,re.I) and not re.search(r"(?:screen|what you see|what do you see)",text,re.I):return False
        return bool(re.search(r"\b(?:open|launch|click|scroll|navigate|go to|go back|go forward|type|copy|clipboard|rename|move|delete|organize|download|screenshot|research|find me|prepare|create (?:a |the )?(?:folder|file|directory|text)|workspace|kholo|khol(?:\s+do|\s+ke)?|gmail|email|inbox|notion|github|calendar|meeting|meetings|event|events|whatsapp|telegram|safari|chrome)\b|\b(?:read|list|check|search)\b.*\b(?:files?|folders?|directory|clipboard|apps|applications|desktop|downloads|page|email|mail|inbox|gmail|github|notion|calendar|meeting|event)\b|\bfolder\b.*\bbanao\b|\bsearch\b.*\b(?:for|google|youtube|web)\b|खोलो|खोल|स्क्रीन|what do you see",text,re.I))

    async def retry_goal(self,content):
        text=re.sub(r"^(?:hey\s+)?nova[\s,:.!-]*","",content.strip(),flags=re.I)
        if not re.fullmatch(r"retry (?:that|the last|previous) task[.!]?",text,re.I):return content
        previous=await asyncio.to_thread(self.history.list,1)
        if not previous:raise ComputerError("NO_PREVIOUS_TASK","There is no previous task to retry. Tell me the goal.")
        return previous[0]["goal"]

    async def _save(self,task,summary=""):
        try:await asyncio.to_thread(self.history.save,task,summary)
        except DatabaseUnavailable:pass

    def active_for(self,owner):
        managed=self.manager.running()
        if managed and managed.owner==owner:return managed
        return next((item for item in self.manager.active.values() if item.owner==owner),None)

    async def run(self,goal,emit,*,owner="",memory="",conversation_id="",trace=None):
        if not self.settings.enabled:raise ComputerError("COMPUTER_USE_DISABLED","Enable Computer Use in settings to perform this task.")
        if has_secret(goal):raise ComputerError("CREDENTIAL_BLOCKED","Computer tasks cannot include credentials.")
        if self.manager.draining:raise ComputerError("COMPUTER_BUSY","Another desktop task is active. Stop it before starting a new task.")
        self.owner=owner;managed=self.manager.create(safe_text(goal),self.settings.mode,self.settings,emit,owner=owner);task=managed.task
        managed.runner=asyncio.current_task()
        task.intent=parse_goal(task.goal)
        trace = trace if isinstance(trace, dict) else {}
        trace["task_id"] = task.id
        trace["task_goal"] = safe_text(task.goal)
        trace["intent"] = self._trace_intent(task.intent)
        trace["requested_application"] = task.intent.target_app
        task.metadata={"simulation":self.settings.simulation,"conversation_id":conversation_id,"intent":task.intent.model_dump(),"task_id":task.id,"pipeline_trace":trace}
        timeout_monitor=None
        try:
            self.executor.scope.scope_for(task);await self._save(task)
            if self.manager.running_id is not None:
                await managed.update(TaskStatus.QUEUED,"Queued for computer control")
            await self.manager.acquire(managed)
            timeout_monitor=asyncio.create_task(self.watch_timeout(managed))
            if not self.settings.simulation:
                self.escape_monitor=asyncio.create_task(self.executor.native.watch_escape(managed.stop))
            await managed.update(TaskStatus.QUEUED,"Task started",goal_contract=task.intent.model_dump() if task.intent else {})
            await managed.update(TaskStatus.PLANNING,"Planning the next action",plan=task.intent.ordered_steps if task.intent else [])
            repetitions={};feedback="";verified_count=0;excluded_tools=set();recovery_action=None;replan_attempts=0;planning_rounds=0
            task.total_steps=max(task.total_steps,len(task.intent.ordered_steps) if task.intent else 0)
            while task.step_index<self.settings.max_task_steps:
                planning_rounds+=1
                if planning_rounds>self.settings.max_task_steps*3:raise ComputerError("PLAN_LIMIT","The bounded planning limit was reached. I stopped safely.")
                await managed.wait_if_paused();managed.check()
                await managed.update(TaskStatus.OBSERVING,"Checking current state")
                observation=ScreenObservation(source="simulation",confidence=0,visible_text="[SIMULATION] No real screen captured.") if self.settings.simulation else await self.executor.vision.observe(self.settings)
                task.last_observation=observation
                trace["screen_state"] = self._trace_observation(observation)
                if observation.blocked_reason:
                    await managed.update(TaskStatus.PAUSED,"Handle the login or security challenge yourself, then Continue.",code=observation.blocked_reason)
                    managed.pause_event.clear();await managed.wait_if_paused();continue
                await managed.update(TaskStatus.PLANNING,"Choosing the next step")
                supplied_feedback=managed.feedback or feedback;managed.feedback=""
                if recovery_action:
                    # Fixed read-only recovery, through the same scope/permission executor.
                    # Exclusive creation failed; inspect rather than repeat or overwrite.
                    decision=TaskDecision(decision="act",action=recovery_action);recovery_action=None
                else:
                    decision=await self.planner.decide(task,observation,self.settings,evidence=managed.evidence,memory=memory,feedback="\n".join(managed.user_updates+[supplied_feedback]),excluded_tools=excluded_tools.copy())
                    trace["planner_result"] = {
                    "decision": decision.decision,
                    "steps": [safe_text(step)[:500] for step in decision.steps[:30]],
                    "message": safe_text(decision.message)[:1000],
                        "action": self._trace_action(decision.action),
                    }
                    trace["provider"] = self.planner.last_provider
                    trace["model"] = self.planner.last_model
                excluded_tools.clear()
                await managed.wait_if_paused()
                if managed.feedback:continue
                task.total_steps=max(task.total_steps,len(decision.steps),len(task.intent.ordered_steps) if task.intent else 0,task.step_index+1)
                if decision.decision=="ask":
                    await managed.update(TaskStatus.PAUSED,decision.message or "Please clarify the next step.")
                    managed.pause_event.clear();await managed.wait_if_paused();feedback="The user requested continue. Observe again; if required details remain missing ask specifically.";continue
                if decision.decision=="complete":
                    if verified_count==0:
                        repetitions["premature"]=repetitions.get("premature",0)+1
                        if repetitions["premature"]>2:raise ComputerError("NO_VERIFIED_ACTION","The planner proposed completion without any verified action.")
                        feedback="No tool has executed for THIS task. You must propose the appropriate registered tool. If the goal is already satisfied, propose a read-only tool to verify it. Do not claim completion yet."
                        continue
                    review=await self.completion_review(task,managed.evidence,feedback="\n".join(managed.user_updates))
                    if not review["complete"]:
                        feedback="Completion review found remaining work: "+review["message"]
                        repetitions["incomplete"]=repetitions.get("incomplete",0)+1
                        if repetitions["incomplete"]>2:raise ComputerError("GOAL_NOT_VERIFIED","The goal was not fully verified. Please clarify the remaining step.")
                        continue
                    return await self.finish(managed,verified_count)
                action=decision.action
                task.last_action=action
                trace["selected_tool"] = action.tool
                trace["action_attempted"] = self._trace_action(action)
                if action.tool == "browser.click" and task.intent and task.intent.target_app.casefold() != "google chrome":
                    excluded_tools.add(action.tool)
                    feedback=f"{task.intent.target_app} does not use the Chrome DOM click tool. Use the requested browser's native search/navigation or a fresh read-only observation instead."
                    continue
                if any(item.get("verified") and item["tool"]==action.tool and item["arguments"]==action.arguments and item.get("observed",{}).get("fingerprint")==observation.fingerprint for item in managed.evidence):
                    replan_attempts+=1
                    if replan_attempts>8:raise ComputerError("LOOP_PROTECTION","The planner did not advance to an unfinished goal step. I stopped safely.")
                    if action.tool in {"app.open","app.focus","app.active"} and re.search(r"\btype\b",goal,re.I) and observation.active_app.casefold()=="textedit":
                        recovery_action=Action(tool="keyboard.hotkey",arguments={"key":"n","modifiers":["command"]},expected_result="A new TextEdit document is open")
                        excluded_tools.update({"app.open","app.focus","app.active"});feedback="TextEdit is active but no document is focused. Use Command-N once to create the requested new document, then observe and type."
                    else:
                        excluded_tools.add(action.tool)
                        feedback=f"{action.tool} with these exact arguments is already verified for this task. Do not repeat it; choose the next unfinished goal step."
                    continue
                updated_goal=self.effective_goal(task.goal,"\n".join(managed.user_updates))
                research_goal=goal+"\n"+updated_goal
                if action.tool=="filesystem.write" and re.search(r"\b(?:research|search|tools|urls|results)\b",research_goal,re.I):
                    content=action.arguments.get("content","")
                    urls=re.findall(r"https?://[^\s<>)]+",content)
                    observed={url for e in managed.evidence if e["tool"] in {"browser.search","browser.read"} for link in e.get("result",{}).get("links",[]) for url in (link.get("url"),link.get("displayed_url")) if url}
                    few_requested=bool(re.search(r"\b(?:few|first (?:three|3)|three|3)\b",research_goal,re.I))
                    if not observed or not urls or few_requested and len(set(urls))<3 or any(url.rstrip(".,") not in observed for url in urls):
                        task.retry_count+=1
                        if task.retry_count>2:raise ComputerError("UNVERIFIED_RESEARCH","Read actual browser results before writing research; every saved URL must be observed.")
                        feedback=("The proposed file was NOT written. " + ("No browser results were read. Use browser.search for the requested query, then browser.read." if not observed else "Its content must include at least THREE tool names with THREE distinct complete URLs from observed source links. Use filesystem.write with content containing name — exact URL pairs; do not omit, shorten, or invent URLs."))
                        excluded_tools.add(action.tool)
                        continue
                task.last_action=action
                signature=action.tool+str(action.arguments)
                repetitions[signature]=repetitions.get(signature,0)+1
                if repetitions[signature]>3:raise ComputerError("LOOP_PROTECTION","The same action was proposed repeatedly. I stopped to prevent an action loop.")
                confirmed=False;attempts=0
                while True:
                    await managed.wait_if_paused();managed.check()
                    if managed.skip_requested:
                        managed.skip_requested=False;feedback="The user skipped the proposed step. Re-plan without claiming it was done.";break
                    try:
                        if not self.settings.simulation and action.tool.startswith(("mouse.","keyboard.","ui.","browser.click","browser.type")):
                            fresh=await self.executor.vision.observe(self.settings)
                            if self.executor.approval_key(action,observation)!=self.executor.approval_key(action,fresh):
                                observation=fresh;confirmed=False;feedback="The target UI changed after planning. Observe and re-plan before interacting.";break
                        await managed.update(TaskStatus.ACTING,self.action_summary(action))
                        if action.tool.startswith("mouse."):
                            await managed.emit("task.action.progress",task,{"step_id":f"{task.id}:{task.step_index}","summary":self.action_summary(action),"metadata":{"tool":action.tool,"visibility_mode":self.settings.action_visibility_mode}})
                        result,_=await self.executor.execute(task,action,observation,simulation=self.settings.simulation,kill=managed.kill,confirmed=confirmed)
                    except ConfirmationRequired:
                        managed.pending_action=action;managed.confirm_event.clear();managed.confirmed=False
                        await managed.update(TaskStatus.WAITING_FOR_CONFIRMATION,self.action_summary(action),confirmation={"tool":action.tool,"arguments":action.arguments,"scope":task.allowed_scope},message="Confirm this exact action?")
                        await managed.confirm_event.wait();managed.check()
                        managed.pending_action=None
                        if managed.skip_requested:managed.skip_requested=False;feedback="The user skipped this action.";break
                        if not managed.confirmed:raise asyncio.CancelledError
                        confirmed=True
                        if task.intent and task.intent.confirmation_requirement != "none" and self._is_final_send_action(action,observation):
                            task.metadata["final_action_confirmed"]=True
                        if not self.settings.simulation:
                            fresh=await self.executor.vision.observe(self.settings)
                            if self.executor.approval_key(action,observation)!=self.executor.approval_key(action,fresh):
                                confirmed=False;observation=fresh;feedback="UI changed while waiting for approval; re-plan.";break
                        continue
                    except ComputerError as error:
                        if error.code=="TEXT_FIELD_NOT_FOCUSED" and action.tool=="keyboard.type_text" and observation.active_app.casefold()=="textedit":
                            new_document=next((element for element in observation.elements if element.label.casefold()=="new document"),None)
                            if new_document:
                                recovery_action=Action(tool="mouse.click",arguments={"x":new_document.x+new_document.width/2,"y":new_document.y+new_document.height/2},expected_result="A new TextEdit document is open")
                                feedback="TextEdit is showing its Open dialog. Click the observed New Document button with the mouse, observe the editable document, then type the requested text."
                            else:
                                recovery_action=Action(tool="keyboard.hotkey",arguments={"key":"n","modifiers":["command"]},expected_result="A new TextEdit document is open")
                                feedback="TextEdit has no focused document. Create a new document with Command-N, observe it, then type the requested text."
                            task.retry_count+=1
                            if task.retry_count>2:raise
                            break
                        if error.code in {"PLACEHOLDER_PATH","ACTION_INVALID","ELEMENT_NOT_FOUND","TARGET_CHANGED","FILE_EXISTS"}:
                            feedback=error.message+" Re-plan from current observed state."+(" The existing destination must be read/listed and verified, not overwritten or blindly retried." if error.code=="FILE_EXISTS" else "");task.retry_count+=1
                            excluded_tools.add(action.tool)
                            if task.retry_count>2:raise
                            if error.code=="FILE_EXISTS":
                                from pathlib import Path
                                path=action.arguments.get("destination",action.arguments.get("path",""))
                                recovery_action=Action(tool="filesystem.list" if Path(path).expanduser().is_dir() else "filesystem.read",arguments={"path":path},expected_result="Existing destination safely inspected without overwriting")
                                excluded_tools.add(action.tool)
                            break
                        if error.code=="REQUESTED_APP_MISMATCH" and task.intent and task.intent.target_app and action.tool in {"app.open","app.focus"}:
                            task.retry_count+=1
                            if task.retry_count>2:raise
                            recovery_action=Action(tool="app.open",arguments={"name":task.intent.target_app},expected_result=f"{task.intent.target_app} is active")
                            excluded_tools.discard("app.open")
                            feedback=f"The requested application is {task.intent.target_app}. Open or focus that exact application before handling its target object."
                            break
                        if error.code=="SCOPE_CONFIRMATION_REQUIRED":
                            managed.pending_action=action;managed.confirm_event.clear();managed.confirmed=False
                            await managed.update(TaskStatus.WAITING_FOR_CONFIRMATION,"Requesting access to an additional folder",confirmation={"tool":action.tool,"arguments":action.arguments,"scope_extension":True},message=error.message)
                            await managed.confirm_event.wait();managed.check()
                            managed.pending_action=None
                            if managed.skip_requested:managed.skip_requested=False;feedback="The user skipped this scope request.";break
                            if not managed.confirmed:raise asyncio.CancelledError
                            for key in ("path","destination"):
                                if key in action.arguments:
                                    from pathlib import Path
                                    path=Path(action.arguments[key]).expanduser().resolve()
                                    task.allowed_scope["filesystem_roots"].append(str(path if path.is_dir() else path.parent))
                            confirmed=False;continue
                        if not self.retry.may_retry(action,attempts):raise
                        attempts+=1;task.retry_count+=1;feedback=error.message
                        await managed.update(TaskStatus.OBSERVING,"Checking a recoverable failure",retry_count=task.retry_count)
                        observation=await self.executor.vision.observe(self.settings) if not self.settings.simulation else observation
                        continue
                    managed.last_result=result
                    trace["last_result"] = {"success": result.success, "simulated": result.simulated, "error": result.error}
                    await managed.update(TaskStatus.VERIFYING,"Checking the result")
                    after=observation if self.settings.simulation else await self.executor.vision.observe(self.settings)
                    if action.tool in {"app.open","app.focus","browser.open"} and not self.settings.simulation:
                        expected_app = task.intent.target_app if task.intent and task.intent.target_app else str(action.arguments.get("name") or action.arguments.get("application") or "")
                        expected_bundle = task.intent.target_bundle_id if task.intent else ""
                        for _ in range(10):
                            await asyncio.sleep(0.15)
                            after=await self.executor.vision.observe(self.settings)
                            if after.active_app and self._app_matches(expected_app, after.active_app, after.active_bundle_id, expected_bundle):break
                    verification=await self.executor.verification.verify(action,result,observation,after)
                    managed.verification=verification;task.last_observation=after
                    trace["screen_state"] = self._trace_observation(after)
                    trace["verification_state"] = {"success": bool(verification.get("success")), "reason": safe_text(str(verification.get("reason", "")))[:1000]}
                    if not verification["success"]:
                        if action.tool=="app.open" and attempts<2 and self.retry.may_retry(action,attempts):
                            # Launch may succeed before macOS transfers focus.
                            # Activate the same resolved app through the shared
                            # executor instead of silently opening another app.
                            focus_action=Action(tool="app.focus",arguments={"name":action.arguments["name"]},expected_result=f"{action.arguments['name']} is active")
                            await self.executor.execute(task,focus_action,after,simulation=self.settings.simulation,kill=managed.kill,confirmed=confirmed)
                            attempts+=1;task.retry_count+=1;observation=after
                            continue
                        if self.retry.may_retry(action,attempts):attempts+=1;task.retry_count+=1;observation=after;continue
                        # No blind repeat of a consequential action. Replan against its actual outcome.
                        feedback=f"Verification failed for {action.tool}: {verification['reason']}. Do not repeat a consequential action blindly."
                        task.retry_count+=1
                        if task.retry_count>2:raise ComputerError("VERIFICATION_FAILED",feedback)
                        excluded_tools.add(action.tool)
                        break
                    evidence={"tool":action.tool,"arguments":action.arguments,"result":result.data,"verified":True,"verification":verification,"simulated":result.simulated,
                        "observed":{"active_app":after.active_app,"active_bundle_id":after.active_bundle_id,"current_url":after.current_url or result.data.get("url",""),"visible_text":after.visible_text[:1500],"fingerprint":after.fingerprint}}
                    if action.tool in {"ui.click","mouse.click"}:
                        target=next((element for element in observation.elements if element.id==action.arguments.get("element_id") or action.tool=="mouse.click" and element.x<=action.arguments["x"]<=element.x+element.width and element.y<=action.arguments["y"]<=element.y+element.height and element.role=="AXButton"),None)
                        if target:evidence["target_label"]=target.label
                    managed.evidence.append(evidence);verified_count+=1;task.step_index+=1
                    replan_attempts=0
                    if task.intent and task.intent.intended_action=="open_folder" and action.tool in {"app.open","app.focus"} and task.intent.target_object:
                        from pathlib import Path
                        recovery_action=Action(tool="filesystem.open",arguments={"path":str(Path.home()/task.intent.target_object)},expected_result=f"{task.intent.target_object} is open in Finder")
                        feedback=f"The requested application is open. Open the exact {task.intent.target_object} folder now; do not treat focusing the app as completion."
                    await managed.update(TaskStatus.VERIFYING,"Step verified" if not self.settings.simulation else "[SIMULATION] Step simulated",verification=verification,
                        last_result={"success":result.success,"simulated":result.simulated},completed_actions=[e["tool"].replace("."," ") for e in managed.evidence])
                    review=await self.completion_review(task,managed.evidence,feedback="\n".join(managed.user_updates))
                    if review["complete"]:
                        return await self.finish(managed,verified_count)
                    feedback="Completion review found remaining work: "+review["message"];break
            raise ComputerError("STEP_LIMIT","The maximum task step count was reached. I stopped safely.")
        except asyncio.CancelledError:
            managed.kill.stop();await managed.update(TaskStatus.CANCELLED,"Stopped");await self._save(task,"Stopped; no further actions executed.");raise
        except (ComputerError,LLMError,OSError,asyncio.TimeoutError) as error:
            code=getattr(error,"code","COMPUTER_ACTION_FAILED");message=getattr(error,"message","A local computer action failed; I stopped safely.")
            task.metadata["error_code"]=code
            task.metadata["failure"]={
                "category":code,"current_step":task.current_step,
                "requested_application":task.intent.target_app if task.intent else "",
                "resolved_application":task.last_observation.active_app if task.last_observation else "",
                "tool":task.last_action.tool if task.last_action else "",
                "recovery_attempts":task.retry_count,"final_reason":safe_text(message),
                "screen_state":{
                    "active_app":task.last_observation.active_app if task.last_observation else "",
                    "window_title":safe_text(task.last_observation.window_title) if task.last_observation else "",
                    "fingerprint":task.last_observation.fingerprint if task.last_observation else "",
                },
            }
            trace.update({
                "task_id": task.id,
                "current_step": safe_text(task.current_step),
                "retry_count": task.retry_count,
                "final_error_code": code,
                "final_error": safe_text(message),
                "resolved_application": safe_text(task.last_observation.active_app if task.last_observation else ""),
            })
            task.metadata["pipeline_trace"] = trace
            await managed.update(TaskStatus.FAILED,safe_text(message),code=code)
            await self._save(task,safe_text(message));return task,"I stopped safely. "+safe_text(message)
        except Exception as error:
            task.metadata["error_code"]="INTERNAL_COMPUTER_ERROR"
            tool=task.last_action.tool if task.last_action else "planning"
            requested=task.intent.target_app if task.intent else "the requested app"
            message=f"The {tool} step for {requested or 'this task'} could not be completed ({type(error).__name__}); I stopped safely and preserved the task state."
            task.metadata["failure"]={"category":"INTERNAL_ERROR","exception_type":type(error).__name__,"current_step":task.current_step,"tool":tool,
                "requested_application":requested,"resolved_application":task.last_observation.active_app if task.last_observation else "","recovery_attempts":task.retry_count,"final_reason":message}
            trace.update({"task_id": task.id, "current_step": safe_text(task.current_step), "retry_count": task.retry_count,
                "final_error_code": "INTERNAL_COMPUTER_ERROR", "final_error": safe_text(message), "selected_tool": tool,
                "requested_application": safe_text(requested), "resolved_application": safe_text(task.last_observation.active_app if task.last_observation else "")})
            task.metadata["pipeline_trace"] = trace
            await managed.update(TaskStatus.FAILED,message,code="INTERNAL_COMPUTER_ERROR")
            await self._save(task,message);return task,message
        finally:
            try:
                if timeout_monitor:timeout_monitor.cancel();await asyncio.gather(timeout_monitor,return_exceptions=True)
                self.last_task=task;self.last_evidence=managed.evidence[-12:]
                if self.escape_monitor:self.escape_monitor.cancel();await asyncio.gather(self.escape_monitor,return_exceptions=True);self.escape_monitor=None
                if not self.settings.simulation:
                    await asyncio.gather(asyncio.wait_for(self.executor.keyboard.release_all(),2),asyncio.wait_for(self.executor.mouse.release_all(),2),return_exceptions=True)
            finally:
                managed.pending_action=None;managed.confirmed=False
                await self.manager.release(managed)
                self.manager.active.pop(task.id,None)
                if self.manager.running_id is None:self.owner=None

    async def watch_timeout(self,managed):
        while managed.task.status not in {TaskStatus.COMPLETED,TaskStatus.FAILED,TaskStatus.CANCELLED}:
            managed.check()
            remaining=self.settings.task_timeout_seconds-(time.monotonic()-managed.created_monotonic)
            if remaining>0:await asyncio.sleep(min(remaining,1));continue
            if managed.pause_event.is_set():
                managed.pause_event.clear()
                await managed.update(TaskStatus.PAUSED,"This task is taking longer than expected. Continue or Stop.",code="TASK_TIMEOUT")
                if not self.settings.simulation:
                    try:await self.executor.keyboard.release_all()
                    except (ComputerError,OSError):pass
            await managed.pause_event.wait()

    async def completion_review(self,task,evidence,*,feedback=""):
        goal=self.effective_goal(task.goal,feedback)
        intent=task.intent or parse_goal(task.goal,feedback)
        if self.settings.simulation:
            verified={item.get("tool") for item in evidence if item.get("verified")}
            if intent.intended_action == "open_application" and verified.intersection({"app.open", "app.focus"}):
                return {"complete":True,"message":""}
            if intent.intended_action == "search" and verified.intersection({"browser.search", "browser.navigate"}):
                return {"complete":True,"message":""}
            if intent.intended_action == "open_folder" and "filesystem.open" in verified:
                return {"complete":True,"message":""}
        if not self.settings.simulation and intent.target_app:
            app_verified=any(
                item.get("verified") and self._app_matches(intent.target_app, item.get("observed",{}).get("active_app", ""), item.get("observed",{}).get("active_bundle_id", ""), intent.target_bundle_id)
                for item in evidence
            )
            if not app_verified:
                return {"complete":False,"message":f"{intent.target_app} has not been observed as the active requested application."}
        if not self.settings.simulation and intent.intended_action == "search":
            search_verified=any(item.get("verified") and item.get("tool")=="browser.search" for item in evidence)
            if not search_verified:
                return {"complete":False,"message":"The requested search has not been submitted and verified in the requested application."}
            if intent.target_app != "Google Chrome" and not any(
                item.get("verified") and self._app_matches(intent.target_app,item.get("observed",{}).get("active_app", ""), item.get("observed",{}).get("active_bundle_id", ""), intent.target_bundle_id)
                and intent.target_object.casefold() in item.get("observed",{}).get("visible_text", "").casefold()
                for item in evidence
            ):
                return {"complete":False,"message":f"The search results have not been observed in {intent.target_app}."}
        if not self.settings.simulation and intent.intended_action in {"find_contact","send_message"}:
            if not entity_is_verified(intent,evidence):
                return {"complete":False,"message":f"The exact contact {intent.target_object or 'requested contact'} has not been resolved."}
            if intent.intended_action == "send_message":
                if intent.missing_information:
                    return {"complete":False,"message":f"I still need the message text for {intent.target_object}."}
                if not any(item.get("verified") and item.get("tool") in {"ui.type","browser.type","keyboard.type_text"} for item in evidence):
                    return {"complete":False,"message":"The requested message has not been typed and verified."}
                if intent.requested_content and not any(
                    item.get("verified") and item.get("arguments",{}).get("text") == intent.requested_content
                    for item in evidence
                ):
                    return {"complete":False,"message":"The exact requested message text has not been verified in the composer."}
                if not task.metadata.get("final_action_confirmed"):
                    return {"complete":False,"message":"The final Send action still requires confirmation."}
                if not any(item.get("verified") and self._is_send_evidence(item) for item in evidence):
                    return {"complete":False,"message":"The message has not been sent and verified in the conversation."}
        if re.match(r"^(?:nova[, ]+)?(?:delete|remove)\b",goal,re.I) and re.search(r"\b(?:file|folder|directory)\b|\b[\w.-]+\.(?:txt|md|csv)\b",goal,re.I) and not any(e["tool"]=="filesystem.delete" for e in evidence):
            return {"complete":False,"message":"The requested deletion has not occurred. Propose the exact filesystem.delete action; it must wait for user confirmation."}
        if re.search(r"\b(?:take|capture|grab|get) (?:an? |the )?screenshot\b|^screenshot\b",goal,re.I) and not any(e["tool"]=="screen.capture" for e in evidence):
            return {"complete":False,"message":"An actual on-demand screenshot has not been captured and processed. Use screen.capture."}
        if "textedit" in goal.casefold() and re.search(r"\bnew document\b",goal,re.I) and not any(
            e["tool"]=="keyboard.hotkey" and e["arguments"].get("key","").casefold()=="n" and "command" in e["arguments"].get("modifiers",[])
            or e["tool"] in {"ui.click","mouse.click"} and e.get("target_label","").casefold()=="new document" for e in evidence):
            return {"complete":False,"message":"A new TextEdit document has not been independently created. Use Command-N or the observed New Document button before typing."}
        if re.search(r"^type\s|\b(?:and|then)\s+type\s",goal,re.I) and not any(e["tool"] in {"keyboard.type_text","ui.type","browser.type"} for e in evidence):
            return {"complete":False,"message":"The requested typing has not been independently verified. Focus a nonsecure editable field and type the requested text."}
        review=await self.planner.verify_goal(task,evidence,self.settings,feedback=feedback)
        # Deterministic checks override an overly optimistic model completion.
        from urllib.parse import urlparse
        wanted_domain="youtube.com" if re.search(r"\b(?:open|go to|search)\b.*\byoutube\b",goal,re.I) else None
        if not self.settings.simulation and wanted_domain and not any(host==wanted_domain or host.endswith("."+wanted_domain) for e in evidence if (host:=urlparse(e.get("observed",{}).get("current_url","")).hostname)):
            return {"complete":False,"message":"The requested YouTube page has not been verified. Navigate to https://www.youtube.com and read its DOM."}
        observed_urls={url for e in evidence if e["tool"] in {"browser.search","browser.read"} for link in e.get("result",{}).get("links",[]) for url in (link.get("url"),link.get("displayed_url")) if url}
        filenames=re.findall(r"\b[\w.-]+\.txt\b",goal,re.I)
        from pathlib import Path
        few_requested=bool(re.search(r"\b(?:few|first (?:three|3)|three|3)\b",goal,re.I))
        existing_saved=any(e["tool"]=="filesystem.read" and (not filenames or Path(e["arguments"]["path"]).name in filenames)
            and (urls:=re.findall(r"https?://[^\s<>)]+",e.get("result",{}).get("content",""))) and (not few_requested or len(set(urls))>=3) and all(url.rstrip(".,") in observed_urls for url in urls) for e in evidence)
        if re.search(r"\b(?:save|write|create a text file)\b",goal,re.I) and re.search(r"\b(?:results|research|tools)\b",goal,re.I) and not (any(e["tool"]=="filesystem.write" for e in evidence) or existing_saved):
            return {"complete":False,"message":"Research results have not been written and verified. Complete the requested file-writing step."}
        return review

    @staticmethod
    def _trace_intent(intent):
        if not intent:
            return {}
        result=intent.model_dump()
        return {key:(safe_text(value)[:1600] if isinstance(value,str) else value) for key,value in result.items()}

    @staticmethod
    def _trace_action(action):
        if not action:
            return {}
        arguments={}
        for key,value in (action.arguments or {}).items():
            if isinstance(value,str):arguments[key]=safe_text(value)[:500]
            elif isinstance(value,(str,int,float,bool)) or value is None:arguments[key]=value
            else:arguments[key]=safe_text(str(value))[:500]
        return {"tool":safe_text(action.tool)[:100],"arguments":arguments,"expected_result":safe_text(action.expected_result)[:400]}

    @staticmethod
    def _trace_observation(observation):
        if not observation:
            return {}
        focused=observation.focused_element or {}
        return {"active_app":safe_text(observation.active_app)[:200],"active_bundle_id":safe_text(observation.active_bundle_id)[:200],
            "window_title":safe_text(observation.window_title)[:300],"source":safe_text(observation.source)[:80],
            "blocked_reason":safe_text(observation.blocked_reason or "")[:300],"fingerprint":safe_text(observation.fingerprint)[:200],
            "element_count":len(observation.elements),"focused_editable":bool(focused.get("editable")),"focused_secure":bool(focused.get("secure"))}

    @staticmethod
    def effective_goal(original,feedback):
        goal=original
        for instruction in feedback.splitlines():
            if re.search(r"\b(?:instead|actually|skip|do not|don't|rather)\b",instruction,re.I):goal=instruction
            else:goal+="\n"+instruction
        goal=re.sub(r"\b(?:instead of|rather than|not)\s+[^\s,.!?;]+","",goal,flags=re.I)
        return re.sub(r"\b(?:do not|don't|skip)\s+(?:the\s+)?\w+","",goal,flags=re.I)

    @staticmethod
    def _app_matches(expected, actual, active_bundle_id="", expected_bundle_id=""):
        expected=expected.casefold();actual=(actual or "").casefold()
        return bool(actual) and (expected in actual or actual in expected or expected=="google chrome" and actual=="chrome" or expected_bundle_id and active_bundle_id==expected_bundle_id)

    @staticmethod
    def _is_final_send_action(action, observation):
        if action.tool not in {"ui.click","mouse.click","browser.click","keyboard.press"}:
            return False
        if action.tool=="keyboard.press":
            return action.arguments.get("key","").casefold() in {"enter","return"}
        element_id=action.arguments.get("element_id")
        for element in (observation.elements if observation else []):
            if element_id and element.id==element_id:
                return bool(re.search(r"\b(?:send|submit|post)\b",element.label,re.I))
        return False

    @staticmethod
    def _is_send_evidence(item):
        result=item.get("result",{})
        observed=item.get("observed",{})
        text=" ".join(str(value) for value in (result.get("visible_text", ""),observed.get("visible_text", ""),result.get("status", "")))
        return bool(re.search(r"\b(?:sent|delivered|submitted|posted)\b",text,re.I))

    async def finish(self,managed,verified_count):
        await managed.wait_if_paused()
        summary=self._summary(managed);task=managed.task
        task.metadata["verified_steps"]=verified_count
        connector_response=self._connector_response(managed)
        if connector_response:
            task.metadata["assistant_response"]=connector_response
        await managed.update(TaskStatus.COMPLETED,"Simulation finished" if self.settings.simulation else "Done",summary=summary)
        await self._save(task,summary);return task,summary

    def _summary(self,managed):
        prefix="[SIMULATION] No real actions performed. Would complete: " if self.settings.simulation else "Verified: "
        lines=[self.action_summary(type("A",(),{"tool":e["tool"],"arguments":e["arguments"]})()) for e in managed.evidence if e.get("verified")]
        # Only actual observed links appear in the result; not model-invented websites.
        links=[]
        for evidence in managed.evidence:
            if evidence["tool"] not in {"browser.search","browser.read"}:continue
            for link in evidence.get("result",{}).get("links",[])[:5]:
                if link.get("title") and link.get("url"):links.append(link["title"]+" — "+(link.get("displayed_url") or link["url"]))
        screen=next((e["result"] for e in reversed(managed.evidence) if e["tool"]=="screen.capture" and not e.get("simulated")),None)
        visible=("\nVisible in "+screen.get("active_app","the active app")+":\n"+safe_text(screen.get("visible_text",""))[:1500]) if screen else ""
        readout=""
        for item in managed.evidence:
            data=item["result"]
            if item["tool"]=="app.active" and data.get("active_app"):readout="\nActive app: "+safe_text(data["active_app"])
            elif item["tool"]=="filesystem.list" and re.search(r"\blist\b",managed.task.goal,re.I):
                names=[safe_text(entry["name"]) for entry in data.get("entries",[])[:20]]
                readout="\nEntries observed: "+", ".join(names)+(f" (showing {len(names)} of {len(data['entries'])} observed entries)" if len(data.get("entries",[]))>len(names) else "")
            elif item["tool"]=="filesystem.read" and re.match(r"^(?:nova[, ]+)?read\b",managed.task.goal,re.I):readout="\nRead content: "+safe_text(data.get("content",""))[:1500]
            elif item["tool"]=="clipboard.read":readout="\nClipboard: "+safe_text(data.get("text",""))[:1500]
        return prefix+" ".join(lines[-8:])+visible+readout+("\n"+"\n".join(dict.fromkeys(links))[:1800] if links else "")

    @staticmethod
    def _connector_response(managed):
        reads=[item.get("result",{}) for item in managed.evidence if item.get("tool")=="connector.google.gmail.read" and item.get("verified")]
        if not reads:
            return ""
        latest=reads[-1]
        subject=safe_text(str(latest.get("subject") or "(no subject)"))[:300]
        sender=safe_text(str(latest.get("from") or "unknown sender"))[:300]
        text=safe_text(str(latest.get("body") or latest.get("snippet") or "No message preview was returned."))
        text=re.sub(r"\s+"," ",text).strip()[:1200]
        return f"I read the latest Gmail message.\nSubject: {subject}\nFrom: {sender}\nSummary: {text}"

    @staticmethod
    def action_summary(action):
        summaries={"app.open":"Opening {name}.","app.focus":"Focusing {name}.","browser.open":"Opening {application}.","browser.search":"Searching {query} in {application}.","browser.navigate":"Opening the page in {application}.","browser.read":"Reading visible results.","filesystem.create_folder":"Creating {path}.","filesystem.write":"Writing {path}.","filesystem.move":"Moving a file.","clipboard.write":"Copying to the clipboard.","screen.capture":"Looking at the screen.","keyboard.type_text":"Typing requested text.","ui.type":"Typing requested text.","connector.google.gmail.search":"Searching Gmail.","connector.google.gmail.read":"Reading the selected Gmail message."}
        return summaries.get(action.tool,"Using "+action.tool+".").format(**action.arguments)

    async def confirm(self,identifier,confirmed):
        managed=self.manager.get(identifier)
        if not managed or managed.task.status!=TaskStatus.WAITING_FOR_CONFIRMATION:raise ComputerError("NO_PENDING_APPROVAL","This task is not waiting for approval.")
        managed.confirmed=confirmed;managed.confirm_event.set();return managed.task
    async def pause(self,identifier):
        managed=self.manager.get(identifier)
        if not managed:raise LookupError("Task not active")
        await managed.pause()
        await self.stop_previews(identifier)
        if not self.settings.simulation:
            try:await self.executor.keyboard.release_all()
            except (ComputerError,OSError):pass
        return managed.task
    async def resume(self,identifier,feedback=""):
        managed=self.manager.get(identifier)
        if not managed:raise LookupError("Task not active")
        if has_secret(feedback):raise ComputerError("CREDENTIAL_BLOCKED","Task follow-ups cannot include credentials; enter them yourself.")
        feedback=safe_text(feedback)[:2000]
        if feedback:
            managed.task.intent=parse_goal(managed.task.goal,feedback)
            managed.task.metadata["intent"]=managed.task.intent.model_dump()
        managed.feedback=feedback
        if feedback:managed.user_updates=(managed.user_updates+[feedback])[-5:]
        if feedback and managed.pending_action:
            managed.skip_requested=True;managed.confirm_event.set()
        await managed.resume();return managed.task
    async def stop(self,identifier):
        managed=self.manager.get(identifier)
        if not managed:raise LookupError("Task not active")
        await managed.stop();await self.stop_previews(identifier);return managed.task
    async def preview(self,identifier):
        managed=self.manager.get(identifier)
        if not managed or self.settings.simulation or managed.task.status not in {TaskStatus.PLANNING,TaskStatus.OBSERVING,TaskStatus.ACTING,TaskStatus.VERIFYING,TaskStatus.WAITING_FOR_CONFIRMATION}:
            raise ComputerError("PREVIEW_INACTIVE","A real active task is required for a preview.")
        runner=asyncio.current_task();self.preview_runners.setdefault(identifier,set()).add(runner)
        try:
            managed.check();image=await self.executor.vision.preview(self.settings);managed.check()
            if self.manager.get(identifier) is not managed or not managed.pause_event.is_set():raise ComputerError("PREVIEW_INACTIVE","Preview stopped with the task.")
            return image
        finally:
            runners=self.preview_runners.get(identifier,set());runners.discard(runner)
            if not runners:self.preview_runners.pop(identifier,None)
    async def stop_previews(self,identifier):
        runners=[runner for runner in self.preview_runners.get(identifier,set()) if runner is not asyncio.current_task()]
        for runner in runners:runner.cancel()
        if runners:await asyncio.gather(*runners,return_exceptions=True)
    async def stop_all(self):
        self.manager.draining=True
        try:
            tasks=list(self.manager.active.values())
            for managed in tasks:await managed.stop()
            for identifier in list(self.preview_runners):await self.stop_previews(identifier)
            runners=[managed.runner for managed in tasks if managed.runner and managed.runner is not asyncio.current_task()]
            if runners:await asyncio.gather(*runners,return_exceptions=True)
        finally:
            self.manager.draining=False
