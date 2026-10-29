#!/usr/bin/env python3
"""Re-run the preserved GP15 raw-source and exact-contrast checkers offline.

This thin entry point delegates scientific calculations to the unchanged
research package; it does not implement a second analysis or query DeepShovel.
"""
from __future__ import annotations

import datetime
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "reproduction"


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12, as declared by the preserved research package")
    spec = importlib.util.spec_from_file_location("gp15_runner", PACKAGE / "run_reproduction.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    plan = json.loads((PACKAGE / "export-plan.json").read_text())
    manifest = json.loads((PACKAGE / "package-manifest.json").read_text())
    runner.demand_final(plan)
    runner.manifest_check(manifest)
    claims = json.loads((ROOT / "claim-source-map.json").read_text())
    n_claim_bindings = 0
    for claim in claims["claims"]:
        for source in claim["bound_sources"]:
            path = ROOT / runner.relative_path(source["export_path"])
            if runner.sha(path) != source["sha256"]:
                raise RuntimeError("Claim source hash mismatch: " + source["export_path"])
            n_claim_bindings += 1
    names = ["independent_original", "independent_selection_B"]
    by_name = {stage["name"]: stage for stage in plan["stages"]}
    stages = [by_name[name] for name in names]
    with tempfile.TemporaryDirectory(prefix="gp15-demo-check-") as temporary:
        work = Path(temporary)
        tree = work / "working_copy"
        source = PACKAGE / "payload"
        shutil.copytree(source, tree)
        results = []
        for index, stage in enumerate(stages, 1):
            try:
                results.append(runner.run_stage(stage, source, tree, work, index))
            except Exception:
                log = work / (f"{index:02d}-" + stage["name"] + ".log")
                if log.exists():
                    print(log.read_text(), file=sys.stderr)
                raise
        original = json.loads((tree / stages[0]["receipt"]).read_text())
        contrast = json.loads((tree / stages[1]["receipt"]).read_text())
        primary = {
            row["leg"]: {key: row[key] for key in (
                "model_rmse", "baseline_rmse", "delta_rmse_model_minus_baseline",
                "n_nonempty_layers", "full_domain_support")}
            for row in original["four_variant_aggregates"] if row["variant"] == "primary"
        }
        differences = [
            {**row, "difference_decimal": float(Fraction(row["difference_exact"]))}
            for row in contrast["exact_layer_error_differences"] if row["leg"] == "leg2"
        ]
        if len(differences) != 6 or not all(Fraction(row["difference_exact"]) > 0 for row in differences):
            raise RuntimeError("C12 requires all six unchanged Leg 2 squared-error contrasts to be positive")
        runner.manifest_check(manifest)
        report = {
            "schema": "deepshovel-www-gp15-demo-check/v1",
            "status": "PASS",
            "at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "python": sys.version.split()[0],
            "package_manifest_sha256": runner.sha(PACKAGE / "package-manifest.json"),
            "export_plan_sha256": runner.sha(PACKAGE / "export-plan.json"),
            "claim_source_bindings_checked": n_claim_bindings,
            "exports_verified_before_and_after": len(manifest["files"]),
            "source_exports_modified": False,
            "stages": results,
            "primary_rmse": primary,
            "matched_support": {
                leg: {key: values[key] for key in (
                    "primary_unique_bottles", "primary_layer_memberships", "layer_records", "layer_stations")}
                for leg, values in original["support"].items()
            },
            "leg2_squared_error_contrasts": differences,
            "C12": {
                "all_six_differences_strictly_positive": True,
                "minimum_difference": min(row["difference_decimal"] for row in differences),
                "argument": contrast["depth_weight_argument"],
            },
            "scope": "Fresh execution of the two preserved checkers against public raw sources and fixed reference outputs. Numerical reproducibility of this research case; not a benchmark of retrieval, semantic extraction or entailment. Run reproduction/run_reproduction.py for the complete ten-stage regeneration including figures.",
        }
    print(json.dumps(report, indent=2) + "\n", end="")


if __name__ == "__main__":
    main()
