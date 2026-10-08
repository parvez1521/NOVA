import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.computer.models import Action, ScreenObservation, ToolResult
from app.computer.intent import resolve_application


class VerificationEngine:
    def __init__(self, executor=None):self.executor=executor

    async def verify(self, action: Action, result: ToolResult, before: ScreenObservation | None, after: ScreenObservation | None) -> dict:
        if result.simulated:return {"success":True,"simulated":True,"reason":"Simulation only; no real action verified"}
        if not result.success:return {"success":False,"reason":"Tool reported failure"}
        tool,args,data=action.tool,action.arguments,result.data
        success=False;reason="The requested outcome could not be independently verified."
        if tool in {"app.open","app.focus"}:
            active=(after.active_app if after else "").casefold();active_bundle=(after.active_bundle_id if after else "").casefold();target=resolve_application(args["name"]);expected=(target[0] if target else args["name"]).casefold();expected_bundle=(target[1] if target else "").casefold()
            success=bool(active) and ((active_bundle==expected_bundle) if expected_bundle else (expected in active or active in expected or expected=="chrome" and active=="google chrome"))
            reason=f"Active application: {active} ({active_bundle or 'bundle unavailable'})"
        elif tool=="app.quit":
            apps=(await self.executor.apps.list())["applications"]
            success=not any(app["name"].casefold()==args["name"].casefold() for app in apps);reason="Running applications checked"
        elif tool=="app.find_entity":
            success=bool(data.get("found")) and bool(data.get("matches"));reason="Exact visible application entity matches observed"
        elif tool.startswith("filesystem."):
            path=Path(args.get("path","")).expanduser().resolve()
            if tool=="filesystem.create_folder":success=path.is_dir()
            elif tool=="filesystem.write":success=path.is_file() and path.read_text(encoding="utf-8")==args["content"]
            elif tool in {"filesystem.move","filesystem.rename"}:success=not path.exists() and Path(args["destination"]).expanduser().exists()
            elif tool=="filesystem.copy":success=Path(args["destination"]).expanduser().is_file() and path.read_bytes()==Path(args["destination"]).expanduser().read_bytes()
            elif tool=="filesystem.delete":success=not path.exists()
            elif tool in {"filesystem.list","filesystem.read"}:success=bool(data.get("path"))
            elif tool=="filesystem.open":success=bool(after and after.active_app)
            reason="Filesystem state independently checked"
        elif tool=="clipboard.write":success=(await self.executor.clipboard.read()).get("text")==args["text"];reason="Clipboard readback checked"
        elif tool=="clipboard.read":success="text" in data;reason="Clipboard text read"
        elif tool.startswith("browser."):
            page=await self.executor.browser.read_page()
            if page.get("challenge") or page.get("credential_page"):return {"success":False,"reason":"Security/authentication requires the user"}
            requested_app=args.get("application","Google Chrome")
            if tool=="browser.open" and requested_app.casefold()!="google chrome":
                success=bool(after and after.active_app and requested_app.casefold() in after.active_app.casefold())
            elif tool in {"browser.navigate","browser.search"} and requested_app.casefold()!="google chrome":
                success=bool(after and after.active_app and requested_app.casefold() in after.active_app.casefold() and data.get("submitted"))
            elif tool=="browser.navigate":
                expected,actual=urlparse(args["url"]),urlparse(page.get("url",""))
                success=expected.hostname==actual.hostname and expected.path.rstrip("/")==actual.path.rstrip("/") and all(parse_qs(actual.query).get(key)==values for key,values in parse_qs(expected.query).items())
            elif tool=="browser.search":
                url=urlparse(page.get("url",""));parameter="search_query" if args.get("site")=="youtube" else "q"
                expected_host="youtube.com" if parameter=="search_query" else "google.com"
                success=bool(page.get("links")) and bool(page.get("visible_text")) and (url.hostname==expected_host or (url.hostname or "").endswith("."+expected_host)) and parse_qs(url.query).get(parameter)==[args["query"]]
            elif tool=="browser.type":
                if requested_app.casefold()!="google chrome":
                    success=bool(data.get("typed")) and bool(after and after.active_app and requested_app.casefold() in after.active_app.casefold())
                else:
                    success=any(e.get("id")==args["element_id"] and e.get("editable") and not e.get("secure") and args["text"] in e.get("value","") for e in page.get("elements",[]))
            elif tool=="browser.scroll":success=data.get("before")!=data.get("after")
            elif tool in {"browser.click","browser.back","browser.forward"}:success=bool(before and after and before.fingerprint!=after.fingerprint)
            elif tool=="browser.find_text":success=bool(data.get("found"))
            else:success=bool(page.get("url"))
            reason="Fresh browser DOM readback checked"
        elif tool in {"keyboard.type_text","ui.type"}:
            if tool=="ui.type" and args.get("element_id") not in {None,"focused"}:
                success=bool(after and any(element.id==args["element_id"] and element.editable and not element.secure and args["text"] in element.value for element in after.elements))
            else:
                focused=after.focused_element if after else None
                success=bool(focused and focused.get("editable") and not focused.get("secure") and args["text"] in focused.get("value",""))
            reason="Target editable-field content independently read back"
        elif tool=="screen.capture":success=data.get("source") in {"native_ocr","local_vision"} and not data.get("blocked_reason") and (bool(data.get("elements")) or bool(data.get("visible_text")));reason="Temporary image processed locally"
        elif tool in {"app.active","apps.list","terminal.run"}:success=bool(data);reason="Read-only result observed"
        elif tool=="mouse.move":
            position=await self.executor.native.call("mouse.position")
            success=abs(position.get("x",-100)-args["x"])<=3 and abs(position.get("y",-100)-args["y"])<=3;reason="Actual pointer location checked"
        elif tool=="keyboard.key_up":success=bool(data.get("pressed"));reason="Key released"
        else:success=bool(before and after and before.fingerprint!=after.fingerprint);reason="Observed UI changed after action"
        return {"success":success,"reason":reason}


class RetryEngine:
    # Only idempotent reads/navigation may be repeated. Never repeat sends, clicks, writes or moves blindly.
    SAFE_RETRY={"app.open","app.focus","browser.open","browser.navigate","browser.search","browser.read","filesystem.list","filesystem.read","screen.capture","apps.list","app.active"}
    def may_retry(self, action: Action, attempts: int) -> bool:return action.tool in self.SAFE_RETRY and attempts<2
