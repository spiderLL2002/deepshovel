#!/usr/bin/env python3
"""Reproduce a root-bound export in a fresh disposable task tree.

This entry point is prepared during active research. It cannot run a draft
publication binding and does not declare a manuscript or review cycle complete.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("A contained task-relative path is required: " + str(value))
    return Path(*path.parts)


def demand_final(plan):
    if (plan.get("status") != "ROOT_FROZEN_FOR_EXPORT" or
            plan.get("frozen_by") != "root" or
            plan.get("publication_bindings_finalized") is not True or
            not plan.get("publication_stage")):
        raise RuntimeError("Publication bindings are still pending; this is preparation only")


def manifest_check(manifest):
    for item in manifest["files"]:
        path = ROOT / relative_path(item["export_path"])
        if not path.is_file() or sha(path) != item["export_sha256"]:
            raise RuntimeError("Export binding failed: " + item["export_path"])


def get_nested(value, pointer):
    for part in pointer:
        value = value[part]
    return value


def compare_outputs(reference, working, stage):
    comparisons = []
    for name in stage.get("compare_files", []):
        rel = relative_path(name)
        want, got = reference / rel, working / rel
        if not got.is_file():
            raise RuntimeError("Stage did not generate " + name)
        row = {"file": name, "reference_sha256": sha(want),
               "recomputed_sha256": sha(got), "byte_identical": sha(want) == sha(got)}
        comparisons.append(row)
        if not row["byte_identical"]:
            raise RuntimeError("Byte mismatch in " + stage["name"] + ": " + name)
    if stage.get("output_directory"):
        directory = relative_path(stage["output_directory"])
        expected = {Path(p).name for p in stage["compare_files"]}
        actual = {p.name for p in (working / directory).iterdir() if p.is_file()}
        if expected != actual:
            raise RuntimeError("Output-file coverage changed in " + stage["name"])
    return comparisons


def run_stage(stage, source, tree, work, index):
    for name in stage.get("remove_generated", []):
        path = tree / relative_path(name)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    command = [sys.executable, "-B", stage["script"], *stage.get("args", [])]
    completed = subprocess.run(command, cwd=tree, capture_output=True, text=True)
    log = work / (f"{index:02d}-" + stage["name"] + ".log")
    log.write_text(completed.stdout + "\nSTDERR\n" + completed.stderr)
    if completed.returncode:
        raise RuntimeError("Stage failed; inspect local log " + str(log))
    result = {"stage": stage["name"], "exit_code": completed.returncode,
              "comparisons": compare_outputs(source, tree, stage)}
    if stage.get("receipt"):
        receipt_path = tree / relative_path(stage["receipt"])
        if not receipt_path.is_file():
            raise RuntimeError("Stage did not generate its independent receipt")
        receipt = json.loads(receipt_path.read_text())
        checks = []
        for expectation in stage["receipt_expectations"]:
            actual = get_nested(receipt, expectation["pointer"])
            if actual != expectation["value"]:
                raise RuntimeError("Independent receipt differs in " + stage["name"] +
                                   ": " + "/".join(expectation["pointer"]))
            checks.append({"pointer": expectation["pointer"], "actual": actual, "passed": True})
        result.update({"independent_receipt_checks": checks,
                       "fresh_receipt_sha256": sha(receipt_path)})
    for name in stage.get("require_generated", []):
        if not (tree / relative_path(name)).is_file():
            raise RuntimeError("Missing fresh artifact " + name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New working directory outside this export")
    args = parser.parse_args()
    plan_path = ROOT / "export-plan.json"
    if not plan_path.is_file():
        raise RuntimeError("No final export plan exists; use prepare_export.py --check-only while research continues")
    plan = json.loads(plan_path.read_text())
    demand_final(plan)
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use the declared Python 3.12 runtime and locked dependencies")
    manifest = json.loads((ROOT / "package-manifest.json").read_text())
    manifest_check(manifest)
    if args.out:
        work = args.out.resolve()
        if work == ROOT or ROOT in work.parents or work.exists():
            raise RuntimeError("--out must be a new directory outside this export")
        work.mkdir(parents=True)
    else:
        work = Path(tempfile.mkdtemp(prefix="gp15-iteration-reproduction-")).resolve()
    source, tree = ROOT / "payload", work / "working_copy"
    shutil.copytree(source, tree)
    stages = [*plan["stages"], plan["publication_stage"]]
    results = [run_stage(stage, source, tree, work, n)
               for n, stage in enumerate(stages, 1)]
    inspections = []
    for item in plan.get("result_inspections", []):
        data = json.loads((tree / relative_path(item["file"])).read_text())
        actual = get_nested(data, item["pointer"])
        if actual != item["expected"]:
            raise RuntimeError("Actual scientific/support result differs: " + item["label"])
        inspections.append({"label": item["label"], "file": item["file"],
                            "pointer": item["pointer"], "actual": actual, "passed": True})
    manifest_check(manifest)
    report = {"status": "PASS", "at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "python": sys.version, "working_directory": str(work),
              "package_manifest_sha256": sha(ROOT / "package-manifest.json"),
              "export_plan_sha256": sha(plan_path), "stages": results, "inspected_results": inspections,
              "exports_verified_before_and_after": len(manifest["files"]),
              "source_exports_modified": False,
              "freshness_rule": "Each stage deletes only its declared generated destinations in the disposable copy before execution.",
              "scope": "Recomputation of exposed public-data results and declared diagnostics. No manuscript/review iteration is counted; numerical reproduction does not certify scientific truth or acceptance."}
    target = work / "verification.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "raw_local_receipt": str(target),
                      "stages": len(results), "exports_modified": False}, indent=2))


if __name__ == "__main__":
    main()
