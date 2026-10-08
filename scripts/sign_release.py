"""Apply NOVA's deterministic ad-hoc identity requirement to the app bundle."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri/target/release/bundle/macos/NOVA.app"


def main() -> None:
    subprocess.run([
        "/usr/bin/codesign", "--force", "--sign", "-", "--identifier", "local.nova.desktop",
        "--requirements", '=designated => identifier "local.nova.desktop"', str(APP),
    ], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    print(f"Signed stable NOVA identity: {APP}")


if __name__ == "__main__":
    main()
