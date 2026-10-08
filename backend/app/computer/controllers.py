"""Real native controllers, called only through the guarded tool executor."""

import asyncio
import plistlib
from pathlib import Path

from app.computer.models import ComputerError
from app.computer.intent import resolve_application
from app.computer.native import NativeBridge, process
from app.database.privacy import has_secret, safe_text


INSTALLED_APPLICATION_ROOTS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Library/CoreServices"),
    Path("/System/Cryptexes/App/System/Applications"),
    Path.home() / "Applications",
)


def is_installed_application(path: Path) -> bool:
    """Accept canonical paths for apps installed through macOS Cryptexes too."""
    try:
        canonical = path.resolve()
        return canonical.suffix.casefold() == ".app" and any(
            canonical.is_relative_to(root.resolve()) for root in INSTALLED_APPLICATION_ROOTS
        )
    except OSError:
        return False


class ApplicationController:
    def __init__(self, native: NativeBridge):
        self.native = native

    async def list(self):
        result=await self.native.call("apps.list")
        result["installed"]=await asyncio.to_thread(self.installed)
        return result

    @staticmethod
    def installed():
        apps=[]
        for root in [Path("/Applications"),Path("/System/Applications"),Path.home()/"Applications"]:
            for path in [*root.glob("*.app"),*root.glob("*/*.app")]:
                try:
                    info=plistlib.loads((path/"Contents/Info.plist").read_bytes())
                    apps.append({"name":info.get("CFBundleDisplayName") or info.get("CFBundleName") or path.stem,"path":str(path),"bundle_id":info.get("CFBundleIdentifier","")})
                except (OSError,ValueError):continue
        return apps[:200]

    async def active(self):
        return await self.native.call("app.active")

    async def open(self, name: str):
        target=resolve_application(name)
        requested_name=target[0] if target else name
        try:result=await self.native.call("app.find",name=name)
        except ComputerError as error:
            if error.code!="APP_NOT_INSTALLED":raise
            candidates=[app for app in await asyncio.to_thread(self.installed) if requested_name.casefold() == app["name"].casefold() or requested_name.casefold() in Path(app["path"]).stem.casefold() or target and target[1].casefold() == app.get("bundle_id","").casefold()]
            if len(candidates)!=1:raise
            result=candidates[0]
        application=Path(result["path"])
        if not is_installed_application(application):
            raise ComputerError("APP_LOCATION_BLOCKED","Only applications in installed application folders may be launched automatically.")
        await process("/usr/bin/open", "-a", result["path"])
        # `open -a` can launch an already-running app without making it the
        # frontmost application. Explicitly focus the exact resolved target so
        # the subsequent observe/verify step cannot pass against loginwindow or
        # NOVA's non-focused companion window.
        await self.focus(requested_name)
        result["requested_name"]=requested_name
        if target:result["bundle_id"]=target[1]
        return result

    async def focus(self, name: str):
        target=resolve_application(name)
        return await self.native.call("app.focus", name=target[0] if target else name)

    async def quit(self, name: str):
        return await self.native.call("app.quit", name=name)


class MouseController:
    def __init__(self, native: NativeBridge):
        self.native = native
        self.pending_buttons=set()

    async def move(self, x: float, y: float): return await self.native.call("mouse.move", x=x, y=y)
    async def _button_action(self,action,**arguments):
        self.pending_buttons.add("right" if action=="mouse.right_click" else "left")
        result=await self.native.call(action,**arguments)
        self.pending_buttons.clear();return result
    async def click(self, x: float, y: float): return await self._button_action("mouse.click", x=x, y=y)
    async def double_click(self, x: float, y: float): return await self._button_action("mouse.double_click", x=x, y=y)
    async def right_click(self, x: float, y: float): return await self._button_action("mouse.right_click", x=x, y=y)
    async def drag(self, x: float, y: float, to_x: float, to_y: float): return await self._button_action("mouse.drag", x=x, y=y, to_x=to_x, to_y=to_y)
    async def scroll(self, dy: int, dx: int = 0): return await self.native.call("mouse.scroll", dy=dy, dx=dx)
    async def release_all(self):
        if self.pending_buttons:
            await self.native.call("mouse.release_all");self.pending_buttons.clear()


class KeyboardController:
    def __init__(self, native: NativeBridge): self.native = native;self.held_keys=set()
    async def press(self, key: str): return await self.native.call("keyboard.press", key=key)
    async def hotkey(self, key: str, modifiers: list[str]): return await self.native.call("keyboard.hotkey", key=key, modifiers=modifiers)
    async def key_down(self, key: str):
        self.held_keys.add(key)
        try:return await self.native.call("keyboard.key_down", key=key)
        except ComputerError:self.held_keys.discard(key);raise
    async def key_up(self, key: str):
        result=await self.native.call("keyboard.key_up", key=key);self.held_keys.discard(key);return result
    async def type_text(self, text: str):
        if has_secret(text): raise ComputerError("CREDENTIAL_BLOCKED", "Enter credentials yourself; NOVA will not type them.")
        return await self.native.call("keyboard.type_text", text=text)
    async def release_all(self):
        for key in tuple(self.held_keys):await self.key_up(key)
        return await self.native.call("keyboard.release_all")


