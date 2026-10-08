"""Visible Chrome + fixed DOM operations via local CDP, in a dedicated profile."""

import asyncio
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.computer.models import ComputerError
from app.computer.intent import resolve_application
from app.computer.native import NativeBridge, process
from app.database.privacy import has_secret, safe_text

PAGE_SCRIPT = r"""(() => {
const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'};
const nodes=[...document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=textbox]')].filter(visible).slice(0,120);
const elements=nodes.map((e,i)=>{e.dataset.novaId='dom-'+i;const r=e.getBoundingClientRect();const secure=e.type==='password'||/otp|2fa|one.?time|credit.?card|card.?number|cc-(?:number|csc|exp|name)|cvv|cvc|passcode|verification.?code|security.?code|\bpin\b/i.test(e.autocomplete+' '+e.name+' '+e.id+' '+e.placeholder+' '+e.getAttribute('aria-label'));return {id:'dom-'+i,role:e.tagName.toLowerCase(),label:secure?'[Secure field]':(e.innerText||e.getAttribute('aria-label')||e.placeholder||e.value||'').slice(0,200),value:secure?'':(e.value||'').slice(0,1000),x:r.x,y:r.y,width:r.width,height:r.height,confidence:1,source:'dom',editable:['INPUT','TEXTAREA'].includes(e.tagName),secure}});
const anchors=[...document.querySelectorAll('a[href]')].filter(visible);
const searchResults=location.pathname==='/search'&&/(^|\.)google\.[a-z.]+$/.test(location.hostname)?anchors.filter(e=>e.querySelector('h3')&&e.closest('#rso,#search')):[];
const links=(searchResults.length?searchResults:anchors).map(e=>({title:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,180),url:e.href,displayed_url:searchResults.length?(e.innerText.match(/https?:\/\/[^\s›]+/)?.[0]||''):''})).filter(e=>e.title&&/^https?:/.test(e.url)).slice(0,50);
const text=(document.body?.innerText||'').slice(0,10000);
return {url:location.href,title:document.title,visible_text:text,elements,links,screen_width:innerWidth,screen_height:innerHeight,scroll_y:scrollY,challenge:/verify you are human|captcha|checking your browser|unusual traffic|security verification/i.test(text),credential_page:elements.some(e=>e.secure)};
})()"""


def public_url(value: str) -> str:
    parsed=urlparse(value)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ComputerError("URL_BLOCKED","Only public HTTP/HTTPS browsing is allowed.")
    host=parsed.hostname.lower()
    import ipaddress
    try:
        address=ipaddress.ip_address(host)
        if not address.is_global: raise ComputerError("URL_BLOCKED","Local/private network browsing is outside this task capability.")
    except ValueError: pass
    if host in {"localhost","metadata.google.internal"} or host.endswith((".local",".internal")) or has_secret(value):
        raise ComputerError("URL_BLOCKED","Private/credential-bearing URLs cannot be browsed by the agent.")
    if re.search(r"\.(?:exe|dmg|pkg|app|sh|command|zip)(?:\?|$)",parsed.path,re.I):
        raise ComputerError("DOWNLOAD_BLOCKED","Executable/archive downloads are not automatic.")
    return value


