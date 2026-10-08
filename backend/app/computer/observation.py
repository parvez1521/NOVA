"""On-demand native AX → browser DOM → temporary screen/OCR observations."""

import hashlib
import json
import re
import tempfile
from pathlib import Path

from app.computer.models import ComputerError, ComputerSettings, ScreenObservation, UIElement
from app.database.privacy import has_secret, safe_text


_CHALLENGE = re.compile(r"verify\s+you\s+are\s+human|i(?:'m| am)\s+not\s+a\s+robot|checking\s+your\s+browser|unusual\s+traffic|security\s+verification|enter\s+(?:the\s+|your\s+)?(?:otp|verification\s+code|one.time\s+code)", re.I)


class MacAccessibilityProvider:
    def __init__(self,native):self.native=native
    async def permission_status(self):return await self.native.call("permissions")
    async def observe(self):return await self.native.call("observe")
    async def click(self,element_id: str):return await self.native.call("ax.click",element_id=element_id)
    async def type(self,text: str,element_id: str | None=None):return await self.native.call("ax.type",text=text,**({"element_id":element_id} if element_id else {}))


class ScreenProvider:
    def __init__(self,native):self.native=native;self.local_vision=None
    async def capture(self,*,active_window=False,settings=None):
        # Filename remains local/private and never reaches a planner or persisted task.
        with tempfile.TemporaryDirectory(prefix="nova-screen-") as directory:
            path=Path(directory)/"screen.png"
            frame=await self.native.call("screen.capture",path=str(path),active_window=active_window)
            result=await self.native.call("ocr",path=str(path),**{key:frame[key] for key in ("x","y","width","height") if key in frame})
            if not result.get("elements") and settings and settings.vision_enabled and settings.local_vision_model and self.local_vision:
                bounds=await self.native.call("permissions")
                result=await self.local_vision.inspect(path,settings.local_vision_model,bounds.get("screen_width",0),bounds.get("screen_height",0))
            return result