class FilesystemProvider:
    """Scope is checked separately before invoking any of these methods."""
    def path(self, value: str) -> Path: return Path(value).expanduser().resolve()
    async def list(self, path: str):
        root = self.path(path)
        if not root.is_dir(): raise ComputerError("DIRECTORY_NOT_FOUND", "The directory does not exist.")
        items = await asyncio.to_thread(lambda: sorted(root.iterdir(), key=lambda item: item.name.casefold())[:300])
        return {"path": str(root), "entries": [{"name": item.name, "path": str(item), "directory": item.is_dir(), "modified_at": item.stat().st_mtime} for item in items if not item.name.startswith(".") and not has_secret(str(item))]}
    async def read(self, path: str):
        target = self.path(path)
        if not target.is_file(): raise ComputerError("FILE_NOT_FOUND", "The file does not exist.")
        if target.stat().st_size > 200000: raise ComputerError("FILE_TOO_LARGE", "Use a smaller text file for this task.")
        try:content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeError as error:raise ComputerError("FILE_NOT_TEXT","This file is not UTF-8 text and cannot be read into the task.") from error
        if has_secret(content): raise ComputerError("CREDENTIAL_BLOCKED", "This file contains private credentials and will not be read into the agent.")
        return {"path":str(target), "content":safe_text(content)}
    async def mkdir(self, path: str):
        target=self.path(path); await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        return {"path":str(target), "exists":target.is_dir()}
    async def write(self, path: str, content: str):
        if has_secret(content): raise ComputerError("CREDENTIAL_BLOCKED", "Credentials cannot be written by the agent.")
        target=self.path(path)
        if target.exists(): raise ComputerError("FILE_EXISTS", "The destination exists. Choose a new filename; overwriting is not automatic.")
        await asyncio.to_thread(self._write, target, content)
        return {"path":str(target), "bytes":target.stat().st_size}
    @staticmethod
    def _write(path, content):
        with path.open("x", encoding="utf-8") as output: output.write(content)
    async def move(self, path: str, destination: str):
        source,target=self.path(path),self.path(destination)
        if target.exists(): raise ComputerError("FILE_EXISTS", "The destination already exists.")
        try:await asyncio.to_thread(self._move_exclusive,source,target)
        except FileExistsError as error:raise ComputerError("FILE_EXISTS","The destination already exists; no file was overwritten.") from error
        return {"path":str(source),"destination":str(target)}
    @staticmethod
    def _move_exclusive(source,target):
        import ctypes
        import os
        import sys
        if sys.platform=="darwin":
            library=ctypes.CDLL(None,use_errno=True)
            rename=library.renamex_np;rename.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
            if rename(os.fsencode(source),os.fsencode(target),4):
                code=ctypes.get_errno();raise OSError(code,os.strerror(code),str(target))
        elif source.is_file():
            os.link(source,target);source.unlink()
        else:raise ComputerError("PLATFORM_UNSUPPORTED","Exclusive directory moves currently require macOS.")
    async def copy(self, path: str, destination: str):
        import shutil
        source,target=self.path(path),self.path(destination)
        if target.exists(): raise ComputerError("FILE_EXISTS", "The destination already exists.")
        def copy_exclusive():
            with source.open("rb") as input_file, target.open("xb") as output_file:
                shutil.copyfileobj(input_file,output_file)
            shutil.copystat(source,target)
        await asyncio.to_thread(copy_exclusive)
        return {"path":str(source),"destination":str(target)}
    async def delete(self, path: str):
        target=self.path(path)
        if target.is_dir(): await asyncio.to_thread(target.rmdir)  # Never recursive deletion.
        else: await asyncio.to_thread(target.unlink)
        return {"path":str(target),"exists":target.exists()}
    async def open(self, path: str):
        target=self.path(path)
        if not target.exists(): raise ComputerError("FILE_NOT_FOUND", "The path does not exist.")
        if target.suffix.casefold() in {".app",".exe",".dmg",".pkg",".sh",".command",".py",".js",".scpt",".workflow",".jar",".run",".bat",".ps1"} or target.is_file() and target.stat().st_mode & 0o111:
            raise ComputerError("EXECUTABLE_BLOCKED","Opening scripts, executables or installers is outside document access.")
        await process("/usr/bin/open",str(target));return {"path":str(target)}


class ClipboardProvider:
    def __init__(self, native: NativeBridge): self.native=native
    async def read(self):
        result=await self.native.call("clipboard.read")
        if has_secret(result.get("text", "")): raise ComputerError("CREDENTIAL_BLOCKED", "Clipboard contains credentials; NOVA will not read them.")
        return result
    async def write(self, text: str):
        if has_secret(text): raise ComputerError("CREDENTIAL_BLOCKED", "NOVA will not copy credentials.")
        return await self.native.call("clipboard.write",text=text)
