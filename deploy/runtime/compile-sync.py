#!/usr/bin/env python3
"""Drive the Web tier's durable background work queue from outside the Worker.

The Web tier owns every piece of background work as rows in D1 (Compile
progression and result import, retrieval indexing, Claim-relation analysis).
A Worker cannot run work on its own, so this pump repeatedly asks it to do one
bounded pass through the loopback-only, credential-protected
``POST /api/internal/work``. All state, leases, retries and progress live in
D1; this process holds none, reads no database files and never contacts the
Compiler or a model provider. Runs with the macOS system Python (3.9).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

TICK_BUDGET_MS = 20_000
IDLE_SECONDS = 5.0
BUSY_PAUSE_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 60.0
MAX_RESPONSE_BYTES = 256 * 1024
# A tick is bounded by its budget; this is only a liveness bound for one call.
REQUEST_TIMEOUT_SECONDS = 180


def read_shared_secret(path: Path) -> str:
    """Read COMPILER_SHARED_SECRET from a dotenv-style file (never logged)."""

    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry.startswith("export "):
            entry = entry[len("export "):].strip()
        if not entry.startswith("COMPILER_SHARED_SECRET="):
            continue
        value = entry.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            return value
    raise ValueError("COMPILER_SHARED_SECRET is not configured")


def tick(base_url: str, secret: str, budget_ms: int = TICK_BUDGET_MS) -> dict:
    url = urlsplit(base_url)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.path not in ("", "/")):
        raise ValueError("The work pump must call the installed loopback Web")
    request = Request(
        base_url.rstrip("/") + "/api/internal/work",
        data=json.dumps({"budget_ms": budget_ms}).encode("utf-8"),
        method="POST",
        headers={
            "authorization": f"Bearer {secret}",
            "content-type": "application/json",
        },
    )
    with build_opener(ProxyHandler({})).open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Work tick response exceeds its size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("Work tick did not succeed")
    return payload


def summarize(payload: dict) -> dict:
    """Counters only: no job IDs, document text, URLs or credentials."""

    compile_summary = payload.get("compile") or {}
    work = payload.get("work") or {}
    return {
        "active_compile_jobs": compile_summary.get("active", 0),
        "compile_advanced": compile_summary.get("advanced", 0),
        "compile_completed": compile_summary.get("completed", 0),
        "compile_failed": compile_summary.get("failed", 0),
        "compile_errors": len(compile_summary.get("errors") or []),
        "work_claimed": work.get("claimed", 0),
        "work_succeeded": work.get("succeeded", 0),
        "work_retried": work.get("retried", 0),
        "work_failed": work.get("failed", 0),
        "open_work": payload.get("open", 0),
        "more": payload.get("more") is True,
    }


def next_delay(result: dict, failures: int) -> float:
    if failures:
        return min(MAX_BACKOFF_SECONDS, 2.0 ** min(failures, 6))
    return BUSY_PAUSE_SECONDS if result.get("more") else IDLE_SECONDS


def write_status(path: Path, result: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False) + "\n")
    temporary.replace(path)


def run_once(config: dict, secret: str) -> tuple[dict, bool]:
    try:
        return summarize(tick(config["web_base_url"], secret)), True
    except HTTPError as error:
        # The Web answers with a JSON error; only its status is recorded.
        return {"errors": [{"error": f"HTTP {error.code}"}]}, False
    except Exception as error:  # noqa: BLE001 - recorded by class name only
        return {"errors": [{"error": type(error).__name__}]}, False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    status_path = Path(config["status_file"])
    status_path.parent.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    failures = 0
    with status_path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while not stopped.is_set():
            try:
                # Re-read each pass so a rotated secret takes effect without a restart.
                secret = read_shared_secret(Path(config["secret_file"]))
                result, ok = run_once(config, secret)
            except Exception as error:  # noqa: BLE001 - e.g. unreadable secret file
                result, ok = {"errors": [{"error": type(error).__name__}]}, False
            failures = 0 if ok else failures + 1
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            write_status(status_path, result)
            if not ok or result.get("compile_advanced") or result.get("work_claimed"):
                print(json.dumps(result), flush=True)
            if args.once:
                break
            stopped.wait(next_delay(result, failures))


if __name__ == "__main__":
    main()
