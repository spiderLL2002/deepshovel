#!/usr/bin/env python3
"""Apply local D1 migrations without letting a completed Wrangler process hang."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrangler", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--persist-to", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def expected_migrations(config_path: Path) -> tuple[str, ...]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    databases = config.get("d1_databases")
    if not isinstance(databases, list):
        raise ValueError("Wrangler config has no D1 database list")
    database = next(
        (item for item in databases if isinstance(item, dict) and item.get("binding") == "DB"),
        None,
    )
    if database is None or not isinstance(database.get("migrations_dir"), str):
        raise ValueError("Wrangler config has no DB migration directory")
    directory = (config_path.parent / database["migrations_dir"]).resolve()
    names = tuple(path.name for path in sorted(directory.glob("*.sql")))
    if not names:
        raise ValueError("No D1 migration files were found")
    return names


def applied_migrations(persist_to: Path) -> set[str]:
    candidates: list[set[str]] = []
    for database in persist_to.rglob("*.sqlite"):
        if database.name == "metadata.sqlite" or database.is_symlink():
            continue
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=0.2) as connection:
                rows = connection.execute("SELECT name FROM d1_migrations").fetchall()
        except (sqlite3.Error, OSError):
            continue
        candidates.append({str(row[0]) for row in rows})
    if len(candidates) > 1:
        raise RuntimeError("Multiple local D1 migration databases were found")
    return candidates[0] if candidates else set()


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def main() -> int:
    args = parse_args()
    expected = set(expected_migrations(args.config))
    applied = applied_migrations(args.persist_to)
    unexpected = applied - expected
    if unexpected:
        raise RuntimeError(f"Database contains migrations unknown to this application: {len(unexpected)}")
    if expected <= applied:
        print("No local D1 migrations to apply.")
        return 0

    command = [
        str(args.wrangler),
        "d1",
        "migrations",
        "apply",
        "DB",
        "--local",
        "--config",
        str(args.config),
        "--persist-to",
        str(args.persist_to),
    ]
    environment = {**os.environ, "CI": "1"}
    process = subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + args.timeout_seconds
    complete = False
    try:
        while time.monotonic() < deadline:
            applied = applied_migrations(args.persist_to)
            complete = expected <= applied
            if complete:
                break
            if process.poll() is not None:
                applied = applied_migrations(args.persist_to)
                complete = expected <= applied
                break
            time.sleep(0.2)

        if complete:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                print("Wrangler completed migrations but remained open; closing it safely.")
                stop_process_group(process)
        else:
            return_code = process.poll()
            stop_process_group(process)
            if return_code is None:
                raise TimeoutError("Timed out before all local D1 migrations committed")
            raise RuntimeError(f"Wrangler exited before all migrations committed (status {return_code})")
    finally:
        stop_process_group(process)

    final_applied = applied_migrations(args.persist_to)
    if not expected <= final_applied:
        raise RuntimeError("Local D1 migration verification failed after Wrangler exited")
    print(f"Verified {len(expected)} local D1 migrations.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Local D1 migration failed: {error}", file=sys.stderr)
        raise SystemExit(70) from None
