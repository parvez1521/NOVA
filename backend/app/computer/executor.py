"""Only this executor may invoke tools: schema → scope → risk → approval → reality."""

import asyncio
import inspect
import json
import re
import time
from pathlib import Path
from typing import get_type_hints

from pydantic import ConfigDict, create_model

from app.computer.browser import BrowserProvider
from app.computer.controllers import ApplicationController, ClipboardProvider, FilesystemProvider, KeyboardController, MouseController
from app.computer.intent import resolve_application
from app.computer.models import Action, ComputerError, ComputerSettings, ScreenObservation, Task, ToolResult, UIElement
from app.computer.observation import ComputerVision
from app.computer.permissions import ConfirmationRequired, PermissionManager
from app.computer.scope import ScopeManager
from app.computer.verification import VerificationEngine
from app.database.privacy import has_secret
from app.tools.base import PermissionLevel, ToolDefinition
from app.tools.registry import ToolRegistry
from app.tools.safety import CommandSafetyLayer


class TaskExecutor:
    def __init__(self, project_root: Path, settings: ComputerSettings, connector_manager=None):
        from app.computer.native import NativeBridge
        native=NativeBridge(project_root);self.native=native;self.settings=settings;self.registry=ToolRegistry();self.connector_manager=connector_manager
        self.apps=ApplicationController(native);self.mouse=MouseController(native);self.keyboard=KeyboardController(native)
        self.files=FilesystemProvider();self.clipboard=ClipboardProvider(native);self.browser=BrowserProvider(native,project_root);self.vision=ComputerVision(native,self.browser)
        self.permissions=PermissionManager(settings);self.scope=ScopeManager();self.verification=VerificationEngine(self)
        self._register()

    async def observe_screen(self, active_window: bool=False):
        return (await self.vision.observe(self.settings,force_screen=True,active_window=active_window)).model_dump(exclude={"screenshot_path"})

    async def terminal(self, arguments: list[str]):
        from app.computer.native import process
        validated=CommandSafetyLayer().validate(arguments)
        output=await process(*validated,timeout=15)
        from app.database.privacy import safe_text
        return {"output":safe_text(output.decode(errors="replace")[:8000]),"arguments":validated}

    def _register(self):
        safe=PermissionLevel.SAFE;confirm=PermissionLevel.CONFIRM;high=PermissionLevel.HIGH_RISK_CONFIRM
        entries={
            "apps.list":(safe,"application",self.apps.list),"app.active":(safe,"application",self.apps.active),"app.find_entity":(safe,"application",self.find_entity),
            "app.open":(safe,"application",self.apps.open),"app.focus":(safe,"application",self.apps.focus),"app.quit":(confirm,"application",self.apps.quit),
            "mouse.move":(safe,"accessibility",self.mouse.move),"mouse.click":(confirm,"accessibility",self.mouse.click),"mouse.double_click":(confirm,"accessibility",self.mouse.double_click),"mouse.right_click":(confirm,"accessibility",self.mouse.right_click),"mouse.drag":(confirm,"accessibility",self.mouse.drag),"mouse.scroll":(safe,"accessibility",self.mouse.scroll),
            "keyboard.press":(confirm,"accessibility",self.keyboard.press),"keyboard.hotkey":(confirm,"accessibility",self.keyboard.hotkey),"keyboard.type_text":(confirm,"accessibility",self.keyboard.type_text),"keyboard.key_down":(confirm,"accessibility",self.keyboard.key_down),"keyboard.key_up":(safe,"accessibility",self.keyboard.key_up),
             "ui.click":(confirm,"accessibility",self.vision.accessibility.click),"ui.type":(safe,"accessibility",self.vision.accessibility.type),
            "filesystem.list":(safe,"filesystem",self.files.list),"filesystem.read":(safe,"filesystem",self.files.read),"filesystem.create_folder":(safe,"filesystem",self.files.mkdir),"filesystem.write":(safe,"filesystem",self.files.write),"filesystem.move":(confirm,"filesystem",self.files.move),"filesystem.rename":(confirm,"filesystem",self.files.move),"filesystem.copy":(safe,"filesystem",self.files.copy),"filesystem.delete":(high,"filesystem",self.files.delete),"filesystem.open":(safe,"filesystem",self.files.open),
            "clipboard.read":(confirm,"clipboard",self.clipboard.read),"clipboard.write":(safe,"clipboard",self.clipboard.write),
             "browser.open":(safe,"browser",self.browser.open),"browser.navigate":(safe,"browser",self.browser.navigate),"browser.search":(safe,"browser",self.browser.search),"browser.read":(safe,"browser",self.browser.read_page),"browser.click":(confirm,"browser",self.browser.click),"browser.type":(safe,"browser",self.browser.type),"browser.scroll":(safe,"browser",self.browser.scroll),"browser.back":(safe,"browser",self.browser.back),"browser.forward":(safe,"browser",self.browser.forward),"browser.reload":(safe,"browser",self.browser.reload),"browser.find_text":(safe,"browser",self.browser.find_text),
            "screen.capture":(safe,"screen",self.observe_screen),"terminal.run":(confirm,"terminal",self.terminal),
        }
        descriptions={
            "app.find_entity":"Find exact visible UI entity matches in the explicitly requested active application; never choose a similar match.",
            "filesystem.create_folder":"Create a directory, never a file. Do not use this tool for a .txt file.",
            "filesystem.write":"Create a NEW UTF-8 TEXT FILE at path containing content. This is how to create results.txt. Never overwrites.",
            "filesystem.read":"Read the actual content of an existing UTF-8 text file.",
            "filesystem.list":"List existing files and directories in path.",
            "browser.open":"Open or focus the explicitly requested browser application. Does not search or navigate.",
            "browser.search":"Search the web for query (site=web or youtube); returns visible text and actual link titles/URLs.",
            "browser.read":"Read the current browser page and its actual visible link titles/URLs.",
            "browser.navigate":"Navigate the browser to a specified public HTTP/HTTPS URL.",
        }
        for name,(permission,capability,method) in entries.items():
            signature=inspect.signature(method);hints=get_type_hints(method);fields={}
            for key,param in signature.parameters.items():
                fields[key]=(hints.get(key,str),... if param.default is inspect.Parameter.empty else param.default)
            model=create_model(name.replace(".","_")+"Args",__config__=ConfigDict(extra="forbid",strict=True),**fields)
            self.registry.register(ToolDefinition(name, descriptions.get(name,name.replace("."," ")+" using actual observed state"),model.model_json_schema(),permission,method),model,capability=capability,summary=name)
        if self.connector_manager and self.connector_manager.enabled and "google" in self.connector_manager.connectors:
            connector_entries={
                "connector.google.gmail.search": (safe, "connector", self.google_gmail_search),
                "connector.google.gmail.read": (safe, "connector", self.google_gmail_read),
                "connector.google.calendar.list": (safe, "connector", self.google_calendar_list),
                "connector.google.drive.search": (safe, "connector", self.google_drive_search),
            }
            connector_descriptions={
                "connector.google.gmail.search": "Search the authorized Gmail mailbox without changing messages.",
                "connector.google.gmail.read": "Read an authorized Gmail message returned by a verified Gmail search.",
                "connector.google.calendar.list": "List events from the authorized primary Google Calendar.",
                "connector.google.drive.search": "Search metadata for files in the authorized Google Drive.",
            }
            for name,(permission,capability,method) in connector_entries.items():
                signature=inspect.signature(method);hints=get_type_hints(method);fields={}
                for key,param in signature.parameters.items():
                    fields[key]=(hints.get(key,str),... if param.default is inspect.Parameter.empty else param.default)
                model=create_model(name.replace(".","_")+"Args",__config__=ConfigDict(extra="forbid",strict=True),**fields)
                self.registry.register(ToolDefinition(name,connector_descriptions[name],model.model_json_schema(),permission,method),model,capability=capability,summary=name)
        if self.connector_manager and self.connector_manager.enabled and "github" in self.connector_manager.connectors:
            connector_entries={
                "connector.github.profile": (safe, self.github_profile),
                "connector.github.repositories": (safe, self.github_repositories),
                "connector.github.search": (safe, self.github_search),
                "connector.github.issues": (safe, self.github_issues),
                "connector.github.pull_requests": (safe, self.github_pull_requests),
                "connector.github.read_file": (safe, self.github_read_file),
            }
            for name,(permission,method) in connector_entries.items():
                signature=inspect.signature(method);hints=get_type_hints(method);fields={key:(hints.get(key,str),... if param.default is inspect.Parameter.empty else param.default) for key,param in signature.parameters.items()}
                model=create_model(name.replace(".","_")+"Args",__config__=ConfigDict(extra="forbid",strict=True),**fields)
                self.registry.register(ToolDefinition(name,"Read authorized GitHub data through the shared connector executor.",model.model_json_schema(),permission,method),model,capability="connector",summary=name)
        if self.connector_manager and self.connector_manager.enabled and "notion" in self.connector_manager.connectors:
            connector_entries={
                "connector.notion.search": (safe, self.notion_search),
                "connector.notion.page": (safe, self.notion_page),
                "connector.notion.page_content": (safe, self.notion_page_content),
            }
            for name,(permission,method) in connector_entries.items():
                signature=inspect.signature(method);hints=get_type_hints(method);fields={key:(hints.get(key,str),... if param.default is inspect.Parameter.empty else param.default) for key,param in signature.parameters.items()}
                model=create_model(name.replace(".","_")+"Args",__config__=ConfigDict(extra="forbid",strict=True),**fields)
                self.registry.register(ToolDefinition(name,"Read authorized Notion data through the shared connector executor.",model.model_json_schema(),permission,method),model,capability="connector",summary=name)

    async def google_gmail_search(self, query: str, max_results: int = 5):
        return await self.connector_manager.execute("google", "gmail.search", {"query": query, "max_results": max_results})

    async def google_gmail_read(self, message_id: str):
        return await self.connector_manager.execute("google", "gmail.read", {"message_id": message_id})

    async def google_calendar_list(self, time_min: str = "", time_max: str = "", max_results: int = 10):
        return await self.connector_manager.execute("google", "calendar.list", {"time_min": time_min, "time_max": time_max, "max_results": max_results})

    async def google_drive_search(self, query: str, max_results: int = 10):
        return await self.connector_manager.execute("google", "drive.search", {"query": query, "max_results": max_results})

    async def github_profile(self):
        return await self.connector_manager.execute("github", "github.profile", {})

    async def github_repositories(self, max_results: int = 30):
        return await self.connector_manager.execute("github", "github.repositories", {"max_results": max_results})

    async def github_search(self, query: str, max_results: int = 10):
        return await self.connector_manager.execute("github", "github.search", {"query": query, "max_results": max_results})

    async def github_issues(self, owner: str, repo: str, state: str = "open", max_results: int = 20):
        return await self.connector_manager.execute("github", "github.issues", {"owner": owner, "repo": repo, "state": state, "max_results": max_results})

    async def github_pull_requests(self, owner: str, repo: str, state: str = "open", max_results: int = 20):
        return await self.connector_manager.execute("github", "github.pull_requests", {"owner": owner, "repo": repo, "state": state, "max_results": max_results})

    async def github_read_file(self, owner: str, repo: str, path: str, ref: str = ""):
        return await self.connector_manager.execute("github", "github.read_file", {"owner": owner, "repo": repo, "path": path, "ref": ref})

    async def notion_search(self, query: str = "", max_results: int = 20):
        return await self.connector_manager.execute("notion", "notion.search", {"query": query, "max_results": max_results})

    async def notion_page(self, page_id: str):
        return await self.connector_manager.execute("notion", "notion.page", {"page_id": page_id})

    async def notion_page_content(self, page_id: str, max_results: int = 100):
        return await self.connector_manager.execute("notion", "notion.page_content", {"page_id": page_id, "max_results": max_results})

    async def find_entity(self, application: str, query: str):
        target=resolve_application(application)
        requested=target[0] if target else application
        active=await self.apps.active()
        if target and not (
            active.get("bundle_id","").casefold()==target[1].casefold()
            or active.get("active_app","").casefold()==requested.casefold()
        ):
            raise ComputerError("REQUESTED_APP_MISMATCH",f"{requested} is not the active application.")
        observation=await self.vision.observe(self.settings)
        query_value=query.casefold().strip()
        matches=[
            {"id":element.id,"label":element.label,"role":element.role,"exact":element.label.casefold().strip()==query_value}
            for element in observation.elements
            if query_value and query_value in element.label.casefold()
        ]
        exact=[item for item in matches if item["exact"]]
        return {"application":requested,"query":query,"matches":exact or matches,"found":bool(exact or matches),"active_app":observation.active_app}

    @staticmethod
    def approval_key(action: Action, observation=None):
        return action.tool+":"+json.dumps(action.arguments,sort_keys=True)+":"+(observation.fingerprint if observation and action.tool.startswith(("mouse.","keyboard.","ui.","browser.click","browser.type")) else "")

    def validate_target(self, action: Action, observation: ScreenObservation | None):
        interactive=action.tool.startswith(("mouse.","keyboard.","ui.","browser.click","browser.type"))
        if not interactive:return
        if observation is None:raise ComputerError("OBSERVATION_REQUIRED","Observe the current UI before interacting.")
        if observation.blocked_reason:raise ComputerError(observation.blocked_reason,"Handle the authentication/security challenge yourself, then continue.")
        if action.tool=="browser.type" and str(action.arguments.get("application", "Google Chrome")).casefold()!="google chrome":
            # Native Safari/other-browser address-bar typing is validated by
            # BrowserProvider against the active bundle, not Chrome DOM IDs.
            return
        if observation.active_app.casefold()=="system settings":
            raise ComputerError("SECURITY_SETTINGS_BLOCKED","Change macOS privacy/security settings yourself; the agent will not interact with System Settings.")
        if action.tool.startswith(("keyboard.","ui.type")) and observation.active_app.casefold() in {"terminal","iterm2"}:
            raise ComputerError("KEYBOARD_TARGET_BLOCKED","Typing commands or settings through keyboard would bypass the safety layer.")
        if action.tool.startswith(("keyboard.","ui.type")):
            focused=observation.focused_element or {}
            if re.search(r"\b(?:terminal|shell prompt|command prompt|powershell)\b",focused.get("description","")+" "+focused.get("title",""),re.I):
                raise ComputerError("KEYBOARD_TARGET_BLOCKED","An embedded terminal cannot bypass the command safety layer.")
            if observation.active_app.casefold() in {"google chrome","chrome","safari","firefox","microsoft edge"} and action.tool!="keyboard.key_up":
                raise ComputerError("KEYBOARD_TARGET_BLOCKED","Use registered browser tools for browser input/navigation; native keyboard cannot bypass browser safety checks.")
        target_id=action.arguments.get("element_id")
        element=next((e for e in observation.elements if e.id==target_id),None) if target_id else None
        if target_id=="focused" and action.tool=="ui.type" and observation.focused_element:
            element=UIElement.model_validate({key:value for key,value in observation.focused_element.items() if key in UIElement.model_fields})
        if target_id and not element:raise ComputerError("ELEMENT_NOT_FOUND","The selected element is no longer present.")
        if action.tool=="ui.click" and element and observation.active_app.casefold()=="textedit" and element.label.casefold()=="new document":
            raise ComputerError("ACTION_INVALID","Use mouse.click on the observed New Document button so the visible dialog interaction can be independently verified.")
        if element and (element.secure or element.confidence<0.8):raise ComputerError("TARGET_UNCERTAIN","The target is secure or insufficiently confident. Please select it yourself.")
        if action.tool in {"ui.type","browser.type"} and element and not element.editable:
            raise ComputerError("TEXT_FIELD_NOT_FOCUSED","The observed target is not an editable text field.")
        if action.tool.startswith("mouse.") and "x" in action.arguments:
            points=[(action.arguments["x"],action.arguments["y"])]
            if action.tool=="mouse.drag":points.append((action.arguments["to_x"],action.arguments["to_y"]))
            for x,y in points:
                if not any(e.source in {"accessibility","ocr"} and e.confidence>=0.8 and not e.secure and e.width>0 and e.x<=x<=e.x+e.width and e.y<=y<=e.y+e.height for e in observation.elements):
                    raise ComputerError("TARGET_UNCERTAIN","Screen coordinates must match a confidently observed native UI element. Use browser element tools for DOM targets.")
        if action.tool in {"keyboard.type_text","ui.type"} and not element:
            focused=observation.focused_element or {}
            if not focused.get("editable") or focused.get("secure"):raise ComputerError("TEXT_FIELD_NOT_FOCUSED","Select a non-secure text field before typing.")

    @staticmethod
    def _canonical_app(value: str) -> str:
        from app.computer.intent import resolve_application
        target=resolve_application(value)
        return target[0] if target else value

    def validate_application_target(self, task: Task, action: Action) -> None:
        intent=task.intent
        if not intent or not intent.target_app:
            return
        requested=intent.target_app
        if action.tool in {"app.open","app.focus","app.find_entity"}:
            actual=self._canonical_app(str(action.arguments.get("name","")))
            if action.tool=="app.find_entity":
                actual=self._canonical_app(str(action.arguments.get("application","")))
            if actual.casefold()!=requested.casefold():
                raise ComputerError("REQUESTED_APP_MISMATCH",f"The user requested {requested}; do not substitute {actual or 'another application'}.")
        if action.tool.startswith("browser."):
            application=self._canonical_app(str(action.arguments.get("application","Google Chrome")))
            if application.casefold()!=requested.casefold():
                raise ComputerError("REQUESTED_APP_MISMATCH",f"The user requested {requested}; browser action targeted {application}.")

    async def execute(self, task: Task, action: Action, observation=None, *, simulation=False, kill=None, confirmed=False):
        started=time.perf_counter()
        if kill:kill.check()
        if action.tool.startswith("browser.") and task.intent and task.intent.target_app and "application" not in action.arguments:
            # The explicit goal contract wins over the browser tool default;
            # never silently route a Safari action through Chrome.
            action.arguments["application"] = task.intent.target_app
        self.validate_application_target(task,action)
        action.arguments=self.registry.validate(action.tool,action.arguments);entry=self.registry.get(action.tool)
        if has_secret(json.dumps(action.arguments)):raise ComputerError("CREDENTIAL_BLOCKED","Credentials cannot be passed to computer tools.")
        if action.tool=="terminal.run":CommandSafetyLayer().validate(action.arguments["arguments"])
        for key in ("path","destination"):
            if key in action.arguments and action.tool.startswith("filesystem."):
                self.scope.validate(task,action.arguments[key])
        if not simulation:self.validate_target(action,observation)
        permission=entry.definition.permission_level
        key=self.approval_key(action,observation)
        # Access switches always remain authoritative, even after approval.
        if not self.permissions.capability_allowed(entry.capability):raise ComputerError("ACCESS_DISABLED",f"Enable {entry.capability} access in Computer settings.")
        if not confirmed:self.permissions.require(task,permission,entry.capability)
        if simulation:
            return ToolResult(success=True,data={"would_execute":action.tool,"arguments":action.arguments},simulated=True),{"success":True,"simulated":True}
        if kill:kill.check()
        token=self.native.interaction_pid.set(observation.active_pid if observation else 0)
        visibility=self.native.visibility_mode.set(self.settings.action_visibility_mode)
        try:result=await entry.definition.execute(**action.arguments)
        finally:self.native.interaction_pid.reset(token);self.native.visibility_mode.reset(visibility)
        if kill:kill.check()
        return ToolResult(success=True,data=result,duration_ms=round((time.perf_counter()-started)*1000,2)),{}


Executor=TaskExecutor