class BrowserProvider:
    def __init__(self, native: NativeBridge, root: Path):
        self.native=native;self.profile=root/"backend/data/computer/chrome-profile"
        self.port=0; self.target=None; self.started=False;self.lock=asyncio.Lock();self.pid=0;self.target_application="Google Chrome"

    async def start(self):
        if self.started:
            return
        async with self.lock:
            if self.started:return
            found=await self.native.call("app.find",name="Google Chrome")
            # Reuse only NOVA's dedicated profile after a backend restart, never the user's browsing profile.
            listing=(await process("/bin/ps","-axo","pid=,command=")).decode(errors="replace")
            existing_line=next((line for line in listing.splitlines()
                if f"--user-data-dir={self.profile}" in line and "--type=" not in line and re.search(r"--remote-debugging-port=\d+",line)),None)
            existing=re.search(r"--remote-debugging-port=(\d+)",existing_line) if existing_line else None
            if existing_line:self.pid=int(existing_line.strip().split()[0])
            if existing:self.port=int(existing[1])
            else:
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1",0));self.port=reservation.getsockname()[1]
            self.profile.mkdir(parents=True,exist_ok=True)
            if not existing:
                await process("/usr/bin/open","-na",found["path"],"--args",f"--remote-debugging-port={self.port}","--remote-debugging-address=127.0.0.1",f"--user-data-dir={self.profile}","--no-first-run","--no-default-browser-check","about:blank")
            for _ in range(60):
                try:
                    async with httpx.AsyncClient(timeout=1) as client:
                        response=await client.get(f"http://127.0.0.1:{self.port}/json/list")
                        tabs=[tab for tab in response.json() if tab.get("type")=="page"]
                        if not tabs:
                            created=await client.put(f"http://127.0.0.1:{self.port}/json/new?about:blank")
                            created.raise_for_status();tab=created.json()
                            if tab.get("type")=="page":tabs=[tab]
                        if tabs:
                            self.target=tabs[0];self.started=True
                            if not self.pid:
                                listing=(await process("/bin/ps","-axo","pid=,command=")).decode(errors="replace")
                                line=next((line for line in listing.splitlines() if f"--user-data-dir={self.profile}" in line and "--type=" not in line and f"--remote-debugging-port={self.port}" in line),None)
                                if line:self.pid=int(line.strip().split()[0])
                            await self.command("Browser.setDownloadBehavior",{"behavior":"deny"},_recover=False)
                            return
                except (httpx.HTTPError,ValueError):pass
                await asyncio.sleep(0.2)
            raise ComputerError("BROWSER_UNAVAILABLE","NOVA's visible browser could not start.")

    async def open(self, application: str = "Google Chrome"):
        target=resolve_application(application)
        requested=target[0] if target else application
        if requested != "Google Chrome":
            found=await self.native.call("app.find",name=requested)
            await process("/usr/bin/open","-a",found["path"])
            await asyncio.sleep(0.4)
            active=await self.native.call("app.focus",name=requested)
            self.target_application=requested
            return {"application":requested,"bundle_id":target[1] if target else "","active_app":active.get("name",requested),"native":True}
        await self.start()
        if self.pid:
            try:await self.native.call("app.focus",name="Google Chrome",pid=self.pid)
            except ComputerError as error:
                if error.code!="APP_NOT_RUNNING":raise
                self.started=False;self.pid=0;await self.start()
                await self.native.call("app.focus",name="Google Chrome",pid=self.pid)
        await self.command("Page.bringToFront")
        self.target_application="Google Chrome"
        return await self.read_page()

    async def command(self, method: str, params: dict | None = None, *, _recover: bool = True):
        if not self.started:await self.start()
        try:
            async with connect(self.target["webSocketDebuggerUrl"],open_timeout=5,max_size=2_000_000) as ws:
                await ws.send(json.dumps({"id":1,"method":method,"params":params or {}}))
                async with asyncio.timeout(20):
                    while True:
                        result=json.loads(await ws.recv())
                        if result.get("id")==1:
                            if result.get("error"):raise ComputerError("BROWSER_ACTION_FAILED","Browser rejected the action.")
                            return result.get("result",{})
        except (InvalidStatus, ConnectionClosed, OSError, asyncio.TimeoutError) as error:
            if not _recover:
                raise ComputerError("BROWSER_UNAVAILABLE","NOVA's visible browser target became unavailable.") from error
            self.started=False; self.target=None; self.pid=0
            await self.start()
            return await self.command(method,params,_recover=False)

    async def evaluate(self, expression: str):
        result=await self.command("Runtime.evaluate",{"expression":expression,"returnByValue":True,"awaitPromise":True})
        if result.get("exceptionDetails"):raise ComputerError("ELEMENT_NOT_FOUND","The browser target changed or was not found.")
        return result.get("result",{}).get("value")

    async def read_page(self):
        if not self.started: return {"url":"","visible_text":"","elements":[],"links":[]}
        result=await self.evaluate(PAGE_SCRIPT)
        sensitive=has_secret("\n".join([result.get("visible_text",""),result.get("title",""),result.get("url","")]+[element.get("label","")+"\n"+element.get("value","") for element in result.get("elements",[])]))
        if sensitive:
            result["credential_page"]=True
            result["visible_text"]="[Sensitive screen content omitted]"
            for element in result.get("elements",[]):element["label"]="[Sensitive screen content omitted]";element["value"]=""
        result["visible_text"]=safe_text(result.get("visible_text",""))
        result["title"]=safe_text(result.get("title",""))
        if has_secret(result.get("url","")):result["url"]=""
        for element in result.get("elements",[]):
            element["label"]="[Secure field]" if element.get("secure") else safe_text(element["label"])
            element["value"]="" if element.get("secure") else safe_text(element.get("value",""))
        result["links"]=[link for link in result.get("links",[]) if not has_secret(link["url"]+link["title"]+link.get("displayed_url",""))]
        return result

    async def navigate(self,url: str, application: str = "Google Chrome"):
        public_url(url)
        if application != "Google Chrome":
            await self.open(application)
            active=await self.native.call("app.active")
            target_pid=active.get("pid",0)
            await self.native.call("keyboard.hotkey",key="l",modifiers=["command"],target_pid=target_pid)
            await self.native.call("keyboard.type_text",text=url,target_pid=target_pid)
            await self.native.call("keyboard.press",key="enter",target_pid=target_pid)
            await asyncio.sleep(1)
            return {"application":application,"url":url,"submitted":True,"active_app":(await self.native.call("app.active")).get("active_app",application)}
        await self.open();await self.command("Page.navigate",{"url":url})
        return await self.loaded()

    async def loaded(self):
        for _ in range(50):
            await asyncio.sleep(0.2)
            if await self.evaluate("document.readyState") == "complete":return await self.read_page()
        raise ComputerError("PAGE_TIMEOUT","The browser page did not finish loading.")

    async def search(self,query: str,site: str="web",application: str="Google Chrome"):
        if has_secret(query):raise ComputerError("CREDENTIAL_BLOCKED","Private credentials cannot become a search query.")
        url="https://www.youtube.com/results?"+urlencode({"search_query":query}) if site=="youtube" else "https://www.google.com/search?"+urlencode({"q":query})
        return await self.navigate(url,application)

    async def click(self,element_id: str):
        if not re.fullmatch(r"dom-\d+",element_id):raise ComputerError("ELEMENT_NOT_FOUND","Observe the page and use its element ID.")
        target=await self.evaluate(f"(() => {{const e=document.querySelector('[data-nova-id={element_id}]');if(!e)throw Error('missing');return {{anchor:e.tagName==='A',url:e.href||'',download:e.hasAttribute('download')}};}})()")
        if target.get("download"):raise ComputerError("DOWNLOAD_BLOCKED","Automatic browser downloads are disabled.")
        if target.get("anchor"):public_url(target["url"])
        expression=f"(() => {{ const e=document.querySelector('[data-nova-id={element_id}]');if(!e)throw Error('missing');if(e.tagName==='A')e.target='_self';e.click();return {{clicked:true}};}})()"
        await self.evaluate(expression);await asyncio.sleep(0.4);return await self.read_page()

    async def type(self,element_id: str,text: str,application: str = "Google Chrome"):
        if has_secret(text):raise ComputerError("CREDENTIAL_BLOCKED","Invalid or credential-bearing input.")
        target=resolve_application(application)
        requested=target[0] if target else application
        if requested != "Google Chrome":
            active=await self.native.call("app.active")
            active_name=active.get("active_app","")
            if not (active_name.casefold()==requested.casefold() or active.get("bundle_id","").casefold()==(target[1] if target else "").casefold()):
                raise ComputerError("REQUESTED_APP_MISMATCH",f"{requested} is not the active application.")
            if not re.search(r"(?:address|search|omnibox)",element_id,re.I):
                raise ComputerError("ELEMENT_NOT_FOUND","The requested native browser field was not identified as an address or search field.")
            await self.native.call("keyboard.hotkey",key="l",modifiers=["command"],target_pid=active.get("pid",0))
            await self.native.call("keyboard.type_text",text=text,target_pid=active.get("pid",0))
            self.target_application=requested
            return {"text":text,"element_id":element_id,"application":requested,"typed":True,"native":True}
        if not re.fullmatch(r"dom-\d+",element_id):raise ComputerError("ELEMENT_NOT_FOUND","Observe the browser page and use its DOM element ID.")
        value=json.dumps(text)
        await self.evaluate(f"(() => {{const e=document.querySelector('[data-nova-id={element_id}]');if(!e||e.type==='password')throw Error('blocked');e.focus();e.value={value};e.dispatchEvent(new Event('input',{{bubbles:true}}));return true;}})()")
        return {"text":text,"element_id":element_id,"application":"Google Chrome"}

    async def scroll(self,dy: int):
        before=await self.evaluate("scrollY");after=await self.evaluate(f"window.scrollBy(0,{int(dy)});scrollY")
        return {"before":before,"after":after}

    async def back(self):
        await self.evaluate("history.back();true");await asyncio.sleep(0.5);return await self.read_page()
    async def forward(self):
        await self.evaluate("history.forward();true");await asyncio.sleep(0.5);return await self.read_page()
    async def reload(self):
        await self.command("Page.reload");await asyncio.sleep(0.5);return await self.read_page()
    async def find_text(self,text: str):
        page=await self.read_page();return {"found":text.casefold() in page["visible_text"].casefold(),"query":text}
