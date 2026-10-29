#!/usr/bin/env python3
"""Export existing exposed GP15 results for SI display and review QC only.

Standard library only. Requires a root-frozen, exact input/code SHA plan.
Does not fit, validate, select bottles, change weights, or rerun the analysis.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
from pathlib import Path
import sys

SCOPE = ("Existing already-exposed data: deterministic presentation and reviewer "
         "QC only; post-exposure descriptive work, not new validation, inference, "
         "calibration, fitting, or changed eligibility/weighting.")
SCRIPT_RELATIVE = "iteration-2026-10-27/analysis/export_review_tables_v1.py"
INPUT_WHITELIST = {
    "primary_layers": "results/ree_ph_fixed_transfer_v1/layer_summary.csv",
    "original_samples": "results/ree_ph_fixed_transfer_v1/sample_predictions.csv",
    "common_layers": "revision-2026-10-27/analysis/results-v2/common_station_layers.csv",
    "shape_layers": "revision-2026-10-27/analysis/results-v2/abundance_shape_layers.csv",
    "paas": "ree/round8/raw/Lin2024-paas.csv",
    "molar_mass": "ree/round8/raw/Lin2024-molar_mass_mgmol.csv",
    "leg1_ph": "ree/round8/raw/GP15-leg1-pH.csv",
    "leg2_ph": "ree/round8/raw/GP15-leg2-pH.csv",
    "depth_conflicts": "revision-2026-10-27/analysis/results-v2/depth_conflicts.csv",
}
LEGS = ("leg1", "leg2")
ELEMENTS = ("Sm", "Gd", "Dy", "Er")
COMMON = {"leg1": {"16", "18"}, "leg2": {"31", "33"}}
EXPECTED_PRIMARY = {"leg1": 24, "leg2": 55}
PH_FIELDS = {
    "leg1": {"bottle": "SD1_PH_TOT_BOTTLE_gr5eba", "fish": "SD1_PH_TOT_FISH_8srxc6"},
    "leg2": {"bottle": "SD1_PH_TOT_BOTTLE_wp5r6m", "fish": "SD1_PH_TOT_FISH_ejuhua"},
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def demand(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def ident(value):
    value = str(value).strip()
    demand(value.lower() not in ("", "nd", "na", "nan"), "Missing identifier")
    try:
        parsed = Decimal(value)
        return format(parsed.normalize(), "f") if parsed.is_finite() else value
    except InvalidOperation:
        return value


def sort_ids(values):
    return sorted(values, key=lambda value: Decimal(value))


def read_csv(path):
    """Track both CSV record ordinal and actual physical source line range."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows, previous_end = [], reader.line_num
        for record, row in enumerate(reader, 1):
            row["_csv_record"] = record
            row["_csv_line_start"] = previous_end + 1
            row["_csv_line_end"] = reader.line_num
            previous_end = reader.line_num
            rows.append(row)
    return rows, fields


def csv_text(rows):
    demand(bool(rows), "Declared CSV output is empty")
    fields = list(rows[0])
    demand(all(list(row) == fields for row in rows), "Inconsistent output CSV schema")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                         if isinstance(value, (list, dict)) else value
                         for key, value in row.items()})
    return stream.getvalue()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def row_ref(role, row):
    return {"source_path": INPUT_WHITELIST[role], "csv_record": row["_csv_record"],
            "physical_csv_lines": [row["_csv_line_start"], row["_csv_line_end"]]}


def keyed_rows(rows, key):
    result = {}
    for row in rows:
        value = key(row)
        demand(value not in result, "Duplicate source key: " + str(value))
        result[value] = row
    return result


def sorted_layers(rows):
    return sorted(rows, key=lambda row: (LEGS.index(row["leg"]), int(row["bin_index"])))


def layer_label(row):
    return row["low_m"] + "–" + row["high_m"]


