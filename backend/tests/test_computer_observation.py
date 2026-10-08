from unittest.mock import AsyncMock

import pytest

from app.computer.browser import public_url
from app.computer.models import ComputerError, ComputerSettings
from app.computer.observation import ComputerVision, ScreenProvider


@pytest.mark.parametrize("url",["file:///etc/passwd","http://localhost","http://127.0.0.1","https://user:password@example.com","https://example.com/setup.dmg"])
def test_browser_blocks_private_urls_and_downloads(url):
    with pytest.raises(ComputerError):public_url(url)


@pytest.mark.asyncio
async def test_dom_observation_is_sanitized_and_challenges_block():
    native=AsyncMock();native.call.return_value={"active_app":"Google Chrome","elements":[],"screen_width":100,"screen_height":100}
    browser=AsyncMock();browser.started=True;browser.read_page.return_value={"url":"https://example.com","visible_text":"API key: sk-private","elements":[],"challenge":True}
    result=await ComputerVision(native,browser).observe(ComputerSettings(browser_access=True))
    assert result.source=="dom" and result.blocked_reason=="SECURITY_CHALLENGE"
    assert "sk-private" not in result.visible_text and result.screenshot_path is None


@pytest.mark.asyncio
async def test_temporary_screenshot_is_removed_even_on_ocr_failure():
    paths=[];native=AsyncMock()
    async def call(action,**args):
        if action=="screen.capture":
            from pathlib import Path
            path=Path(args["path"]);path.write_bytes(b"fake-image");paths.append(path);return {}
        raise ComputerError("OCR_FAILED","OCR unavailable")
    native.call.side_effect=call
    with pytest.raises(ComputerError):await ScreenProvider(native).capture()
    assert paths and not paths[0].exists()


@pytest.mark.asyncio
async def test_visible_secure_field_prevents_screenshot_capture():
    native=AsyncMock();native.call.return_value={"active_app":"Editor","elements":[{"id":"secure","role":"AXSecureTextField","secure":True,"width":100,"height":25}],"focused_element":{"secure":True}}
    vision=ComputerVision(native,AsyncMock());vision.screen.capture=AsyncMock(side_effect=AssertionError("No secure screenshot"))
    result=await vision.observe(ComputerSettings(accessibility_access=True,screen_access=True),force_screen=True)
    assert result.blocked_reason=="CREDENTIAL_REQUIRED"
    vision.screen.capture.assert_not_called()


@pytest.mark.asyncio
async def test_local_vision_cannot_upload_image_to_remote_ollama(tmp_path):
    from app.computer.vision import LocalVisionProvider
    with pytest.raises(ComputerError,match="loopback-local"):
        await LocalVisionProvider("https://remote.example").inspect(tmp_path/"private.png","vision",100,100)


@pytest.mark.asyncio
async def test_browser_recovers_closed_windows_in_dedicated_profile(tmp_path,monkeypatch):
    import httpx
    from app.computer.browser import BrowserProvider
    browser=BrowserProvider(AsyncMock(),tmp_path);browser.native.call.return_value={"path":"/Applications/Google Chrome.app"}
    monkeypatch.setattr("app.computer.browser.process",AsyncMock(return_value=f"123 Chrome --user-data-dir={browser.profile} --remote-debugging-port=12345".encode()))
    methods=[]
    def handle(request):
        methods.append(request.method)
        return httpx.Response(200,json=[] if request.method=="GET" else {"type":"page","id":"new","webSocketDebuggerUrl":"ws://127.0.0.1:12345/devtools/page/new"})
    client=httpx.AsyncClient
    monkeypatch.setattr("app.computer.browser.httpx.AsyncClient",lambda **kwargs:client(transport=httpx.MockTransport(handle),**kwargs))
    browser.command=AsyncMock(return_value={})
    await browser.start()
    assert browser.started and browser.pid==123 and browser.target["id"]=="new"
    assert methods==["GET","PUT"]


