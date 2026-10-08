"""Create a free local installer without Finder automation or user-app writes."""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    app=ROOT/"src-tauri/target/release/bundle/macos/NOVA.app"
    output=ROOT/"src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg"
    output.parent.mkdir(parents=True,exist_ok=True)
    (ROOT/"build").mkdir(parents=True,exist_ok=True)
    if not app.is_dir():raise RuntimeError("Build NOVA.app first")
    with tempfile.TemporaryDirectory(prefix="nova-installer-",dir=ROOT/"build") as directory:
        staging=Path(directory)
        shutil.copytree(app,staging/"NOVA.app")
        (staging/"Applications").symlink_to("/Applications",target_is_directory=True)
        subprocess.run(["/usr/bin/hdiutil","create","-volname","NOVA","-srcfolder",str(staging),"-format","UDZO","-ov",str(output)],check=True)
    subprocess.run(["/usr/bin/hdiutil","verify",str(output)],check=True)
    print("Installer:",output)


if __name__=="__main__":main()
