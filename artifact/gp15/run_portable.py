#!/usr/bin/env python3
"""Run the unchanged ten-stage GP15 workflow with a narrow runtime whitelist.

The original run_reproduction.py remains the strict byte-for-byte entry point.
This wrapper changes comparison policy only. It never rewrites a reference,
scientific program, generated result, protocol, or runtime field on disk.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "reproduction"
ANALYSIS = "iteration-2026-10-27/analysis/"
FIGURES = "iteration-2026-10-27/figures/final_v1/"

# Exact file/pointer pairs, not field-name or directory wildcards.
RUNTIME_FIELDS = {
    ANALYSIS + "review-tables-v1/source_manifest.json": ("python_version",),
    ANALYSIS + "continuous-box-v1/source_manifest.json": ("python_version",),
    ANALYSIS + "selection-weight-domain-v1/input_manifest.json": ("interpreter",),
    FIGURES + "figure-source-manifest.json": ("environment", "python"),
}

# Only these three output entries can inherit a changed runtime file's digest
# and length. Both reference and fresh bindings must match their actual files.
RUNTIME_HASH_BINDINGS = {
    ANALYSIS + "review-tables-v1/output_manifest.json": (10, "source_manifest.json"),
    ANALYSIS + "continuous-box-v1/output_manifest.json": (5, "source_manifest.json"),
    ANALYSIS + "selection-weight-domain-v1/output_manifest.json": (3, "input_manifest.json"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def nested(value, pointer):
    for part in pointer:
        value = value[part]
    return value


def assign(value, pointer, replacement):
    nested(value, pointer[:-1])[pointer[-1]] = replacement


def json_differences(expected, actual, pointer=()):
    """Diagnostic only; acceptance uses exact reconstructed bytes below."""
    if type(expected) is not type(actual):
        return [{"pointer": list(pointer), "expected": expected, "actual": actual}]
    if isinstance(expected, dict):
        differences = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                differences.append({"pointer": list(pointer + (key,)),
                                    "expected": expected.get(key), "actual": actual.get(key),
                                    "missing_from": "expected" if key not in expected else "actual"})
            else:
                differences.extend(json_differences(expected[key], actual[key], pointer + (key,)))
        return differences
    if isinstance(expected, list) and len(expected) == len(actual):
        return [d for index, (left, right) in enumerate(zip(expected, actual))
                for d in json_differences(left, right, pointer + (index,))]
    return [] if expected == actual else [
        {"pointer": list(pointer), "expected": expected, "actual": actual}]


def png_diagnostic(expected, actual):
    from PIL import Image
    import numpy as np
    with Image.open(expected) as left, Image.open(actual) as right:
        a, b = np.asarray(left.convert("RGBA")), np.asarray(right.convert("RGBA"))
        result = {"expected_dimensions": list(left.size), "actual_dimensions": list(right.size),
                  "expected_mode": left.mode, "actual_mode": right.mode,
                  "expected_rgba_sha256": hashlib.sha256(a.tobytes()).hexdigest(),
                  "actual_rgba_sha256": hashlib.sha256(b.tobytes()).hexdigest(),
                  "expected_metadata_keys": sorted(left.info), "actual_metadata_keys": sorted(right.info)}
        if a.shape == b.shape:
            result["different_pixels"] = int(np.any(a != b, axis=2).sum())
            result["maximum_channel_difference"] = int(np.abs(a.astype("int16") - b.astype("int16")).max())
        return result


class PortableComparison:
    def __init__(self, reference, working, runtime=None):
        self.reference, self.working = Path(reference), Path(working)
        self.runtime = runtime or sys.version
        self.cache = {}
        self.by_stage = {}
        self.coverage_errors = []

    def compare_one(self, name):
        if name in self.cache:
            return self.cache[name]
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Comparison path must be contained")
        expected, actual = self.reference / name, self.working / name
        row = {"file": name, "passed": False, "byte_identical": False}
        self.cache[name] = row
        if not expected.is_file() or not actual.is_file():
            row["error"] = "Missing reference or freshly generated file"
            return row
        a, b = expected.read_bytes(), actual.read_bytes()
        row.update(reference_sha256=sha(expected), recomputed_sha256=sha(actual),
                   reference_bytes=len(a), recomputed_bytes=len(b), byte_identical=(a == b))
        if a == b:
            row.update(passed=True, comparison="BYTE_IDENTICAL")
            return row
        row["first_different_byte"] = next((i for i, pair in enumerate(zip(a, b)) if pair[0] != pair[1]), min(len(a), len(b)))
        if name in RUNTIME_FIELDS or name in RUNTIME_HASH_BINDINGS:
            try:
                reference_json, actual_json = json.loads(a), json.loads(b)
                # This exact serializer is verified against each unchanged reference.
                # Rebuilding permitted fields therefore cannot conceal formatting,
                # numeric-lexeme, field-order, extra-field or scientific differences.
                if encoded(reference_json) != a:
                    raise ValueError("Reference serialization differs from the declared exact serializer")
                allowed = []
                reconstructed = copy.deepcopy(reference_json)
                if name in RUNTIME_FIELDS:
                    pointer = RUNTIME_FIELDS[name]
                    want = self.runtime.split()[0] if pointer == ("python_version",) else self.runtime
                    got = nested(actual_json, pointer)
                    if got != want:
                        raise ValueError("Runtime field does not match the interpreter executing this run")
                    previous = nested(reference_json, pointer)
                    assign(reconstructed, pointer, got)
                    if got != previous:
                        allowed.append({"pointer": list(pointer), "expected": previous, "actual": got,
                                        "reason": "Explicit Python runtime metadata field"})
                if name in RUNTIME_HASH_BINDINGS:
                    index, filename = RUNTIME_HASH_BINDINGS[name]
                    bound_name = str(PurePosixPath(name).parent / filename)
                    bound_result = self.compare_one(bound_name)
                    if not bound_result["passed"]:
                        raise ValueError("Referenced runtime file has an unpermitted difference")
                    for document in (reference_json, actual_json):
                        if document["outputs"][index]["path"] != filename:
                            raise ValueError("Runtime hash binding changed its declared target")
                    for key, old, new in (
                        ("sha256", sha(self.reference / bound_name), sha(self.working / bound_name)),
                        ("bytes", (self.reference / bound_name).stat().st_size, (self.working / bound_name).stat().st_size),
                    ):
                        pointer = ("outputs", index, key)
                        if nested(reference_json, pointer) != old or nested(actual_json, pointer) != new:
                            raise ValueError("Runtime hash/length binding does not match the actual file")
                        assign(reconstructed, pointer, new)
                        if old != new:
                            allowed.append({"pointer": list(pointer), "expected": old, "actual": new,
                                            "reason": "Verified binding to a runtime-only changed file",
                                            "bound_file": bound_name})
                row["allowed_metadata_differences"] = allowed
                if encoded(reconstructed) == b and allowed:
                    row.update(passed=True, comparison="IDENTICAL_EXCEPT_DECLARED_RUNTIME_METADATA")
                    return row
                row["unpermitted_json_differences"] = json_differences(reconstructed, actual_json)
                row["error"] = "Bytes differ beyond the explicit runtime whitelist"
            except (ValueError, KeyError, TypeError, IndexError) as error:
                row["error"] = str(error)
        else:
            row["error"] = "Strict byte comparison failed; this file has no runtime exception"
            if name.endswith(".json"):
                try:
                    row["unpermitted_json_differences"] = json_differences(json.loads(a), json.loads(b))
                except (ValueError, UnicodeError):
                    pass
        if name.endswith(".png"):
            try:
                row["png_diagnostic"] = png_diagnostic(expected, actual)
            except Exception as error:
                row["png_diagnostic_error"] = str(error)
        return row

    def compare_outputs(self, reference, working, stage):
        if Path(reference) != self.reference or Path(working) != self.working:
            raise RuntimeError("Unexpected execution tree")
        rows = [self.compare_one(name) for name in stage.get("compare_files", [])]
        self.by_stage[stage["name"]] = rows
        if stage.get("output_directory"):
            directory = self.working / stage["output_directory"]
            expected = {Path(name).name for name in stage["compare_files"]}
            actual = {p.name for p in directory.iterdir() if p.is_file()} if directory.is_dir() else set()
            if actual != expected:
                self.coverage_errors.append({"stage": stage["name"], "expected": sorted(expected), "actual": sorted(actual)})
        return rows


def locked_dependencies():
    result = {}
    for line in (PACKAGE / "requirements-research.lock").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, expected = line.split("==")
        actual = importlib.metadata.version(name)
        if expected != actual:
            raise RuntimeError(f"Install the unchanged dependency lock: {name} requires {expected}, found {actual}")
        result[name] = actual
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New run directory outside artifact/gp15; existing paths are rejected")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12 with the preserved dependency lock")
    dependencies = locked_dependencies()
    spec = importlib.util.spec_from_file_location("gp15_strict_runner", PACKAGE / "run_reproduction.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    plan = json.loads((PACKAGE / "export-plan.json").read_text())
    manifest = json.loads((PACKAGE / "package-manifest.json").read_text())
    runner.demand_final(plan)
    runner.manifest_check(manifest)
    if args.out:
        work = args.out.resolve()
        if work.exists() or work == ROOT or ROOT in work.parents:
            raise RuntimeError("--out must be a new directory outside artifact/gp15")
        work.mkdir(parents=True)
    else:
        work = Path(tempfile.mkdtemp(prefix="gp15-portable-")).resolve()
    reference, tree = PACKAGE / "payload", work / "working_copy"
    shutil.copytree(reference, tree)
    comparison = PortableComparison(reference, tree)
    # Reuse every original stage command, deletion rule, receipt expectation and
    # output-coverage contract. Only the comparison callback changes in memory.
    runner.compare_outputs = comparison.compare_outputs
    stages = [*plan["stages"], plan["publication_stage"]]
    results, execution_errors = [], []
    for index, stage in enumerate(stages, 1):
        try:
            result = runner.run_stage(stage, reference, tree, work, index)
            result["execution_and_receipt_checks_passed"] = True
        except Exception as error:
            message = str(error).replace(str(work), "<run-directory>")
            result = {"stage": stage["name"], "execution_and_receipt_checks_passed": False,
                      "error": message, "comparisons": comparison.by_stage.get(stage["name"], [])}
            execution_errors.append({"stage": stage["name"], "error": message})
        results.append(result)
        print(json.dumps({"stage": stage["name"], "execution_and_receipt_checks_passed": result["execution_and_receipt_checks_passed"],
                          "failed_file_comparisons": sum(not row["passed"] for row in result["comparisons"])}), flush=True)
    inspections = []
    for item in plan.get("result_inspections", []):
        try:
            data = json.loads((tree / runner.relative_path(item["file"])).read_text())
            actual = runner.get_nested(data, item["pointer"])
            inspection = {"label": item["label"], "file": item["file"], "pointer": item["pointer"],
                          "expected": item["expected"], "actual": actual, "passed": actual == item["expected"]}
        except Exception as error:
            inspection = {"label": item["label"], "passed": False, "error": str(error).replace(str(work), "<run-directory>")}
        inspections.append(inspection)
    runner.manifest_check(manifest)
    rows = [row for result in results for row in result["comparisons"]]
    differences = [{"file": row["file"], **change} for row in rows for change in row.get("allowed_metadata_differences", [])]
    passed = not execution_errors and not comparison.coverage_errors and all(row["passed"] for row in rows) and all(x["passed"] for x in inspections)
    byte_identical = passed and all(row["byte_identical"] for row in rows)
    report = {
        "schema": "gp15-portable-complete-reproduction/v1", "status": "PASS" if passed else "FAIL",
        "comparison_status": ("BYTE_IDENTICAL" if byte_identical else "SCIENTIFIC_RESULTS_IDENTICAL_WITH_DECLARED_RUNTIME_DIFFERENCES") if passed else "UNPERMITTED_DIFFERENCES_OR_EXECUTION_FAILURE",
        "at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "runtime": {"python": sys.version, "platform": platform.platform(), "dependencies": dependencies},
        "wrapper_sha256": sha(Path(__file__)), "strict_runner_sha256": sha(PACKAGE / "run_reproduction.py"),
        "package_manifest_sha256": sha(PACKAGE / "package-manifest.json"), "export_plan_sha256": sha(PACKAGE / "export-plan.json"),
        "stage_count": len(results), "file_comparison_count": len(rows),
        "byte_identical_file_count": sum(row["byte_identical"] for row in rows),
        "runtime_metadata_only_file_count": sum(row["passed"] and not row["byte_identical"] for row in rows),
        "failed_file_comparison_count": sum(not row["passed"] for row in rows),
        "allowed_metadata_differences": differences, "coverage_errors": comparison.coverage_errors,
        "stages": results, "inspected_results": inspections,
        "exports_verified_before_and_after": len(manifest["files"]), "source_exports_modified": False,
        "generated_outputs_rewritten_by_wrapper": False,
        "scope": "Fresh execution of all ten frozen research stages. Only four explicit Python-runtime fields and three verified dependent manifest entries may differ. Scientific data and all PNG/SVG/PDF files retain strict byte comparison. This is research-case reproducibility, not system semantic accuracy or anonymous peer review.",
    }
    target = work / "portable-verification.json"
    target.write_bytes(encoded(report))
    print(json.dumps({"status": report["status"], "comparison_status": report["comparison_status"],
                      "report": str(target), "stages": len(results), "file_comparisons": len(rows),
                      "allowed_metadata_difference_count": len(differences),
                      "failed_file_comparisons": report["failed_file_comparison_count"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