@pytest.mark.asyncio
async def test_ocr_secrets_cannot_survive_in_element_labels():
    native=AsyncMock();native.call.return_value={"active_app":"Editor","elements":[]}
    vision=ComputerVision(native,AsyncMock())
    vision.screen.capture=AsyncMock(return_value={"elements":[
        {"id":"label","role":"text","label":"OTP","source":"ocr"},
        {"id":"code","role":"text","label":"123456","source":"ocr"}]})
    observation=await vision.observe(ComputerSettings(screen_access=True),force_screen=True)
    assert observation.blocked_reason=="CREDENTIAL_REQUIRED"
    assert "123456" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_native_security_challenge_pauses_even_without_browser_dom():
    native=AsyncMock();native.call.return_value={"active_app":"Safari","elements":[{"id":"challenge","role":"AXStaticText","label":"Verify you are human"}]}
    observation=await ComputerVision(native,AsyncMock()).observe(ComputerSettings(accessibility_access=True,browser_access=False))
    assert observation.blocked_reason=="SECURITY_CHALLENGE"


@pytest.mark.asyncio
@pytest.mark.parametrize("labels",[["Verify you are human"],["OTP","123456"],["API key: sk-private"]])
async def test_sensitive_native_content_blocks_capture_before_image_processing(labels):
    native=AsyncMock();native.call.return_value={"active_app":"Editor","elements":[{"id":str(i),"role":"AXStaticText","label":label} for i,label in enumerate(labels)]}
    vision=ComputerVision(native,AsyncMock());vision.screen.capture=AsyncMock(side_effect=AssertionError("Sensitive image must not be captured"))
    observation=await vision.observe(ComputerSettings(accessibility_access=True,screen_access=True),force_screen=True)
    assert observation.blocked_reason in {"SECURITY_CHALLENGE","CREDENTIAL_REQUIRED"}
    vision.screen.capture.assert_not_called()
    assert "123456" not in observation.model_dump_json() and "sk-private" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_secure_focused_metadata_cannot_expose_plain_code():
    native=AsyncMock();native.call.return_value={"active_app":"Editor","focused_element":{"secure":True,"label":"123456","description":"123456","title":"123456","value":"123456"}}
    observation=await ComputerVision(native,AsyncMock()).observe(ComputerSettings(accessibility_access=True))
    assert observation.blocked_reason=="CREDENTIAL_REQUIRED" and "123456" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_browser_masks_split_secrets_before_returning_dom(tmp_path):
    import json
    from app.computer.browser import BrowserProvider
    browser=BrowserProvider(AsyncMock(),tmp_path);browser.started=True
    browser.evaluate=AsyncMock(return_value={"url":"https://example.com","visible_text":"A page","elements":[{"label":"OTP","value":"123456"}],"links":[],"title":"Example"})
    page=await browser.read_page()
    assert page["credential_page"] and "123456" not in json.dumps(page)


@pytest.mark.asyncio
async def test_live_preview_is_opt_in_and_security_checks_same_temporary_image():
    from pathlib import Path
    from app.computer.models import ScreenObservation
    native=AsyncMock();vision=ComputerVision(native,AsyncMock());vision.observe=AsyncMock(return_value=ScreenObservation(active_pid=1))
    with pytest.raises(ComputerError):await vision.preview(ComputerSettings(screen_access=True))
    native.call.assert_not_awaited()
    paths=[]
    async def call(action,**arguments):
        if action=="screen.capture":
            path=Path(arguments["path"]);paths.append(path);path.write_bytes(b"private-image");return {}
        return {"elements":[{"label":"OTP"},{"label":"123456"}]}
    native.call.side_effect=call
    with pytest.raises(ComputerError,match="Sensitive"):
        await vision.preview(ComputerSettings(screen_access=True,watch_nova=True))
    assert paths and not paths[0].exists()


@pytest.mark.asyncio
async def test_live_preview_denies_known_secure_page_before_capture():
    from app.computer.models import ScreenObservation
    native=AsyncMock();vision=ComputerVision(native,AsyncMock());vision.observe=AsyncMock(return_value=ScreenObservation(blocked_reason="CREDENTIAL_REQUIRED"))
    with pytest.raises(ComputerError):await vision.preview(ComputerSettings(screen_access=True,watch_nova=True))
    native.call.assert_not_awaited()
