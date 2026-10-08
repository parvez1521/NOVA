"""Verify the already-launched NOVA shell restarts only its own crashed sidecar."""
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

DATA=Path.home()/"Library/Application Support/local.nova.desktop"


def status():return json.loads((DATA/"native-status.json").read_text())
def snapshot():
    with sqlite3.connect(f"file:{DATA/'backend/data/nova.db'}?mode=ro",uri=True) as connection:
        assert not connection.execute("SELECT id FROM tasks WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED','INTERRUPTED')").fetchall(),"Finish current tasks before a recovery test"
        return {table:connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("messages","memories","tasks")}


def main():
    before=status();assert before["status"]=="ready";saved=snapshot()
    pid=int(subprocess.check_output(["lsof","-t",f"-iTCP:{before['base_url'].rsplit(':',1)[1]}","-sTCP:LISTEN"],text=True).strip())
    command=subprocess.check_output(["ps","-p",str(pid),"-o","command="],text=True)
    assert "/NOVA.app/Contents/MacOS/nova-backend" in command or "/NOVA.app/Contents/MacOS/nova-backend-" in command
    os.kill(pid,signal.SIGKILL)
    deadline=time.monotonic()+80
    while time.monotonic()<deadline:
        time.sleep(1);after=status()
        if after["status"]=="ready" and after["restarts"]>before["restarts"]:break
    else:raise AssertionError("Native supervisor did not recover")
    assert after["pid"]!=before["pid"] and after["base_url"]==before["base_url"] and snapshot()==saved
    print("native_shell_recovery:",json.dumps({"backend_before":before["pid"],"backend_after":after["pid"],"restart_count":after["restarts"],"data_preserved":saved,"development_backend_untouched":True}))


if __name__=="__main__":main()
