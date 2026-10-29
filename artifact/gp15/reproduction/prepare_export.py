#!/usr/bin/env python3
"""Inspect a draft inventory, or stage a root-finalized portable export.

No analysis or manuscript program is executed. No publication ZIP is created.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import warnings
import zipfile

from run_reproduction import demand_final, relative_path, sha

HERE = Path(__file__).resolve().parent
PRIVATE_PATTERNS = (
    rb"/" + rb"Users/" + rb"[^/\s]+" + rb"/", rb"Authorization\s*:",
    rb"Bearer\s+[A-Za-z0-9]", rb"\brs1\.[A-Za-z0-9_-]{10,}",
    rb"https?://[^\s\"<>]*deepshovel", rb"localhost:[0-9]",
)


def private_hits(path):
    chunks = [("", path.read_bytes())]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            chunks.extend((name, archive.read(name)) for name in archive.namelist())
    return [{"member": member, "pattern": pattern.decode()}
            for member, content in chunks for pattern in PRIVATE_PATTERNS
            if re.search(pattern, content, flags=re.I)]


def preflight(plan, task):
    rows, seen, missing, changed, privacy = [], set(), [], [], []
    for item in plan["source_inventory"]:
        destination = str(relative_path(item["payload_path"]))
        if destination in seen:
            raise RuntimeError("Duplicate payload destination " + destination)
        seen.add(destination)
        source = task / relative_path(item["source_path"])
        if not source.is_file():
            missing.append(item["source_path"])
            continue
        if sha(source) != item["sha256"]:
            changed.append(item["source_path"])
        privacy.extend({"source_path": item["source_path"], **hit} for hit in private_hits(source))
        if source.suffix == ".py":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                ast.parse(source.read_text())
        rows.append(destination)
    for item in plan["packaging_assets"]:
        source = task / relative_path(item["source_path"])
        if not source.is_file():
            missing.append(item["source_path"])
        elif sha(source) != item["sha256"]:
            changed.append(item["source_path"])
        else:
            privacy.extend({"source_path": item["source_path"], **hit} for hit in private_hits(source))
    for entry in plan["original_raw_sources"]:
        if entry["path"] not in seen:
            raise RuntimeError("Original raw/supporting source omitted: " + entry["path"])
        source = task / relative_path(entry["path"])
        if not source.is_file() or sha(source) != entry["sha256"]:
            changed.append(entry["path"])
    if len(plan["original_raw_sources"]) != 17:
        raise RuntimeError("The exact 17-source scope must be preserved")
    for binding in plan["analysis_bindings"]:
        frozen = json.loads((task / relative_path(binding["plan_path"])).read_text())
        if frozen.get("frozen") is not True or frozen.get("frozen_by") != "root":
            raise RuntimeError("Scientific plan is not root frozen")
        for entry in frozen["inputs"]:
            source = task / relative_path(entry["path"])
            if not source.is_file():
                missing.append(entry["path"])
            elif sha(source) != entry["sha256"]:
                changed.append(entry["path"])
            if entry["path"] not in seen:
                raise RuntimeError("Frozen input absent from payload inventory: " + entry["path"])
        if sha(task / frozen["analysis_script"]) != frozen["analysis_script_sha256"]:
            raise RuntimeError("Frozen analysis code SHA changed")
    for stage in [*plan["stages"], *([plan["publication_stage"]] if plan.get("publication_stage") else [])]:
        if stage["script"] not in seen:
            raise RuntimeError("Stage script omitted from inventory: " + stage["script"])
        for name in stage.get("compare_files", []):
            if name not in seen:
                raise RuntimeError("Stage reference output omitted: " + name)
        generated = [*stage.get("compare_files", []), *stage.get("require_generated", [])]
        if stage.get("receipt"):
            generated.append(stage["receipt"])
        for name in generated:
            path = relative_path(name)
            if not any(path == relative_path(item) or relative_path(item) in path.parents
                       for item in stage.get("remove_generated", [])):
                raise RuntimeError("Copied result could survive freshness deletion: " + name)
    lineage = plan.get("public_lineage")
    if lineage:
        public_map = json.loads((task / relative_path(lineage["path"])).read_text())
        for group in public_map["groups"]:
            for entry in group["bindings"]:
                if entry["path"] not in seen:
                    raise RuntimeError("Public lineage target is absent from the inventory: " + entry["path"])
                source = task / relative_path(entry["path"])
                if not source.is_file() or sha(source) != entry["sha256"]:
                    changed.append(entry["path"])
    pending = plan.get("pending_bindings", [])
    successful_status = "PREFLIGHT_PASS" if plan.get("publication_bindings_finalized") else "PREFLIGHT_PASS_PENDING_PUBLICATION"
    return {"status": successful_status if not (missing or changed or privacy) else "PREFLIGHT_FAILED",
            "payload_files_checked": len(rows), "original_raw_source_count": plan["original_raw_source_count"],
            "missing": missing, "changed": changed, "privacy_hits": privacy,
            "pending_bindings": pending, "publication_bindings_finalized": plan.get("publication_bindings_finalized", False),
            "analyses_executed": 0, "publication_zip_created": False, "manuscript_or_review_iteration_count_delta": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=HERE / "export-plan.draft.json")
    parser.add_argument("--task-root", type=Path, default=HERE.parents[1])
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--out", type=Path, help="New export directory; requires root-final publication bindings")
    args = parser.parse_args()
    plan, task = json.loads(args.plan.read_text()), args.task_root.resolve()
    receipt = preflight(plan, task)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt["missing"] or receipt["changed"] or receipt["privacy_hits"]:
        raise SystemExit(2)
    if args.check_only:
        return
    demand_final(plan)
    if not args.out:
        raise RuntimeError("--out is required for staging and must name a new directory")
    out = args.out.resolve()
    if out.exists() or out == task or out in task.parents:
        raise RuntimeError("Never overwrite a task or export")
    out.mkdir(parents=True)
    files = []
    for item in plan["source_inventory"]:
        source = task / relative_path(item["source_path"])
        relative = Path("payload") / relative_path(item["payload_path"])
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"export_path": str(relative), "source_path_relative_to_task": item["public_origin_path"],
                      "source_sha256": item["sha256"], "export_sha256": sha(target), "bytes": target.stat().st_size,
                      "transformation": "none; source bytes and task-relative destination preserved"})
    for item in plan["packaging_assets"]:
        source, relative = task / relative_path(item["source_path"]), relative_path(item["export_path"])
        shutil.copy2(source, out / relative)
        files.append({"export_path": str(relative), "source_path_relative_to_task": item["source_path"],
                      "source_sha256": item["sha256"], "export_sha256": sha(out / relative),
                      "bytes": (out / relative).stat().st_size, "transformation": "new packaging file"})
    shutil.copy2(args.plan, out / "export-plan.json")
    files.append({"export_path": "export-plan.json", "source_path_relative_to_task": str(args.plan.resolve().relative_to(task)),
                  "source_sha256": sha(args.plan), "export_sha256": sha(out / "export-plan.json"),
                  "bytes": (out / "export-plan.json").stat().st_size, "transformation": "none; root-bound final export plan"})
    manifest = {"schema": "geoscience-research.iteration-reproduction/v1", "original_source_count": 17,
                "files": files, "total_bytes": sum(row["bytes"] for row in files),
                "scientific_frozen_files_modified": False,
                "note": "Manifest omits its own hash and a later path-neutral execution summary. No scientific source, result, plan or historical receipt was rewritten."}
    (out / "package-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Export staged; no calculations or publication ZIP have been produced.")


if __name__ == "__main__":
    main()
