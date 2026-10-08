from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.computer.controllers import ApplicationController, ClipboardProvider, FilesystemProvider, KeyboardController, MouseController, is_installed_application
from app.computer.models import ComputerError


@pytest.mark.asyncio
async def test_mouse_and_keyboard_use_fixed_native_protocol():
    native=AsyncMock(); native.call.return_value={"success":True}
    mouse=MouseController(native); keyboard=KeyboardController(native)
    await mouse.click(10,20); native.call.assert_called_with("mouse.click",x=10,y=20)
    await mouse.drag(10,20,30,40); native.call.assert_called_with("mouse.drag",x=10,y=20,to_x=30,to_y=40)
    await keyboard.hotkey("l",["command"]); native.call.assert_called_with("keyboard.hotkey",key="l",modifiers=["command"])
    await keyboard.type_text("नमस्ते"); native.call.assert_called_with("keyboard.type_text",text="नमस्ते")
    with pytest.raises(ComputerError): await keyboard.type_text("My password is hunter2")
    await keyboard.release_all(); native.call.assert_called_with("keyboard.release_all")


@pytest.mark.asyncio
async def test_clipboard_secrets_are_not_returned():
    native=AsyncMock(); native.call.return_value={"text":"API key: sk-private"}
    with pytest.raises(ComputerError): await ClipboardProvider(native).read()
    with pytest.raises(ComputerError): await ClipboardProvider(native).write("Password is private")


@pytest.mark.asyncio
async def test_filesystem_only_uses_sandbox_test_paths(tmp_path):
    provider=FilesystemProvider(); folder=tmp_path/"screenshots"
    assert (await provider.mkdir(str(folder)))["exists"]
    file=folder/"notes.txt"; await provider.write(str(file),"Hello NOVA")
    assert (await provider.read(str(file)))["content"]=="Hello NOVA"
    with pytest.raises(ComputerError): await provider.write(str(file),"Overwrite")
    destination=folder/"renamed.txt";await provider.move(str(file),str(destination))
    assert not file.exists() and destination.exists()
    assert (await provider.list(str(folder)))["entries"][0]["name"]=="renamed.txt"
    await provider.delete(str(destination));assert not destination.exists()


@pytest.mark.asyncio
async def test_missing_app_is_reported_without_fake_open(monkeypatch):
    native=AsyncMock();native.call.side_effect=ComputerError("APP_NOT_INSTALLED","Missing app")
    run=AsyncMock();monkeypatch.setattr("app.computer.controllers.process",run)
    with pytest.raises(ComputerError):await ApplicationController(native).open("Missing")
    run.assert_not_called()


def test_exclusive_move_never_replaces_existing_destination(tmp_path):
    source=tmp_path/"source.txt";destination=tmp_path/"existing.txt"
    source.write_text("Source");destination.write_text("Keep this")
    with pytest.raises(FileExistsError):FilesystemProvider._move_exclusive(source,destination)
    assert source.read_text()=="Source" and destination.read_text()=="Keep this"


@pytest.mark.asyncio
async def test_system_cryptex_safari_can_be_launched(monkeypatch):
    path="/System/Cryptexes/App/System/Applications/Safari.app"
    native=AsyncMock();native.call.return_value={"path":path,"name":"Safari"}
    run=AsyncMock();monkeypatch.setattr("app.computer.controllers.process",run)
    result=await ApplicationController(native).open("Safari")
    run.assert_awaited_once_with("/usr/bin/open","-a",path)
    assert result["bundle_id"]=="com.apple.Safari"


def test_installed_app_symlinks_allow_system_apps_but_not_downloads(tmp_path,monkeypatch):
    installed=tmp_path/"Applications";installed.mkdir()
    cryptex=tmp_path/"System/Cryptexes/App/System/Applications";cryptex.mkdir(parents=True)
    safari=cryptex/"Safari.app";safari.mkdir()
    (installed/"Safari.app").symlink_to(safari,target_is_directory=True)
    downloads=tmp_path/"Downloads";downloads.mkdir()
    installer=downloads/"Installer.app";installer.mkdir()
    (installed/"Installer.app").symlink_to(installer,target_is_directory=True)
    monkeypatch.setattr("app.computer.controllers.INSTALLED_APPLICATION_ROOTS",(installed,cryptex))
    assert is_installed_application(installed/"Safari.app")
    assert not is_installed_application(installed/"Installer.app")
    assert not is_installed_application(Path(str(cryptex)+"-untrusted")/"Safari.app")


@pytest.mark.asyncio
async def test_document_open_cannot_run_installer_or_script(tmp_path,monkeypatch):
    run=AsyncMock();monkeypatch.setattr("app.computer.controllers.process",run)
    for name in ["download.command","installer.pkg","code.py"]:
        path=tmp_path/name;path.write_text("code")
        with pytest.raises(ComputerError):await FilesystemProvider().open(str(path))
    run.assert_not_called()


@pytest.mark.asyncio
async def test_keyboard_cleanup_releases_all_held_keys():
    native=AsyncMock();native.call.return_value={"pressed":True}
    keyboard=KeyboardController(native)
    await keyboard.key_down("a");await keyboard.release_all()
    assert not keyboard.held_keys
    assert any(call.args==("keyboard.key_up",) and call.kwargs=={"key":"a"} for call in native.call.call_args_list)


@pytest.mark.asyncio
async def test_app_launch_cannot_run_downloaded_application(monkeypatch):
    native=AsyncMock();native.call.return_value={"path":"/Users/username/Downloads/Installer.app","name":"Installer"}
    run=AsyncMock();monkeypatch.setattr("app.computer.controllers.process",run)
    with pytest.raises(ComputerError,match="installed application folders"):
        await ApplicationController(native).open("Installer")
    run.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_drag_releases_assistant_mouse_buttons():
    import asyncio
    native=AsyncMock();started=asyncio.Event()
    async def call(action,**arguments):
        if action=="mouse.drag":started.set();await asyncio.Event().wait()
        return {"released":True}
    native.call.side_effect=call;mouse=MouseController(native)
    task=asyncio.create_task(mouse.drag(1,2,3,4));await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert mouse.pending_buttons
    await mouse.release_all();native.call.assert_called_with("mouse.release_all")
    assert not mouse.pending_buttons
