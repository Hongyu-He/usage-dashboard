"""Idempotent, project-owned start/status/stop. Safe to invoke over SSH."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

from collector import ROOT, config


def health(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def owned(state):
    if not state or state.get("root") != str(ROOT) or state.get("service") != "usage-dashboard":
        return False
    try:
        pid = int(state["pid"])
        if Path(f"/proc/{pid}").stat().st_uid != os.getuid():
            return False
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return str(ROOT / "server.py").encode() in args
    except (OSError, ValueError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "status", "stop", "connection-info"])
    command = parser.parse_args().command
    cfg = config()
    port = cfg["port"]
    if command == "connection-info":
        print(json.dumps({"port": port, "root": str(ROOT)}))
        return
    state = health(port)
    if command == "status":
        print(json.dumps({"running": owned(state), "health": state}, ensure_ascii=False))
        raise SystemExit(0 if owned(state) else 1)
    if command == "stop":
        if not state:
            print("Not running; no process was signaled.")
            return
        if not owned(state):
            raise SystemExit("Port belongs to an unverified process; refusing to stop it.")
        os.kill(state["pid"], signal.SIGTERM)
        for _ in range(64):
            if not owned(state):
                print("Stopped usage-dashboard.")
                return
            time.sleep(0.25)
        raise SystemExit("Stop is still pending; inspect this project's run/server.log.")
    if owned(state):
        print(f"Already running: http://127.0.0.1:{port}")
        return
    if state:
        raise SystemExit("Port is occupied by a different service; choose a different port in local-settings.json.")
    run = ROOT / "run"
    run.mkdir(exist_ok=True)
    with (run / "server.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py")],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    for _ in range(60):
        state = health(port)
        if owned(state):
            print(f"Ready: http://127.0.0.1:{port}")
            return
        if child.poll() is not None:
            raise SystemExit("Server failed to start; inspect run/server.log (possibly port in use).")
        time.sleep(0.25)
    raise SystemExit("Startup is still pending; inspect run/server.log before retrying.")


if __name__ == "__main__":
    main()
