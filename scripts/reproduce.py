#!/usr/bin/env python3
"""Reproduce recorded paper results without production services or credentials."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifact"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="A new directory for logs and generated records")
    parser.add_argument("--extended", action="store_true", help="Also run production contracts and two RDF processors; install repro/requirements.lock first")
    args = parser.parse_args()
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="deepshovel-reproduce-"))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    steps = []
    env = os.environ.copy()
    for key in list(env):
        if key in {"PYTHONPATH", "DEEPSHOVEL_REPO", "GPT_API_KEY", "DEEPSEEK_API_KEY", "COMPILER_SHARED_SECRET", "OPENALEX_API_KEY", "COMPILER_MINERU_API_TOKEN"}:
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"

    def run(name, command, cwd=ROOT, expected_success=True):
        begin = time.monotonic()
        proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
        clean = lambda s: s.replace(str(ROOT), "$CHECKOUT").replace(str(output), "$RUN")
        (output / (name + ".stdout.txt")).write_text(clean(proc.stdout))
        (output / (name + ".stderr.txt")).write_text(clean(proc.stderr))
        ok = (proc.returncode == 0) == expected_success
        steps.append({"name": name, "command": [clean(str(v)) for v in command], "exit_code": proc.returncode, "passed": ok, "seconds": round(time.monotonic() - begin, 3)})
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        if not ok:
            raise RuntimeError(f"{name}: see the captured output in {output}")
        return proc

    result = {"schema": "deepshovel-reproduction/1", "tier": "extended" if args.extended else "core", "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(), "execution": "automated functional reproduction, not a human user study", "steps": steps}
    try:
        run("file-manifest", [sys.executable, "scripts/verify-release.py"])
        for name in ["comparison-reuse-ui-export-v15.json", "inline-source-reuse-ui-export-v15.json"]:
            run("verify-" + name.removesuffix(".json"), [sys.executable, "artifact/verify_reuse.py", "--record", "artifact/second-case/" + name, "--source-pdf", "artifact/second-case/source.pdf"])
        run("build-observation-branch", [sys.executable, "artifact/prepare_handoff.py", "--result", "artifact/second-case/retrieved-result-only.json", "--graph", "artifact/second-case/full-graph.json", "--out", str(output / "derived-handoff.json")])
        derived = json.loads((output / "derived-handoff.json").read_text())
        original = json.loads((ARTIFACT / "second-case/retrieved-result-only.json").read_text())
        derived = derived.get("result", derived)
        original = original.get("result", original)
        assert all(derived[k] == v for k, v in original.items() if k != "observation_evidence_path")
        branch = derived["observation_evidence_path"]
        assert len(branch["nodes"]) == 33 and len(branch["edges"]) == 33
        run("consecutive-20-slots", [sys.executable, "artifact/handoff-cohort/check.py", "--out-dir", str(output / "cohort")])
        cohort = json.loads((output / "cohort/summary.json").read_text())
        assert cohort["slots"] == 20 and cohort["counts"]["shortest_has_both"] == 16 and cohort["counts"]["success"] == 20
        run("additional-30-paper-structures", [sys.executable, "examples/structural-corpus/check.py"])
        run("frozen-ai-diagnostic", [sys.executable, "artifact/evaluation/evaluate.py"])
        record = json.loads((ARTIFACT / "second-case/comparison-reuse-ui-export-v15.json").read_text())
        record["payload"]["source_reading"]["notes"] = "Deliberate corruption for a negative control."
        tampered = output / "tampered-record.json"
        tampered.write_text(json.dumps(record))
        run("reject-changed-payload", [sys.executable, "artifact/verify_reuse.py", "--record", str(tampered)], expected_success=False)
        wrong_pdf = output / "wrong.pdf"
        wrong_pdf.write_bytes(b"%PDF-1.4\nDeliberately incorrect file identity.\n")
        run("reject-wrong-pdf", [sys.executable, "artifact/verify_reuse.py", "--record", "artifact/second-case/comparison-reuse-ui-export-v15.json", "--source-pdf", str(wrong_pdf)], expected_success=False)
        # Exercise the actual static handler on an ephemeral loopback port.
        spec = importlib.util.spec_from_file_location("artifact_server", ARTIFACT / "server.py")
        server_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server_module)
        from functools import partial
        server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), partial(server_module.Handler, directory=str(ARTIFACT)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ["health", "generic-reuse.html", "vendor/pdfjs/build/pdf.mjs", "second-case/source.pdf"]:
                with urlopen(f"http://127.0.0.1:{server.server_port}/{path}") as response:
                    assert response.status == 200 and response.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        steps.append({"name": "browser-assets-and-health", "passed": True, "scope": "HTTP assets only; does not assert browser interaction"})
        if args.extended:
            result["versions"] = {name: importlib.metadata.version(name) for name in ["numpy", "pydantic", "httpx", "openai", "rdflib"]}
            result["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
            result["npm"] = subprocess.check_output(["npm", "--version"], text=True).strip()
            run("production-contracts", [sys.executable, "artifact/replays/run_all.py", "--out", str(output / "contracts")])
            run("rdf-python", [sys.executable, "artifact/rdf-route/check.py", "--out-dir", str(output / "rdf-python")])
            deps = output / "rdfjs-deps"
            deps.mkdir()
            for name in ["package.json", "package-lock.json"]:
                shutil.copy2(ARTIFACT / "rdfjs" / name, deps / name)
            run("rdf-js-dependencies", ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org"], cwd=deps)
            run("rdf-javascript", ["node", "artifact/rdf-route/consume.cjs", "--module", str(deps), "--input", "artifact/rdf-route/input-record.jsonld", "--out", str(output / "rdf-js")])
            run("rdf-cross-client-isomorphism", [sys.executable, "artifact/rdf-route/cross_client_isomorphic.py", "--jsonld", "artifact/rdf-route/input-record.jsonld", "--js-dataset", str(output / "rdf-js/dataset.json"), "--original-record", "artifact/second-case/comparison-reuse-ui-export-v15.json", "--js-recovered", str(output / "rdf-js/recovered-record.json"), "--out", str(output / "cross-client.json")])
        result["passed"] = True
    except Exception as exc:
        result["passed"] = False
        result["error"] = str(exc).replace(str(ROOT), "$CHECKOUT").replace(str(output), "$RUN")
        raise
    finally:
        result["seconds"] = round(time.monotonic() - started, 3)
        (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print("Report: " + str(output / "report.json"), flush=True)


if __name__ == "__main__":
    main()