def export(primary, samples, common, shapes, raw, fields):
    artifacts = {}
    primary = sorted_layers([row for row in primary if row["variant"] == "primary"])
    demand(len(primary) == 12, "Exactly twelve primary layer rows required")
    primary_keys = keyed_rows(primary, lambda row: (row["leg"], int(row["bin_index"])))
    demand(set(primary_keys) == {(leg, index) for leg in LEGS for index in range(6)},
           "All six primary layers required on each leg")
    primary_ids = {leg: set().union(*(set(json.loads(row["sample_ids"]))
                                    for row in primary if row["leg"] == leg)) for leg in LEGS}
    demand({leg: len(ids) for leg, ids in primary_ids.items()} == EXPECTED_PRIMARY,
           "Unexpected primary unique-bottle support")
    sample_index = keyed_rows(samples, lambda row: (row["leg"], ident(row["sample_id"])))

    score_rows = []
    score_md = ["# Primary layer scoring values", "", SCOPE, "",
                "The score is RMSE of six equally weighted layer mean errors. It is not "
                "RMSE of individual bottle errors. Each primary within-layer mean uses "
                "equal bottle weights. Inclusive-boundary memberships can repeat bottles. "
                "All pH values are total scale at 25 °C; signed errors are prediction minus measured pH.", ""]
    for leg in LEGS:
        score_md += ["## " + leg, "",
                     "|Layer m|Bottles/stations|Measured pH|REE pH|Reference pH|REE error|Reference error|",
                     "|---|---:|---:|---:|---:|---:|---:|"]
        for row in (row for row in primary if row["leg"] == leg):
            for prediction, error in (("predicted_ph", "prediction_error"), ("baseline_ph", "baseline_error")):
                demand(abs(float(row[prediction]) - float(row["observed_ph"]) - float(row[error])) < 1e-12,
                       "Stored signed-error identity failed")
            ids = json.loads(row["sample_ids"])
            demand(len(ids) == int(row["n_records"]), "Layer record count disagrees with sample IDs")
            demand(all((leg, sid) in sample_index for sid in ids), "Primary sample missing from original identity table")
            score_rows.append({key: row[key] for key in
                               ("leg", "bin_index", "low_m", "high_m", "n_records", "n_stations",
                                "observed_ph", "predicted_ph", "baseline_ph", "prediction_error", "baseline_error")})
            score_rows[-1].update(sample_ids=ids, source_csv_record=row["_csv_record"],
                                  source_csv_line=row["_csv_line_start"])
            vals = [f"{float(row[key]):.6f}" for key in
                    ("observed_ph", "predicted_ph", "baseline_ph", "prediction_error", "baseline_error")]
            score_md.append("|" + "|".join([layer_label(row), row["n_records"] + "/" + row["n_stations"], *vals]) + "|")
        score_md += [""]
    score_md += ["CSV values retain original stored numeric tokens; Markdown is rounded to six decimals. "
                 "Source: " + INPUT_WHITELIST["primary_layers"] + ", variant=primary.", ""]
    artifacts["primary_layer_scores.csv"] = csv_text(score_rows)
    artifacts["primary_layer_scores.md"] = "\n".join(score_md)

    common = sorted_layers(common)
    demand(len(common) == 12, "Exactly twelve common-station layer rows required")
    common_keys = keyed_rows(common, lambda row: (row["leg"], int(row["bin_index"])))
    demand(set(common_keys) == set(primary_keys), "Common-station layer coverage differs")
    event_rows = []
    event_md = ["# Common-station event support", "", SCOPE, "",
                "Station labels are held constant by the existing station-equal diagnostic; "
                "bottles and weights are unchanged. Constant station labels do not establish "
                "a single-event or same-cast profile. Event IDs are source identifiers, not "
                "new independent replicates.", ""]
    for leg in LEGS:
        event_md += ["## " + leg, "", "|Station|200–300|300–400|400–500|500–600|600–700|700–800|",
                     "|---|---|---|---|---|---|---|"]
        matrix = {}
        for index in range(6):
            row = common_keys[leg, index]
            demand(set(json.loads(row["stations"])) == COMMON[leg], "Unexpected common station labels")
            ids = json.loads(row["sample_ids"])
            demand(len(ids) == int(row["n_records"]), "Common membership count differs")
            demand(set(ids) <= primary_ids[leg], "Common diagnostic contains non-primary bottle")
            members = [sample_index[leg, sid] for sid in ids]
            for station in sort_ids(COMMON[leg]):
                group = [member for member in members if ident(member["station"]) == station]
                demand(bool(group), "Common station missing from declared occupied layer")
                events = sort_ids({ident(member["event"]) for member in group})
                group_ids = sort_ids({ident(member["sample_id"]) for member in group})
                matrix[station, index] = ", ".join(events)
                event_rows.append({"leg": leg, "station": station, "bin_index": index,
                                   "low_m": row["low_m"], "high_m": row["high_m"],
                                   "event_ids": events, "n_memberships": len(group), "sample_ids": group_ids,
                                   "common_layer_csv_record": row["_csv_record"],
                                   "sample_prediction_csv_records": [member["_csv_record"] for member in group],
                                   "original_ree_csv_lines": [int(member["ree_line"]) for member in group],
                                   "original_ph_csv_lines": [int(member["ph_line"]) for member in group]})
        for station in sort_ids(COMMON[leg]):
            event_md.append("|" + "|".join([station, *(matrix[station, index] for index in range(6))]) + "|")
        event_md += [""]
    event_md += ["Sources: " + INPUT_WHITELIST["common_layers"] + " (fixed membership) and "
                 + INPUT_WHITELIST["original_samples"] + " (station/event identity and raw row locators).", ""]
    artifacts["common_station_events.csv"] = csv_text(event_rows)
    artifacts["common_station_events.md"] = "\n".join(event_md)

    shapes = sorted_layers([row for row in shapes if row["weighting"] == "record"])
    demand(len(shapes) == 12, "Exactly twelve stored record-weighted shape rows required")
    demand(set(keyed_rows(shapes, lambda row: (row["leg"], int(row["bin_index"])))) == set(primary_keys),
           "Shape layer coverage differs")
    shape_rows = []
    shape_md = ["# Stored record-weighted abundance and relative-shape descriptions", "", SCOPE, "",
                "A is the mean of the four shale-normalized molar concentrations; s is the "
                "slope after dividing the weighted mean composition by its A. The listed "
                "s is s_of_mean_composition, not the mean of bottle-level s. Alpha=A*s is "
                "an algebraic identity, not a causal partition. Geographical support differs "
                "between legs and changes between layers. These post-exposure descriptions "
                "do not isolate abundance, mixing or fractionation mechanisms.", ""]
    for leg in LEGS:
        shape_md += ["## " + leg, "", "|Layer m|Bottles/stations|A ×10⁸|s of mean composition|Alpha ×10⁸|",
                     "|---|---:|---:|---:|---:|"]
        for row in (row for row in shapes if row["leg"] == leg):
            prior = primary_keys[leg, int(row["bin_index"])]
            demand(set(json.loads(row["sample_ids"])) == set(json.loads(prior["sample_ids"])),
                   "Shape and primary memberships differ")
            demand(row["n_records"] == prior["n_records"] and row["n_stations"] == prior["n_stations"],
                   "Shape and primary membership counts differ")
            demand(abs(float(row["mean_A"]) * float(row["s_of_mean_composition"]) - float(row["mean_alpha"])) < 1e-20,
                   "Stored layer abundance-shape identity failed")
            shape_rows.append({key: row[key] for key in
                               ("leg", "bin_index", "low_m", "high_m", "n_records", "n_stations",
                                "mean_A", "s_of_mean_composition", "mean_alpha")})
            shape_rows[-1].update(weighting="record", sample_ids=json.loads(row["sample_ids"]),
                                  source_csv_record=row["_csv_record"], interpretation=row["interpretation"])
            shape_md.append(f"|{layer_label(row)}|{row['n_records']}/{row['n_stations']}|"
                            f"{float(row['mean_A']) * 1e8:.6f}|{float(row['s_of_mean_composition']):.6f}|"
                            f"{float(row['mean_alpha']) * 1e8:.6f}|")
        shape_md += [""]
    shape_md += ["Source values are copied from " + INPUT_WHITELIST["shape_layers"]
                 + "; no feature or target is re-estimated. CSV retains original tokens; "
                 "Markdown rescales A/alpha only for display.", ""]
    artifacts["abundance_shape_record_layers.csv"] = csv_text(shape_rows)
    artifacts["abundance_shape_record_layers.md"] = "\n".join(shape_md)

    paas, mass = raw["paas"], raw["molar_mass"]
    demand(len(paas) == len(mass) == 1, "Normalization sources must have one data row")
    constants = []
    constants_md = ["# Original four-element normalization constants", "", SCOPE, "",
                    "|Element|PAAS μg g⁻¹|Source molar mass mg mol⁻¹|Converted molar mass g mol⁻¹|",
                    "|---|---:|---:|---:|"]
    for element in ELEMENTS:
        mg = Decimal(mass[0][element]); grams = mg * Decimal("0.001")
        constants.append({"element": element, "paas_ug_g": paas[0][element],
                          "source_molar_mass_mg_mol": mass[0][element],
                          "converted_molar_mass_g_mol": format(grams, "f"),
                          "source_paas_csv_record": paas[0]["_csv_record"],
                          "source_mass_csv_record": mass[0]["_csv_record"]})
        constants_md.append(f"|{element}|{paas[0][element]}|{mass[0][element]}|{grams:.3f}|")
    constants_md += ["", "Conversion: source mg mol⁻¹ ×10⁻³ gives g mol⁻¹. "
                     "PAAS mol g⁻¹ = PAAS μg g⁻¹ ×10⁻⁶ / molar mass g mol⁻¹. "
                     "Seawater pmol kg⁻¹ ×10⁻¹⁵ gives mol g⁻¹. These are the author's "
                     "adopted source constants; no updated atomic-weight substitution is made.", "",
                     "Sources: " + INPUT_WHITELIST["paas"] + " and " + INPUT_WHITELIST["molar_mass"] + ".", ""]
    artifacts["normalization_constants.csv"] = csv_text(constants)
    artifacts["normalization_constants.md"] = "\n".join(constants_md)

    sd_report = {"scope": SCOPE,
                 "meaning": "Counts of finite source SD tokens describe availability of reported repeat-measurement support only. A finite SD is not a replicate count, accuracy certificate, independent sample, or complete measurement-error distribution. No missing SD is imputed; zero SD remains zero.",
                 "primary_definition": "Union of original primary layer sample IDs within each leg; unique bottle identity, not layer memberships. Raw pH source line must match original sample_predictions ph_line.",
                 "legs": {}}
    for leg in LEGS:
        role = leg + "_ph"
        ph_rows = raw[role]
        indexed = keyed_rows(ph_rows, lambda row: ident(row["Sample_ID"]))
        demand(primary_ids[leg] <= set(indexed), "Primary IDs missing from raw pH table")
        for sid in primary_ids[leg]:
            demand(indexed[sid]["_csv_line_start"] == int(sample_index[leg, sid]["ph_line"]),
                   "Raw pH physical line and original identity locator differ")
        result = {"source_path": INPUT_WHITELIST[role], "n_all_source_records": len(ph_rows),
                  "n_unique_primary_bottles": len(primary_ids[leg]), "sd_fields": {}}
        any_ids = set()
        for treatment, field in PH_FIELDS[leg].items():
            demand(field in fields[role], "Required source SD column missing")
            finite_rows = [row for row in ph_rows if number(row[field]) is not None]
            finite_ids = {ident(row["Sample_ID"]) for row in finite_rows}
            intersection = sort_ids(finite_ids & primary_ids[leg])
            any_ids |= finite_ids
            result["sd_fields"][treatment] = {
                "field": field, "selection": "All original table rows with finite numeric SD; no flag, depth or pH eligibility restriction for this source count.",
                "n_finite_sd_records_all_rows": len(finite_rows),
                "n_finite_sd_unique_sample_ids_all_rows": len(finite_ids),
                "n_zero_sd_records_all_rows": sum(number(row[field]) == 0 for row in finite_rows),
                "n_negative_sd_records_all_rows": sum(number(row[field]) < 0 for row in finite_rows),
                "n_primary_intersection_unique_ids": len(intersection),
                "primary_intersection_sample_ids": intersection,
                "finite_records": [{"sample_id": ident(row["Sample_ID"]), "station": ident(row["Station_ID"]),
                                    "event": ident(row["Event_ID"]), "reported_sd_token": row[field],
                                    "in_original_primary": ident(row["Sample_ID"]) in primary_ids[leg],
                                    **row_ref(role, row)} for row in finite_rows]}
        result["any_sd_field_union"] = {"n_unique_sample_ids": len(any_ids),
                                         "n_source_rows_with_any_finite_sd": sum(
                                             any(number(row[field]) is not None for field in PH_FIELDS[leg].values())
                                             for row in ph_rows),
                                         "n_primary_intersection_unique_ids": len(any_ids & primary_ids[leg]),
                                         "primary_intersection_sample_ids": sort_ids(any_ids & primary_ids[leg])}
        sd_report["legs"][leg] = result
    sd_report["overall"] = {
        "n_all_source_records": sum(sd_report["legs"][leg]["n_all_source_records"] for leg in LEGS),
        "n_unique_primary_bottles": sum(EXPECTED_PRIMARY.values()),
        "by_sd_treatment": {
            treatment: {
                "n_finite_sd_source_records": sum(sd_report["legs"][leg]["sd_fields"][treatment]["n_finite_sd_records_all_rows"] for leg in LEGS),
                "n_primary_intersection_bottles": sum(sd_report["legs"][leg]["sd_fields"][treatment]["n_primary_intersection_unique_ids"] for leg in LEGS),
                "primary_intersection_ids": [{"leg": leg, "sample_id": sid} for leg in LEGS
                                             for sid in sd_report["legs"][leg]["sd_fields"][treatment]["primary_intersection_sample_ids"]]}
            for treatment in ("bottle", "fish")},
        "any_sd_field_union": {
            "n_source_rows_with_any_finite_sd": sum(sd_report["legs"][leg]["any_sd_field_union"]["n_source_rows_with_any_finite_sd"] for leg in LEGS),
            "n_primary_intersection_bottles": sum(sd_report["legs"][leg]["any_sd_field_union"]["n_primary_intersection_unique_ids"] for leg in LEGS),
            "primary_intersection_ids": [{"leg": leg, "sample_id": sid} for leg in LEGS
                                         for sid in sd_report["legs"][leg]["any_sd_field_union"]["primary_intersection_sample_ids"]]}}
    artifacts["ph_sd_support.json"] = json_text(sd_report)

    conflicts = raw["depth_conflicts"]
    selected = [row for row in conflicts if row["eligible_primary"] == "True"]
    demand(len(selected) == 53, "Unexpected primary nonzero-conflict count")
    for row in selected:
        demand(row["identity_matches"] == "True", "Nonmatching primary conflict identity")
        demand(ident(row["sample_id"]) in primary_ids[row["leg"]], "Depth conflict not in original primary support")
        original = sample_index[row["leg"], ident(row["sample_id"])]
        demand(int(row["ree_csv_line"]) == int(original["ree_line"])
               and int(row["ph_csv_line"]) == int(original["ph_line"]),
               "Conflict raw row locators differ from original identity table")
        demand(number(row["depth_difference_ree_minus_ph_m"]) is not None
               and float(row["depth_difference_ree_minus_ph_m"]) != 0, "Expected finite nonzero depth conflict")
    keyed_rows(selected, lambda row: (row["leg"], ident(row["sample_id"])))
    def depth_counts(rows):
        values = [abs(Decimal(row["depth_difference_ree_minus_ph_m"])) for row in rows]
        return {"n_nonzero_conflicts": len(values),
                "cumulative_counts": {"0_lt_abs_le_2_m": sum(v <= 2 for v in values),
                                      "0_lt_abs_le_10_m": sum(v <= 10 for v in values),
                                      "abs_gt_10_m": sum(v > 10 for v in values)},
                "disjoint_counts": {"0_lt_abs_le_2_m": sum(v <= 2 for v in values),
                                    "2_lt_abs_le_10_m": sum(2 < v <= 10 for v in values),
                                    "abs_gt_10_m": sum(v > 10 for v in values)}}
    depth_report = {"scope": SCOPE, "source_path": INPUT_WHITELIST["depth_conflicts"],
                    "selection": "Existing nonzero-depth-conflict rows with eligible_primary=True; no new exclusion or depth reassignment.",
                    "denominators": {"all_primary_unique_bottles": sum(EXPECTED_PRIMARY.values()),
                                     "primary_nonzero_conflicts": len(selected)},
                    "threshold_note": "The <=2 and <=10 requests are cumulative and overlap. Separate disjoint bins are also supplied; zero differences are absent from the input and are not counted as nonzero conflicts.",
                    "overall": depth_counts(selected),
                    "by_leg": {leg: depth_counts([row for row in selected if row["leg"] == leg]) for leg in LEGS},
                    "records": [{"leg": row["leg"], "sample_id": row["sample_id"],
                                 "station": row["station"], "event": row["event"],
                                 "ree_reported_depth_m": row["ree_reported_depth_m"],
                                 "ph_reported_depth_m": row["ph_reported_depth_m"],
                                 "signed_difference_ree_minus_ph_m": row["depth_difference_ree_minus_ph_m"],
                                 "abs_difference_m": format(abs(Decimal(row["depth_difference_ree_minus_ph_m"])), "f"),
                                 "original_ree_csv_line": int(row["ree_csv_line"]),
                                 "original_ph_csv_line": int(row["ph_csv_line"]),
                                 "raw_ree_source": row["ree_source"], "raw_ree_sha256": row["ree_sha256"],
                                 "raw_ph_source": row["ph_source"], "raw_ph_sha256": row["ph_sha256"],
                                 **row_ref("depth_conflicts", row)}
                                for row in sorted(selected, key=lambda row: (LEGS.index(row["leg"]), Decimal(row["sample_id"])))]}
    artifacts["primary_depth_conflict_distribution.json"] = json_text(depth_report)
    return artifacts