class ComputerVision:
    def __init__(self,native,browser):
        self.native=native;self.browser=browser
        self.accessibility=MacAccessibilityProvider(native);self.screen=ScreenProvider(native)

    async def preview(self,settings:ComputerSettings) -> bytes:
        if not settings.watch_nova or not settings.screen_access:
            raise ComputerError("LIVE_PREVIEW_DISABLED","Enable Watch NOVA and Screen Access explicitly to view a task preview.")
        preflight=settings.model_copy(update={"screen_access":False,"vision_enabled":False})
        before=await self.observe(preflight)
        if before.blocked_reason:raise ComputerError(before.blocked_reason,"The preview is hidden on authentication/security content.")
        # View-only local pixels: native OCR checks this exact temporary capture;
        # no model receives the image and nothing is retained after the response.
        with tempfile.TemporaryDirectory(prefix="nova-preview-") as directory:
            path=Path(directory)/"preview.png"
            frame=await self.native.call("screen.capture",path=str(path),active_window=True)
            result=await self.native.call("ocr",path=str(path),**{key:frame[key] for key in ("x","y","width","height") if key in frame})
            text="\n".join(element.get("label","") for element in result.get("elements",[]))
            if has_secret(text) or _CHALLENGE.search(text):raise ComputerError("PREVIEW_PRIVATE","Sensitive/security content is omitted from the preview.")
            after=await self.observe(preflight)
            if after.blocked_reason or before.active_pid!=after.active_pid:
                raise ComputerError("PREVIEW_TARGET_CHANGED","The preview target changed; observe again.")
            return path.read_bytes()

    async def observe(self,settings:ComputerSettings,*,force_screen=False,active_window=False) -> ScreenObservation:
        raw=await self.native.call("observe",accessibility=settings.accessibility_access)
        if not settings.accessibility_access:raw["elements"]=[];raw.pop("focused_element",None)
        elements=[UIElement.model_validate(element) for element in raw.get("elements",[])]
        visible="\n".join(element.label for element in elements if element.label)[:8000]
        source="accessibility" if elements else "native";url="";blocked=None
        focused=raw.get("focused_element") or {}
        native_text="\n".join([visible,raw.get("window_title","")]+[element.label+"\n"+element.value for element in elements]+[focused.get(key,"") for key in ("label","description","title","value")])
        sensitive=has_secret(native_text)
        if (raw.get("focused_element") or {}).get("secure") or any(element.secure and element.width>0 and element.height>0 for element in elements):
            blocked="CREDENTIAL_REQUIRED"
        if _CHALLENGE.search(native_text):blocked="SECURITY_CHALLENGE"
        elif sensitive:blocked=blocked or "CREDENTIAL_REQUIRED"
        for element in elements:element.label="[Secure field]" if element.secure else safe_text(element.label)
        dedicated_browser=settings.browser_access and self.browser.started and raw.get("active_app") in {"Google Chrome","Chrome"} and (not raw.get("active_pid") or raw["active_pid"]==self.browser.pid)
        if force_screen and dedicated_browser:
            page=await self.browser.read_page()
            if page.get("credential_page") or page.get("challenge"):
                raise ComputerError("CREDENTIAL_REQUIRED","Handle the login/security page yourself; no screenshot will be sent to a model.")
        if not force_screen and dedicated_browser:
            page=await self.browser.read_page()
            url=page.get("url","")
            raw["window_title"]=page.get("title",raw.get("window_title",""))
            visible=page.get("visible_text",visible)
            elements=[UIElement.model_validate(element) for element in page.get("elements",[])];source="dom"
            if page.get("challenge"):blocked="SECURITY_CHALLENGE"
            elif page.get("credential_page"):blocked="CREDENTIAL_REQUIRED"
        elif force_screen or (not elements and settings.screen_access and settings.vision_enabled):
            if not settings.screen_access:raise ComputerError("SCREEN_ACCESS_DISABLED","Enable Screen Access to inspect your screen.")
            if blocked:pass
            else:
                captured=await self.screen.capture(active_window=active_window,settings=settings)
                elements=[UIElement.model_validate(element) for element in captured.get("elements",[])]
                visible=(captured.get("visible_text") or "\n".join(element.label for element in elements))[:8000];source="native_ocr"
                if captured.get("source")=="local_vision" or elements and elements[0].source=="local_vision":source="local_vision"
        sensitive=sensitive or has_secret("\n".join([visible]+[element.label+"\n"+element.value for element in elements]))
        if sensitive:
            blocked=blocked or "CREDENTIAL_REQUIRED"
            visible="[Sensitive screen content omitted]"
            for element in elements:element.label="[Sensitive screen content omitted]";element.value=""
            if raw.get("focused_element"):
                for key in ("label","description","title","value"):
                    if key in raw["focused_element"]:raw["focused_element"][key]="[Sensitive screen content omitted]"
        elif _CHALLENGE.search(visible):
            blocked="SECURITY_CHALLENGE"
        for element in elements:
            element.label="[Secure field]" if element.secure else safe_text(element.label)
            element.value="" if element.secure else safe_text(element.value)
        observation=ScreenObservation(active_app=safe_text(raw.get("active_app","")),active_bundle_id=safe_text(raw.get("active_bundle_id","")),active_pid=raw.get("active_pid",0),window_title=safe_text(raw.get("window_title","")),
            screen_width=raw.get("screen_width",0),screen_height=raw.get("screen_height",0),elements=elements,
            visible_text=safe_text(visible),confidence=0.98 if source in {"dom","accessibility"} else max((element.confidence for element in elements),default=0.4),
            source=source,current_url="" if has_secret(url) else url,blocked_reason=blocked,focused_element=raw.get("focused_element"))
        if observation.focused_element:
            for key in ("label","description","title","value"):
                if key in observation.focused_element:observation.focused_element[key]="[Secure field]" if observation.focused_element.get("secure") else safe_text(observation.focused_element.get(key,""))
        observation.fingerprint=hashlib.sha256(json.dumps({"app":observation.active_app,"pid":observation.active_pid,"url":observation.current_url,"text":observation.visible_text,"focused":observation.focused_element,"elements":[e.model_dump() for e in elements]},sort_keys=True).encode()).hexdigest()[:20]
        return observation
