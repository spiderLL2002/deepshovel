import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1] / "deploy/runtime"
spec = importlib.util.spec_from_file_location("lifecycle", RUNTIME / "service-lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


def alive(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


@contextlib.contextmanager
def supervisor(stubborn_worker=False):
    with tempfile.TemporaryDirectory(prefix="deepshovel lifecycle ") as temporary:
        root = Path(temporary)
        for directory in ("app/deploy/generated", "app/deploy/runtime", "app/node_modules/.bin", "secrets", "venv/bin", "fake-bin"):
            (root / directory).mkdir(parents=True)
        for name in ("run-services.sh", "service-lifecycle.py"):
            shutil.copy2(RUNTIME / name, root / "app/deploy/runtime" / name)
        stub = '''#!/usr/bin/python3
import os, pathlib, signal, time
root = pathlib.Path(__file__).resolve().parents[2] if "venv/bin" in __file__ else pathlib.Path(__file__).parent
with (root / "children").open("a") as log:
    log.write(str(os.getpid()) + "\\n")
if "venv/bin" in __file__ and (root / "stubborn").exists():
    if os.fork() == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with (root / "children").open("a") as log:
            log.write(str(os.getpid()) + "\\n")
while True:
    time.sleep(1)
'''
        for relative in ("venv/bin/deepshovel-compiler", "fake-node"):
            (root / relative).write_text(stub)
            (root / relative).chmod(0o755)
        (root / "fake-bin/curl").write_text("#!/bin/sh\nexit 0\n")
        (root / "fake-bin/curl").chmod(0o755)
        for relative in ("secrets/web.env", "secrets/compiler.env", "app/node_modules/.bin/wrangler", "app/deploy/generated/wrangler.public.json"):
            (root / relative).touch()
        (root / "app/deploy/generated/deployment.env").write_text(
            f"NODE_EXECUTABLE='{root}/fake-node'\nLOCAL_PORT=1\nCOMPILER_PORT=2\nLOCAL_HEALTH_URL=http://unused\n"
        )
        if stubborn_worker:
            (root / "stubborn").touch()
        with (root / "log").open("w+") as log:
            command = ["/bin/bash", str(root / "app/deploy/runtime/run-services.sh"), str(root)]
            process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True,
                                       env=dict(os.environ, PATH=f"{root}/fake-bin:" + os.environ["PATH"]))
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    children = (root / "children").read_text().splitlines() if (root / "children").exists() else []
                    if len(children) >= (3 if stubborn_worker else 2):
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError((root / "log").read_text())
                yield root, process, command
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


class ServiceLifecycleTests(unittest.TestCase):
    def assert_stopped(self, root, process, expected):
        self.assertEqual(process.wait(timeout=12), expected)
        events = (root / "log").read_text()
        self.assertEqual(events.count("Web started"), 1, events)
        self.assertEqual(events.count("Compiler started"), 1, events)
        for pid in (root / "children").read_text().splitlines():
            self.assertFalse(alive(int(pid)), f"child {pid} survived shutdown")
        self.assertFalse(lifecycle.lock_busy(root / "data/service-supervisor.lock"))

    def test_stop_signals_exit_instead_of_restarting_children(self):
        for stop_signal in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=stop_signal), supervisor() as (root, process, _):
                os.kill(process.pid, stop_signal)
                self.assert_stopped(root, process, 128 + stop_signal)

    def test_cleanup_survives_repeated_signals_and_reaps_orphan_worker(self):
        with supervisor(stubborn_worker=True) as (root, process, _):
            os.kill(process.pid, signal.SIGTERM)
            time.sleep(0.4)
            os.kill(process.pid, signal.SIGHUP)
            os.kill(process.pid, signal.SIGTERM)
            # Bash may defer traps during foreground sleep and process HUP first.
            status = process.wait(timeout=12)
            self.assertIn(status, (128 + signal.SIGTERM, 128 + signal.SIGHUP))
            self.assert_stopped(root, process, status)

    def test_duplicate_supervisor_is_rejected_before_starting_children(self):
        with supervisor() as (root, process, command):
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 75)
            self.assertIn("Another DeepShovel operation", result.stderr)
            self.assertIsNone(process.poll())
            os.kill(process.pid, signal.SIGTERM)
            self.assert_stopped(root, process, 143)

    def test_busy_control_lock_cannot_run_a_second_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-control.lock"
            with path.open("w") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                action = ["python3", str(RUNTIME / "service-lifecycle.py"), "lock", str(path), "/bin/echo", "ACTION RAN"]
                result = subprocess.run(action, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 75)
                self.assertNotIn("ACTION RAN", result.stdout)
            result = subprocess.run(action, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertIn("ACTION RAN", result.stdout)

    def test_shutdown_waits_for_ports_and_vector_store_lock_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as listener:
            root = Path(temporary)
            lock = root / "data/compiler/retrieval/qdrant/.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text("preserve this database lock")
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with lock.open("r+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                with self.assertRaisesRegex(RuntimeError, "port .*qdrant/.lock"):
                    lifecycle.wait_stopped(root, [port], timeout=0)
                listener.close()
                with self.assertRaisesRegex(RuntimeError, "qdrant/.lock"):
                    lifecycle.wait_stopped(root, [port], timeout=0)
            lifecycle.wait_stopped(root, [port], timeout=0)
            self.assertEqual(lock.read_text(), "preserve this database lock")

    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler unavailable")
    def test_controller_startup_grace_is_bounded_and_does_not_hide_regressions(self):
        source = (RUNTIME.parents[1] / "control_app/ServiceControl.swift").read_text()
        state_code = source[source.index("private enum CompilerState"):source.index("private struct Signals")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "main.swift"
            harness.write_text("import Foundation\n" + state_code + """
private var state = CompilerStartup()
assert(state.observe(running: false, ready: false, now: 0) == .stopped)
assert(state.observe(running: true, ready: false, now: 10) == .starting)
assert(state.observe(running: true, ready: false, now: 249) == .starting)
assert(state.observe(running: true, ready: false, now: 250) == .failed)
assert(state.observe(running: true, ready: true, now: 251) == .ready)
assert(state.observe(running: true, ready: false, now: 252) == .failed)
assert(state.observe(running: false, ready: false, now: 253) == .stopped)
assert(state.observe(running: true, ready: false, now: 254) == .starting)
assert(state.observe(running: true, ready: true, now: 255, pid: 100) == .ready)
assert(state.observe(running: true, ready: false, now: 256, pid: 101) == .starting)
assert(state.observe(running: true, ready: false, now: 496, pid: 101) == .failed)
""")
            subprocess.run(["swiftc", str(harness), "-o", str(root / "check")], check=True, capture_output=True, timeout=60)
            subprocess.run([str(root / "check")], check=True, capture_output=True, timeout=5)

    def test_readiness_rejects_degraded_or_wrong_service(self):
        for body in (
            {"status": "degraded", "service": "deepshovel-compiler", "retrieval": {"status": "ready"}},
            {"status": "ok", "service": "other", "retrieval": {"status": "ready"}},
        ):
            response = subprocess.CompletedProcess([], 0, json.dumps(body))
            with patch.object(lifecycle.subprocess, "run", return_value=response):
                self.assertFalse(lifecycle.health_ready("http://compiler/ready", compiler=True))
        with patch.object(lifecycle.subprocess, "run", return_value=subprocess.CompletedProcess([], 22, "")):
            self.assertFalse(lifecycle.health_ready("http://compiler/ready", compiler=True))

    def test_readiness_requires_warm_retrieval_and_reports_failure(self):
        for status, expected in (("cold", False), ("ready", True)):
            response = subprocess.CompletedProcess([], 0, json.dumps({
                "status": "ok", "service": "deepshovel-compiler", "retrieval": {"status": status},
            }))
            with patch.object(lifecycle.subprocess, "run", return_value=response):
                self.assertEqual(lifecycle.health_ready("http://compiler", compiler=True), expected)
        with patch.object(lifecycle, "health_ready", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Services remain running"):
                lifecycle.wait_ready(["http://web", "http://compiler"], timeout=0, compiler=True)
        with patch.object(lifecycle, "health_ready", side_effect=[True, False, True, True]), \
             patch.object(lifecycle.time, "sleep"):
            lifecycle.wait_ready(["http://web", "http://compiler"], timeout=5, compiler=True)


if __name__ == "__main__":
    unittest.main()