def main(plan_path, output_path):
    demand(sys.version_info[:2] == (3, 12), "Run only with the approved Python 3.12 runtime")
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text())
    demand(plan.get("schema") == "gp15-existing-result-display-plan/v1", "Unknown plan schema")
    demand(plan.get("frozen") is True and plan.get("frozen_by") == "root", "ROOT_FREEZE_REQUIRED")
    demand(plan.get("task_root_relative") == "../..", "Unexpected task root contract")
    task_root = (plan_path.parent / plan["task_root_relative"]).resolve()
    script = Path(__file__).resolve()
    demand(script == task_root / SCRIPT_RELATIVE, "Unexpected exporter script location")
    demand(sha(script) == plan.get("analysis_script_sha256"), "Script SHA differs from root freeze")
    entries = keyed_rows(plan["inputs"], lambda entry: entry["role"])
    demand(set(entries) == set(INPUT_WHITELIST), "Missing or extra input role")
    sources = {}
    for role, relative in INPUT_WHITELIST.items():
        demand(entries[role]["path"] == relative, "Input path not on exact whitelist")
        path = task_root / relative
        demand(path.resolve() == path, "Symlinked or noncanonical input rejected")
        demand(sha(path) == entries[role]["sha256"], "Input SHA mismatch: " + role)
        sources[role] = path
    relative_out = Path(plan["output_relative"])
    demand(not relative_out.is_absolute() and ".." not in relative_out.parts,
           "Output must use a contained relative path")
    expected_out = (task_root / relative_out).resolve()
    allowed_parent = (task_root / "iteration-2026-10-27/analysis").resolve()
    demand(expected_out.parent == allowed_parent, "Output must be a new direct child of iteration analysis")
    demand(output_path.resolve() == expected_out, "Output differs from root plan")
    demand(not expected_out.exists(), "Output directory already exists; never overwrite")
    raw, fields = {}, {}
    for role, path in sources.items():
        raw[role], fields[role] = read_csv(path)
    artifacts = export(raw["primary_layers"], raw["original_samples"], raw["common_layers"],
                       raw["shape_layers"], raw, fields)
    demand(set(artifacts) == set(plan["outputs"]), "Artifact names differ from frozen plan")
    source_manifest = {"scope": SCOPE, "plan_path": str(plan_path.relative_to(task_root)),
                       "plan_sha256": sha(plan_path), "script_path": SCRIPT_RELATIVE,
                       "script_sha256": sha(script), "python_version": sys.version.split()[0],
                       "inputs": plan["inputs"], "artifact_definitions": plan["artifact_definitions"],
                       "unchanged": ["original sources and outputs", "bottle membership", "weights", "model coefficients", "primary estimand"],
                       "no_network": True, "no_fitting": True, "no_new_validation": True}
    artifacts["source_manifest.json"] = json_text(source_manifest)
    # Recheck bindings before the first output write; no source file is ever opened for writing.
    for role, path in sources.items():
        demand(sha(path) == entries[role]["sha256"], "Input changed during export: " + role)
    demand(sha(script) == plan["analysis_script_sha256"], "Script changed during export")
    demand(sha(plan_path) == source_manifest["plan_sha256"], "Plan changed during export")
    expected_out.mkdir(exist_ok=False)
    manifest_rows = []
    for name in sorted(artifacts):
        payload = artifacts[name].encode("utf-8")
        with (expected_out / name).open("xb") as handle:
            handle.write(payload)
        manifest_rows.append({"path": name, "bytes": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {"scope": SCOPE, "outputs": manifest_rows,
                "self_hash_omitted": "output_manifest.json cannot contain its own SHA; all other outputs including source_manifest.json are hashed.",
                "output_relative": str(relative_out), "source_manifest": "source_manifest.json"}
    with (expected_out / "output_manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(manifest))
    print(json_text({"status": "EXISTING_RESULT_DISPLAY_EXPORT_COMPLETE", "scope": SCOPE,
                     "output": str(expected_out), "n_files": len(artifacts) + 1}), end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    main(args.plan, args.out)
