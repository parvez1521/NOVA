"""Cancellable fixed native-helper protocol; compiles only when used."""

import asyncio
import json
import platform
import plistlib
import tempfile
import os
import signal
from contextvars import ContextVar
from pathlib import Path

from app.computer.models import ComputerError


async def process(*args: str, data: bytes | None = None, timeout: float = 20) -> bytes:
    child = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    communication = asyncio.create_task(child.communicate(data))
    try:
        output, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
        if child.returncode:
            raise ComputerError("COMPUTER_PROCESS_FAILED", "The local computer helper failed.")
        return output
    finally:
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 0.25)
            except asyncio.TimeoutError:
                child.kill(); await child.wait()
        await communication


class NativeBridge:
    def __init__(self, project_root: Path):
        self.source = Path(__file__).parent / "native/NovaNative.swift"
        self.bundle = project_root / "backend/data/computer/NOVA Computer Access.app"
        self.binary = self.bundle / "Contents/MacOS/NovaNative"
        packaged=os.environ.get("NOVA_NATIVE_HELPER")
        if packaged:
            self.binary=Path(packaged);self.bundle=self.binary.parents[2]
        self.lock = asyncio.Lock()
        self.signature_verified=False
        self.interaction_pid=ContextVar("nova_interaction_pid",default=0)
        self.visibility_mode=ContextVar("nova_visibility_mode",default="NORMAL")

    async def build(self):
        if platform.system() != "Darwin":
            raise ComputerError("PLATFORM_UNSUPPORTED", "Real computer use currently requires macOS.")
        async with self.lock:
            if os.environ.get("NOVA_NATIVE_HELPER"):
                if not self.binary.is_file():raise ComputerError("NATIVE_HELPER_MISSING","The packaged native helper is missing. Reinstall NOVA.")
                if not self.signature_verified:
                    await process("/usr/bin/codesign","--verify","--deep","--strict",str(self.bundle));self.signature_verified=True
                return
            if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
                if self.signature_verified:return
                try:
                    await process("/usr/bin/codesign","--verify","--deep","--strict",str(self.bundle))
                    self.signature_verified=True;return
                except ComputerError:pass
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            (self.bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"local.nova.computer-access","CFBundleName":"NOVA Computer Access","CFBundleExecutable":"NovaNative","CFBundlePackageType":"APPL","CFBundleVersion":"1","NSHighResolutionCapable":True,"LSUIElement":True,"NSScreenCaptureUsageDescription":"NOVA observes the screen only during a requested computer task."}))
            # Replace the binary inode; in-place recompilation can leave macOS's
            # executable page-signature cache associated with the previous code.
            with tempfile.TemporaryDirectory(prefix="nova-build-",dir=self.binary.parent) as directory:
                compiled=Path(directory)/"NovaNative"
                await process("/usr/bin/xcrun", "swiftc", str(self.source), "-o", str(compiled), "-framework", "AppKit", "-framework", "ApplicationServices", "-framework", "Vision", "-framework", "ScreenCaptureKit", timeout=120)
                compiled.replace(self.binary)
            await process("/usr/bin/codesign", "--force", "--deep", "--sign", "-", "--identifier", "local.nova.computer-access", "--requirements", '=designated => identifier "local.nova.computer-access"', str(self.bundle))
            await process("/usr/bin/codesign","--verify","--deep","--strict",str(self.bundle))
            self.signature_verified=True

    async def call(self, action: str, **arguments) -> dict:
        await self.build()
        if action.startswith(("mouse.","keyboard.","ax.")) and action not in {"mouse.position","mouse.release_all","keyboard.release_all","keyboard.key_up"} and self.interaction_pid.get():
            arguments["target_pid"]=self.interaction_pid.get()
        if action.startswith("mouse.") and action not in {"mouse.position","mouse.scroll","mouse.release_all"}:
            arguments["movement_duration"]={"FAST":0,"NORMAL":0.28,"HUMAN_VISIBLE":0.7}[self.visibility_mode.get()]
        raw=await process(str(self.binary),data=json.dumps({"action":action,"arguments":arguments}).encode(),timeout=30)
        result=json.loads(raw)
        if not result.get("success"):
            error = result.get("error", {})
            raise ComputerError(error.get("code", "NATIVE_ACTION_FAILED"), error.get("message", "Native action failed."))
        return result["data"]

    async def watch_escape(self,stop):
        await self.build()
        child=await asyncio.create_subprocess_exec(str(self.binary),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        child.stdin.write(b'{"action":"watch.escape"}');await child.stdin.drain();child.stdin.close()
        try:
            while line:=await child.stdout.readline():
                if b'"escape":true' in line:await stop();return
        finally:
            if child.returncode is None:child.terminate()
            await child.wait()
