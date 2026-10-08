"""Build reproducible local sidecar/resources; excludes .env and all user data."""
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TAURI=ROOT/"src-tauri"
RESOURCES=TAURI/"resources"


def run(*arguments):subprocess.run(arguments,check=True)


def dependencies(binary):
    return [line.strip().split(" (",1)[0] for line in subprocess.check_output(["/usr/bin/otool","-L",str(binary)],text=True).splitlines()[1:]]


def bundle_whisper():
    source=Path(shutil.which("whisper-cli") or "/missing/whisper-cli").resolve()
    if not source.is_file():raise RuntimeError("Install whisper.cpp before packaging local STT")
    destination=RESOURCES/"speech";destination.mkdir(parents=True,exist_ok=True)
    def resolve_dependency(current,name):
        if name.startswith("/opt/homebrew/"):return Path(name).resolve()
        candidate=current.parent/name.split("/",1)[1]
        if not candidate.exists():candidate=current.parent.parent/"lib"/name.split("/",1)[1]
        if not candidate.exists():candidate=source.parent.parent/"lib"/name.split("/",1)[1]
        if not candidate.exists():raise RuntimeError("Cannot resolve local speech dependency: "+name)
        return candidate.resolve()
    copied={};queue=[source]+list(Path("/opt/homebrew/opt/ggml/libexec").glob("libggml-*.so"))
    while queue:
        current=queue.pop(0).resolve()
        if current in copied:continue
        target=destination/current.name
        if target.exists():target.unlink()
        shutil.copy2(current,target);target.chmod(0o755);copied[current]=target
        for name in dependencies(current):
            if name.startswith(("/opt/homebrew/","@rpath/")):queue.append(resolve_dependency(current,name))
    for source_path,target in copied.items():
        run("/usr/bin/codesign","--remove-signature",str(target))
        for name in dependencies(source_path):
            if name.startswith(("/opt/homebrew/","@rpath/")):
                run("/usr/bin/install_name_tool","-change",name,"@loader_path/"+resolve_dependency(source_path,name).name,str(target))
        if target.suffix==".dylib":run("/usr/bin/install_name_tool","-id","@loader_path/"+target.name,str(target))
        run("/usr/bin/codesign","--force","--sign","-",str(target))
    environment={**os.environ,"GGML_BACKEND_DL_PATH":str(destination)}
    subprocess.run([str(destination/source.name),"--help"],env=environment,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    if platform.system()!="Darwin" or platform.machine()!="arm64":raise RuntimeError("This build targets Apple Silicon macOS")
    RESOURCES.mkdir(parents=True,exist_ok=True);(TAURI/"binaries").mkdir(exist_ok=True)
    run(sys.executable,"-m","PyInstaller","--noconfirm","--clean","--onefile","--name","nova-backend",
        "--distpath",str(ROOT/"build/sidecar"),"--workpath",str(ROOT/"build/pyinstaller"),"--specpath",str(ROOT/"build"),
        "--paths",str(ROOT/"backend"),"--collect-submodules","uvicorn","--collect-submodules","websockets",
        "--collect-submodules","app",str(ROOT/"backend/desktop_entry.py"))
    shutil.copy2(ROOT/"build/sidecar/nova-backend",TAURI/"binaries/nova-backend-aarch64-apple-darwin")
    sys.path.insert(0,str(ROOT/"backend"))
    from app.computer.native import NativeBridge
    native=NativeBridge(ROOT);asyncio.run(native.build())
    target=RESOURCES/"NOVA Computer Access.app"
    if target.exists():shutil.rmtree(target)
    shutil.copytree(native.bundle,target)
    run("/usr/bin/xcrun","swiftc",str(TAURI/"native/NovaVoice.swift"),"-o",str(RESOURCES/"NovaVoice"),"-framework","AVFoundation","-framework","Speech","-framework","CoreAudio","-framework","AudioToolbox",
        "-Xlinker","-sectcreate","-Xlinker","__TEXT","-Xlinker","__info_plist","-Xlinker",str(TAURI/"native/VoiceInfo.plist"))
    run("/usr/bin/codesign","--force","--sign","-","--identifier","local.nova.voice","--requirements",'=designated => identifier "local.nova.voice"',str(RESOURCES/"NovaVoice"))
    bundle_whisper()
    models=RESOURCES/"models";models.mkdir(exist_ok=True)
    source=ROOT/"backend/data/models/ggml-small.bin"
    if not source.is_file():raise RuntimeError("An installed small Whisper model is required; no automatic model download")
    target=models/source.name
    if not target.exists() or target.stat().st_size!=source.stat().st_size:shutil.copy2(source,target)
    (RESOURCES/"runtime-manifest.json").write_text(json.dumps({"version":"0.2.0","architecture":"arm64","python_bundled":True,"whisper_model":"small","user_data_included":False},indent=2))


if __name__=="__main__":main()
