#!/usr/bin/env python3
"""Small, dependency-free guards for the installed service lifecycle."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def locked_exec(path: Path, command: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise RuntimeError(f"Another DeepShovel operation is still running ({path.name}); wait for it to finish.")
    # Keep the lock across exec, including any service children still shutting
    # down. Never unlink a lock file: another process may hold its inode open.
    os.set_inheritable(descriptor, True)
    os.execvp(command[0], command)


def lock_busy(path: Path) -> bool:
    try:
        handle = path.open("r+")
    except FileNotFoundError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def busy_resources(root: Path, ports: list[int]) -> list[str]:
    busy = []
    for port in ports:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                busy.append(f"port {port}")
    for relative in ("service-supervisor.lock", "compiler/retrieval/qdrant/.lock"):
        if lock_busy(root / "data" / relative):
            busy.append(relative)
    return busy


def wait_stopped(root: Path, ports: list[int], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        busy = busy_resources(root, ports)
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Shutdown has not released " + ", ".join(busy) + "; startup aborted. No processes or data were removed.")
        time.sleep(0.2)


def health_ready(url: str, compiler: bool = False) -> bool:
    result = subprocess.run(
        ["curl", "--silent", "--fail", "--max-time", "3", url],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode:
        return False
    try:
        health = json.loads(result.stdout)
        if compiler:
            # Remote MinerU availability is independent of local startup.
            return health.get("status") == "ok" and health.get("service") == "deepshovel-compiler" and health.get("retrieval", {}).get("status") == "ready"
        return health.get("status") == "ok" and health.get("service") == "deepshovel"
    except (ValueError, AttributeError):
        return False


def wait_ready(urls: list[str], timeout: float, compiler: bool = False) -> None:
    deadline = time.monotonic() + timeout
    while True:
        pending = [url for index, url in enumerate(urls) if not health_ready(url, compiler and index == 1)]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Still waiting for health: " + ", ".join(pending) + ". Services remain running; inspect the service logs before restarting.")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    lock = commands.add_parser("lock")
    lock.add_argument("path", type=Path)
    lock.add_argument("command", nargs=argparse.REMAINDER)
    stopped = commands.add_parser("stopped")
    stopped.add_argument("root", type=Path)
    stopped.add_argument("ports", type=int, nargs="+")
    stopped.add_argument("--timeout", type=float, default=30)
    ready = commands.add_parser("ready")
    ready.add_argument("urls", nargs="+")
    ready.add_argument("--compiler", action="store_true")
    ready.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    try:
        if args.action == "lock":
            locked_exec(args.path, args.command)
        elif args.action == "stopped":
            wait_stopped(args.root, args.ports, args.timeout)
        else:
            wait_ready(args.urls, args.timeout, args.compiler)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"DeepShovel: {error}", file=sys.stderr)
        raise SystemExit(75)


if __name__ == "__main__":
    main()
