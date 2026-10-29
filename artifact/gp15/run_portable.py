#!/usr/bin/env python3
"""Run the unchanged GP15 workflow with explicit runtime and PNG checks.

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
PRIMARY = "results/ree_ph_fixed_transfer_v1/"

# Fixed names only. The primary diagnostic must have identical decoded pixels.
# Final figures additionally require byte-identical SVG AND PDF counterparts.
FINAL_FIGURE_NAMES = (
    "fig01_sampling_support", "fig02_sampled_layer_means", "fig03_error_decomposition",
    "figS01_fixed_sensitivity", "figS02_calibration_domain",
)
PNG_RULES = {PRIMARY + "ree_ph_transfer.png": {"exact_pixels": True, "vectors": ()}}
PNG_RULES.update({FIGURES + name + ".png": {
    "exact_pixels": False, "vectors": (FIGURES + name + ".svg", FIGURES + name + ".pdf")
} for name in FINAL_FIGURE_NAMES})
MAX_FINAL_CHANNEL_DIFFERENCE = 2  # integer channel levels out of 255
MAX_FINAL_CHANGED_PIXEL_DENOMINATOR = 10000  # ratio <= 1/10000, checked as integers

# Exact manifest entries; no generic hash-field exemption is permitted.
PNG_HASH_BINDINGS = {
    PRIMARY + "run_manifest.json": [
        ("output_files", 5, "file", "ree_ph_transfer.png", PRIMARY + "ree_ph_transfer.png", ("sha256",))
    ],
    FIGURES + "figure-source-manifest.json": [
        ("outputs", index * 3, "path", FIGURES + name + ".png", FIGURES + name + ".png", ("sha256", "bytes"))
        for index, name in enumerate(FINAL_FIGURE_NAMES)
    ],
}

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


def comparison_json_bytes(name, value):
    # This one original exporter uses json.dumps(..., indent=2) without a
    # terminal newline (analyze_ree_ph_transfer.py:519). Do not infer formatting
    # from changed output or strip whitespace from other files.
    if name == PRIMARY + "run_manifest.json":
        return json.dumps(value, indent=2).encode("utf-8")
    return encoded(value)


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
        def metadata(value):
            if isinstance(value, bytes):
                return {"byte_count": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            if isinstance(value, dict):
                return {key: metadata(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [metadata(item) for item in value]
            return value

        result = {"expected_dimensions": list(left.size), "actual_dimensions": list(right.size),
                  "expected_mode": left.mode, "actual_mode": right.mode,
                  "expected_format": left.format, "actual_format": right.format,
                  "expected_frame_count": getattr(left, "n_frames", 1), "actual_frame_count": getattr(right, "n_frames", 1),
                  "expected_rgba_sha256": hashlib.sha256(a.tobytes()).hexdigest(),
                  "actual_rgba_sha256": hashlib.sha256(b.tobytes()).hexdigest(),
                  "expected_metadata": metadata(left.info), "actual_metadata": metadata(right.info),
                  "metadata_identical": left.info == right.info}
        if a.shape == b.shape:
            result["different_pixels"] = int(np.any(a != b, axis=2).sum())
            result["total_pixels"] = int(a.shape[0] * a.shape[1])
            result["different_pixel_ratio"] = result["different_pixels"] / result["total_pixels"]
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
        if name in PNG_RULES:
            try:
                rule = PNG_RULES[name]
                diagnostic = png_diagnostic(expected, actual)
                row["png_diagnostic"] = diagnostic
                row["png_rule"] = {
                    "exact_decoded_rgba_required": rule["exact_pixels"],
                    "maximum_channel_difference": 0 if rule["exact_pixels"] else MAX_FINAL_CHANNEL_DIFFERENCE,
                    "maximum_changed_pixel_ratio": 0 if rule["exact_pixels"] else 1 / MAX_FINAL_CHANGED_PIXEL_DENOMINATOR,
                    "required_byte_identical_vectors": list(rule["vectors"]),
                    "dimensions_mode_format_frame_count_and_metadata_must_match": True,
                }
                vectors = [self.compare_one(vector) for vector in rule["vectors"]]
                row["vector_dependencies"] = [{key: vector.get(key) for key in
                    ("file", "byte_identical", "reference_sha256", "recomputed_sha256")} for vector in vectors]
                structure = (diagnostic["expected_dimensions"] == diagnostic["actual_dimensions"]
                             and diagnostic["expected_mode"] == diagnostic["actual_mode"]
                             and diagnostic["expected_format"] == diagnostic["actual_format"] == "PNG"
                             and diagnostic["expected_frame_count"] == diagnostic["actual_frame_count"] == 1
                             and diagnostic["metadata_identical"])
                if rule["exact_pixels"]:
                    pixels = diagnostic.get("different_pixels") == 0
                else:
                    pixels = (diagnostic.get("maximum_channel_difference", 256) <= MAX_FINAL_CHANNEL_DIFFERENCE
                              and diagnostic.get("different_pixels", 1) * MAX_FINAL_CHANGED_PIXEL_DENOMINATOR
                              <= diagnostic.get("total_pixels", 0))
                if structure and pixels and all(vector["byte_identical"] for vector in vectors):
                    row.update(passed=True, comparison=("PNG_DECODED_RGBA_IDENTICAL" if diagnostic["different_pixels"] == 0
                               else "PNG_BOUNDED_RASTER_DIFFERENCE_WITH_BYTE_IDENTICAL_VECTORS"))
                else:
                    row["error"] = "PNG structure, metadata, pixel threshold or required vector-byte comparison failed"
            except Exception as error:
                row["error"] = "PNG comparison failed: " + str(error)
            return row
        if name in RUNTIME_FIELDS or name in RUNTIME_HASH_BINDINGS or name in PNG_HASH_BINDINGS:
            try:
                reference_json, actual_json = json.loads(a), json.loads(b)
                # This exact serializer is verified against each unchanged reference.
                # Rebuilding permitted fields therefore cannot conceal formatting,
                # numeric-lexeme, field-order, extra-field or scientific differences.
                if comparison_json_bytes(name, reference_json) != a:
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
                if name in PNG_HASH_BINDINGS:
                    for collection, index, identity_key, identity_value, bound_name, fields in PNG_HASH_BINDINGS[name]:
                        bound_result = self.compare_one(bound_name)
                        if not bound_result["passed"]:
                            raise ValueError("Referenced PNG failed its declared pixel/vector checks")
                        for document in (reference_json, actual_json):
                            if document[collection][index][identity_key] != identity_value:
                                raise ValueError("PNG hash binding changed its declared target")
                        for key in fields:
                            pointer = (collection, index, key)
                            if key == "sha256":
                                old, new = sha(self.reference / bound_name), sha(self.working / bound_name)
                            else:
                                old, new = (self.reference / bound_name).stat().st_size, (self.working / bound_name).stat().st_size
                            if nested(reference_json, pointer) != old or nested(actual_json, pointer) != new:
                                raise ValueError("PNG hash/length binding does not match the actual file")
                            assign(reconstructed, pointer, new)
                            if old != new:
                                allowed.append({"pointer": list(pointer), "expected": old, "actual": new,
                                                "reason": "Verified binding to a PNG that passed its fixed pixel/vector policy",
                                                "bound_file": bound_name})
                row["allowed_metadata_differences"] = allowed
                if comparison_json_bytes(name, reconstructed) == b and allowed:
                    png_binding_changed = any(change.get("bound_file") in PNG_RULES for change in allowed)
                    row.update(passed=True, comparison=("IDENTICAL_EXCEPT_DECLARED_METADATA_AND_PNG_BINDINGS" if png_binding_changed
                                                       else "IDENTICAL_EXCEPT_DECLARED_RUNTIME_METADATA"))
                    return row
                row["unpermitted_json_differences"] = json_differences(reconstructed, actual_json)
                row["error"] = "Bytes differ beyond the explicit runtime/PNG-binding whitelist"
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
    png_changes = [row for row in rows if row["passed"] and row.get("comparison", "").startswith("PNG_")]
    comparison_status = ("BYTE_IDENTICAL" if byte_identical else
                         "SCIENTIFIC_DATA_AND_VECTORS_BYTE_IDENTICAL_WITH_DECLARED_RUNTIME_AND_PNG_DIFFERENCES" if png_changes else
                         "SCIENTIFIC_RESULTS_IDENTICAL_WITH_DECLARED_RUNTIME_DIFFERENCES")
    report = {
        "schema": "gp15-portable-complete-reproduction/v2", "status": "PASS" if passed else "FAIL",
        "comparison_status": comparison_status if passed else "UNPERMITTED_DIFFERENCES_OR_EXECUTION_FAILURE",
        "at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "runtime": {"python": sys.version, "platform": platform.platform(), "dependencies": dependencies},
        "wrapper_sha256": sha(Path(__file__)), "strict_runner_sha256": sha(PACKAGE / "run_reproduction.py"),
        "package_manifest_sha256": sha(PACKAGE / "package-manifest.json"), "export_plan_sha256": sha(PACKAGE / "export-plan.json"),
        "stage_count": len(results), "file_comparison_count": len(rows),
        "byte_identical_file_count": sum(row["byte_identical"] for row in rows),
        "runtime_metadata_only_file_count": sum(row.get("comparison") == "IDENTICAL_EXCEPT_DECLARED_RUNTIME_METADATA" for row in rows),
        "verified_png_binding_file_count": sum(row.get("comparison") == "IDENTICAL_EXCEPT_DECLARED_METADATA_AND_PNG_BINDINGS" for row in rows),
        "png_exact_pixel_file_count": sum(row.get("comparison") == "PNG_DECODED_RGBA_IDENTICAL" for row in rows),
        "png_bounded_raster_difference_file_count": sum(row.get("comparison") == "PNG_BOUNDED_RASTER_DIFFERENCE_WITH_BYTE_IDENTICAL_VECTORS" for row in rows),
        "vector_files_byte_identical": all(row["byte_identical"] for row in rows if row["file"].endswith((".svg", ".pdf"))),
        "failed_file_comparison_count": sum(not row["passed"] for row in rows),
        "allowed_metadata_differences": differences, "coverage_errors": comparison.coverage_errors,
        "allowed_png_differences": png_changes,
        "stages": results, "inspected_results": inspections,
        "exports_verified_before_and_after": len(manifest["files"]), "source_exports_modified": False,
        "generated_outputs_rewritten_by_wrapper": False,
        "scope": "Fresh execution of all ten frozen research stages. Scientific data and SVG/PDF retain strict byte comparison. Four explicit Python-runtime fields and their verified manifest entries may differ. Six named PNGs have explicit decoded-pixel checks: the primary PNG must be pixel-identical; five final PNGs also require byte-identical SVG/PDF, identical dimensions/mode/metadata, max channel difference 2/255 and changed-pixel ratio <=0.0001. Only their verified manifest hash/length fields may then differ. No file is rewritten. This is research-case reproducibility, not system semantic accuracy or anonymous peer review.",
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
