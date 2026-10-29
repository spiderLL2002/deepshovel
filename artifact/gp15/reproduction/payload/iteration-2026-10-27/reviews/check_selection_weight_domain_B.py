#!/usr/bin/env python3
"""Independent QC of C-authored displays; never imports/consults its exporter.

All GP15 values are exposed. This does not validate B's continuous-box program.
"""
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/selection-weight-domain-v1"
TASK = ROOT.parent
STATUSES = ("not_shared_sample_id", "station_or_event_mismatch", "leg2_station_or_sample_seen_in_leg1",
            "missing_finite_depth", "outside_predeclared_depth_union", "bottle_flag_not_allowed",
            "missing_or_invalid_response", "eligible_in_at_least_one_depth_definition")
ELEMENTS = ("Sm", "Gd", "Dy", "Er")
LEGS = ("leg1", "leg2")


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [dict(row, _record=n, _line=n+1) for n, row in enumerate(csv.DictReader(stream), 1)]


def identity(token):
    token = str(token).strip()
    try:
        d = Decimal(token)
        return format(d.normalize(), "f") if d.is_finite() else token
    except InvalidOperation:
        return token


def finite(token):
    try:
        value = Decimal(token)
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keyed(rows, key):
    result = {}
    for row in rows:
        k = key(row)
        assert k not in result, ("duplicate", k)
        result[k] = row
    return result


def main():
    assert sys.version_info[:2] == (3, 12)
    differences, checked = [], Counter()
    def compare(category, locator, expected, observed):
        checked[category] += 1
        if expected != observed:
            differences.append({"category": category, "locator": locator,
                                "expected": str(expected), "observed": str(observed)})
    plan_path = ROOT / "analysis/selection-weight-domain-plan-v1.frozen.json"
    plan = json.loads(plan_path.read_text())
    manifest = json.loads((OUT / "input_manifest.json").read_text())
    compare("plan_binding", "plan SHA", sha(plan_path), manifest["plan_sha256"])
    compare("plan_binding", "known root plan", "ef2af25c9c0e0ad91a51caa37fd5373a3e66473509230a269269e3ce72dd5a4f", sha(plan_path))
    inputs = {entry["role"]: TASK / entry["path"] for entry in plan["inputs"]}
    for entry in plan["inputs"]:
        compare("input_sha", entry["role"], entry["sha256"], sha(inputs[entry["role"]]))
    compare("plan_binding", "manifest input list", plan["inputs"], manifest["inputs"])
    compare("script_identity_only", "C exporter SHA", plan["analysis_script_sha256"], sha(TASK / plan["analysis_script"]))
    output_manifest = json.loads((OUT / "output_manifest.json").read_text())
    for entry in output_manifest["outputs"]:
        compare("output_sha", entry["path"], entry["sha256"], sha(OUT / entry["path"]))
        compare("output_bytes", entry["path"], entry["bytes"], (OUT / entry["path"]).stat().st_size)
    compare("output_coverage", "ten files", set(plan["outputs"]), {p.name for p in OUT.iterdir() if p.is_file()})

    raw = {(leg, kind): read(inputs[leg + "_" + kind]) for leg in LEGS for kind in ("ree", "ph")}
    idx = {key: keyed(rows, lambda row: identity(row["Sample_ID"])) for key, rows in raw.items()}
    first_ids = set(idx["leg1", "ree"]) | set(idx["leg1", "ph"])
    first_stations = {identity(row["Station_ID"]) for key, rows in raw.items() if key[0] == "leg1" for row in rows}
    original_audit = read(inputs["pairing_audit"])
    audit = keyed(original_audit, lambda row: (row["leg"], identity(row["sample_id"])))
    provenance = keyed(read(OUT / "audit_status_provenance.csv"), lambda row: (row["leg"], identity(row["sample_id"])))
    flow = keyed(read(OUT / "eligibility_flow.csv"), lambda row: (row["leg"], row["metric"]))
    layers = read(inputs["layers"])
    primary = keyed([row for row in layers if row["variant"] == "primary"], lambda row: (row["leg"], int(row["bin_index"])))
    computed, statistics, accepted = {}, {}, {}
    for leg in LEGS:
        ri, pi = idx[leg, "ree"], idx[leg, "ph"]
        universe, shared = set(ri) | set(pi), set(ri) & set(pi)
        compare("audit_universe", leg, universe, {sid for l, sid in audit if l == leg})
        compare("provenance_universe", leg, universe, {sid for l, sid in provenance if l == leg})
        status_counts, accepted[leg] = Counter(), set()
        for sid in universe:
            status = STATUSES[0]
            if sid in shared:
                r, p = ri[sid], pi[sid]
                station, event = identity(r["Station_ID"]), identity(r["Event_ID"])
                rd, pd = finite(r["Sample_Depth"]), finite(p["Sample_Depth"])
                ree_cols = {e: next(k for k in r if k.startswith(e + "_D_CONC_BOTTLE_")) for e in ELEMENTS}
                ph_col = next(k for k in p if k.startswith("PH_TOT_BOTTLE_"))
                values = [finite(r[ree_cols[e]]) for e in ELEMENTS]
                predicates = [station != identity(p["Station_ID"]) or event != identity(p["Event_ID"]),
                              leg == "leg2" and (sid in first_ids or station in first_stations),
                              rd is None or pd is None,
                              False if rd is None or pd is None else not (200 <= rd <= 800 or 200 <= pd <= 800),
                              p["Flag_" + ph_col].strip() != "2" or any(r["Flag_" + ree_cols[e]].strip() != "2" for e in ELEMENTS),
                              finite(p[ph_col]) is None or any(v is None or v <= 0 for v in values)]
                status = next((STATUSES[n+1] for n, failed in enumerate(predicates) if failed), STATUSES[-1])
                # Predicates are evaluated for QC, but only the first failure determines the terminal class.
                a = audit[leg, sid]
                for key, expected in (("ree_line", r["_line"]), ("ph_line", p["_line"])):
                    compare("original_raw_line", f"{leg}/{sid}/{key}", expected, int(a[key]))
            computed[leg, sid] = status
            status_counts[status] += 1
            if status == STATUSES[-1]:
                accepted[leg].add(sid)
            a, export = audit[leg, sid], provenance[leg, sid]
            compare("terminal_status_vs_original", f"{leg}/{sid}", status, a["status"])
            compare("terminal_status_vs_C", f"{leg}/{sid}", status, export["original_status"])
            for key, expected in (("audit_csv_record", a["_record"]), ("audit_physical_line", a["_line"]),
                                  ("present_ree", sid in ri), ("present_ph", sid in pi)):
                compare("C_provenance", f"{leg}/{sid}/{key}", str(expected), export[key])
            for key, expected in (("raw_ree_physical_line", ri[sid]["_line"] if sid in ri else ""),
                                  ("raw_ph_physical_line", pi[sid]["_line"] if sid in pi else ""),
                                  ("original_station_token", a.get("station", "")),
                                  ("original_event_token", a.get("event", "")),
                                  ("original_ree_depth_token", a.get("ree_reported_depth", "")),
                                  ("original_ph_depth_token", a.get("ph_reported_depth", ""))):
                compare("C_provenance", f"{leg}/{sid}/{key}", str(expected), export[key])
        primary_ids = {sid for sid in accepted[leg] if 200 <= finite(pi[sid]["Sample_Depth"]) <= 800}
        memberships = 0
        for index in range(6):
            ids = {sid for sid in accepted[leg] if 200+100*index <= finite(pi[sid]["Sample_Depth"]) <= 300+100*index}
            row = primary[leg, index]
            compare("primary_layer_membership", f"{leg}/{index}", ids, set(json.loads(row["sample_ids"])))
            compare("primary_layer_count", f"{leg}/{index}", len(ids), int(row["n_records"]))
            memberships += len(ids)
        metrics = {"raw_ree_data_records": len(raw[leg, "ree"]), "raw_ph_data_records": len(raw[leg, "ph"]),
                   "raw_ree_unique_sample_IDs": len(ri), "raw_ph_unique_sample_IDs": len(pi),
                   "raw_sample_ID_union": len(universe), "raw_shared_sample_IDs": len(shared),
                   "raw_ree_only_sample_IDs": len(set(ri)-set(pi)), "raw_ph_only_sample_IDs": len(set(pi)-set(ri)),
                   **{s: status_counts[s] for s in STATUSES},
                   "eligible_depth_union_unique_bottles": len(accepted[leg]),
                   "primary_unique_bottles": len(primary_ids), "primary_layer_memberships": memberships}
        id_metrics = {"raw_ree_data_records": set(ri), "raw_ph_data_records": set(pi),
                      "raw_ree_unique_sample_IDs": set(ri), "raw_ph_unique_sample_IDs": set(pi),
                      "raw_sample_ID_union": universe, "raw_shared_sample_IDs": shared,
                      "raw_ree_only_sample_IDs": set(ri)-set(pi), "raw_ph_only_sample_IDs": set(pi)-set(ri),
                      **{s: {sid for l, sid in computed if l == leg and computed[l, sid] == s} for s in STATUSES},
                      "eligible_depth_union_unique_bottles": accepted[leg], "primary_unique_bottles": primary_ids}
        remaining = len(universe)
        for s in STATUSES:
            row = flow[leg, s]
            compare("ordered_entering", f"{leg}/{s}", remaining, int(row["entering_ordered_stage"]))
            remaining -= status_counts[s]
            compare("ordered_unassigned", f"{leg}/{s}", remaining, int(row["unassigned_after_stage"]))
        for key, value in metrics.items():
            compare("flow_count", f"{leg}/{key}", value, int(flow[leg, key]["count"]))
            if key in id_metrics:
                compare("flow_ID_set", f"{leg}/{key}", id_metrics[key], set(json.loads(flow[leg, key]["sample_ids"])))
        statistics[leg] = metrics

    contrasts = keyed(read(OUT / "layer_squared_error_difference.csv"), lambda row: (row["leg"], int(row["bin_index"])))
    signs, exact_d = {leg: [] for leg in LEGS}, []
    for key, row in primary.items():
        m, b = Fraction(row["prediction_error"]), Fraction(row["baseline_error"])
        d = m*m-b*b
        sign = "positive" if d > 0 else "negative" if d < 0 else "zero"
        exp = contrasts[key]
        for field, expected in (("model_signed_error_source_token", row["prediction_error"]),
                                ("reference_signed_error_source_token", row["baseline_error"]), ("sign", sign)):
            compare("contrast_source_and_sign", f"{key}/{field}", expected, exp[field])
        for field, expected in (("model_squared_exact_decimal", m*m), ("reference_squared_exact_decimal", b*b),
                                ("difference_exact_decimal", d)):
            compare("contrast_exact_decimal", f"{key}/{field}", expected, Fraction(exp[field]))
        compare("contrast_exact_fraction", str(key), d, Fraction(int(exp["difference_numerator"]), int(exp["difference_denominator"])))
        compare("contrast_source_record", str(key), row["_record"], int(exp["source_csv_record"]))
        compare("contrast_membership", str(key), set(json.loads(row["sample_ids"])), set(json.loads(exp["sample_ids"])))
        signs[key[0]].append(sign)
        exact_d.append({"leg": key[0], "bin_index": key[1], "difference_exact": str(d), "sign": sign})

    annotations = read(inputs["domain_rows"])
    metadata = json.loads(inputs["domain_metadata"].read_text(), parse_float=Decimal)
    notebook = TASK / "ree/round8/raw/Lin2024-notebook-published-output.txt"
    saved = notebook.read_text()
    saved_a = list(map(Decimal, re.findall(r"average REE_slope of\s+\d+\s*-\s*\d+\s*m:\s*([\deE.+-]+)", saved)))
    saved_y = list(map(Decimal, re.findall(r"Average pH of depth \d+-\d+ m:\s*([\deE.+-]+)", saved)))
    aa, yy = metadata["training_alpha_six_layer_means"], metadata["training_ph_six_layer_means"]
    compare("domain_training_source", "alpha six means", saved_a, aa)
    compare("domain_training_source", "pH six means", saved_y, yy)
    compare("domain_training_source", "saved training source SHA", metadata["training_source_sha256"], sha(notebook))
    compare("domain_source_coverage", "48 CSV rows", 48, len(annotations))
    compare("domain_source_coverage", "48 metadata rows", 48, metadata["n_annotations"])
    compare("domain_source_coverage", "48 metadata annotation objects", 48, len(metadata["annotations"]))
    compare("domain_training_source", "source layer SHA", metadata["layer_result_sha256"], sha(inputs["layers"]))
    selected = [row for row in annotations if row["variant"] == "primary"]
    compare("domain_source_coverage", "12 primary", 12, len(selected))
    labels = keyed(read(OUT / "joint_domain_labels.csv"), lambda row: (row["leg"], int(row["bin_index"])))
    cells, primary_domains = Counter(), []
    def direction(value, reference):
        return "below" if value < min(reference) else "above" if value > max(reference) else "within"
    for row in selected:
        key = row["leg"], int(row["bin_index"])
        alpha = Decimal(row["alpha_from_fixed_prediction"])
        observed = Decimal(row["observed_ph"])
        ad, yd = direction(alpha, aa), direction(observed, yy)
        aw, yw = ad == "within", yd == "within"
        exp = labels[key]
        for field, expected in (("original_alpha_directional_label", ad), ("original_observed_ph_directional_label", yd),
                                ("alpha_within_original_range", str(aw)), ("observed_ph_within_original_range", str(yw)),
                                ("alpha_source_token", row["alpha_from_fixed_prediction"]),
                                ("observed_ph_source_token", row["observed_ph"]),
                                ("two_axis_label", "alpha_"+("within" if aw else "outside")+"__observed_ph_"+("within" if yw else "outside"))):
            compare("domain_output_label_or_token", f"{key}/{field}", expected, exp[field])
        compare("domain_original_label", f"{key}/alpha", ad, row["alpha_relative_to_training_layer_means"])
        compare("domain_original_label", f"{key}/observed", yd, row["observed_ph_relative_to_training_layer_means"])
        for field in ("observed_ph", "predicted_ph", "prediction_error", "baseline_error", "n_records", "n_stations"):
            compare("domain_original_primary_fields", f"{key}/{field}", primary[key][field], row[field])
        compare("domain_primary_membership", str(key), set(json.loads(primary[key]["sample_ids"])), set(json.loads(exp["sample_ids"])))
        for field, expected in (("annotation_csv_record", row["_record"]),
                                ("annotation_physical_line", row["_line"]),
                                ("primary_layer_csv_record", primary[key]["_record"])):
            compare("domain_source_record", f"{key}/{field}", expected, int(exp[field]))
        cells[row["leg"], aw, yw] += 1
        cells["all_primary_rows", aw, yw] += 1
        primary_domains.append({"leg": key[0], "bin_index": key[1], "alpha_label": ad, "observed_ph_label": yd})
    cell_rows = read(OUT / "joint_domain_counts.csv")
    compare("domain_output_coverage", "12 label rows", 12, len(labels))
    compare("domain_output_coverage", "12 count cells", 12, len(cell_rows))
    compare("domain_output_coverage", "count cell identities",
            {(leg, a, y) for leg in (*LEGS, "all_primary_rows") for a in (False, True) for y in (False, True)},
            {(row["leg"], row["alpha_within_original_range"] == "True", row["observed_ph_within_original_range"] == "True") for row in cell_rows})
    for row in cell_rows:
        key = row["leg"], row["alpha_within_original_range"] == "True", row["observed_ph_within_original_range"] == "True"
        compare("domain_count_cell", str(key), cells[key], int(row["n_layer_means"]))

    source_files = [plan_path, ROOT / "analysis/export_selection_weight_domain_v1.py", *inputs.values(), notebook,
                    TASK / "ree/round8/analyze_ree_ph_transfer.py"]
    file_hashes = [{"path": str(path.relative_to(TASK)), "sha256": sha(path)} for path in source_files]
    output_hashes = [{"path": str(path.relative_to(TASK)), "sha256": sha(path)} for path in sorted(OUT.iterdir()) if path.is_file()]
    report = {"schema": "selection-weight-domain-independent-review/v1", "status": "PASS" if not differences else "DIFFERENCES_FOUND",
              "scope": "Independent QC of C-authored selection/weight/domain displays only; all targets already exposed; not a new review cycle, new validation or manuscript approval.",
              "checker_path": str(Path(__file__).relative_to(TASK)), "checker_sha256": sha(Path(__file__)),
              "runtime": sys.version, "source_and_plan_hashes": file_hashes, "target_output_hashes": output_hashes,
              "comparison_counts": dict(checked), "n_differences": len(differences), "differences": differences,
              "independently_reconstructed_flow": statistics, "n_terminal_statuses_reconstructed": len(computed),
              "exact_layer_error_differences": exact_d, "signs_by_leg": signs,
              "depth_weight_argument": "For each leg, fixed six-layer errors imply MSE_model(w)-MSE_reference(w)=sum_k w_k*d_k. If every d_k>0 and w_k>=0,sum(w)=1, the difference is at least min(d)>0; monotonic square root gives the same strict RMSE ordering. Leg2 has six positive d. Leg1 has two positive/four negative; admissible one-hot weights can attain either sign, so no invariant ordering. No new weights or weighted score is selected/calculated. This concerns stored primary point errors only, not RSD scenarios, intra-layer weighting or geographical representativeness.",
              "primary_domain_labels": primary_domains,
              "domain_count_cells": [{"leg": leg, "alpha_within": a, "observed_ph_within": y, "n": cells[leg, a, y]}
                                     for leg in (*LEGS, "all_primary_rows") for a in (True, False) for y in (True, False)],
              "domain_reference": {"n_alpha_means": len(aa), "n_observed_ph_means": len(yy),
                                   "alpha_min": str(min(aa)), "alpha_max": str(max(aa)), "observed_ph_min": str(min(yy)), "observed_ph_max": str(max(yy)),
                                   "interpretation": "Extrema of six global calibration-layer means, endpoints included; not the full range of1755 source-compilation rows. Observed target pH, not predicted pH, defines axis2. No joint validity or advance suitability criterion/subset score follows."},
              "actual_work": ["Read original prepare_pairs lines155-212 and C plan/output definitions; did not consult, import or copy C exporter implementation (its file bytes were hashed for identity only).", "Independently parsed four raw tables with Decimal finite-value handling and rebuilt first-failure terminal classes, including all zero categories, primary IDs and inclusive memberships.", "Compared original audit statuses/raw lines and every C provenance row, all flow counts/ID sets/stage counts, twelve exact Fraction contrasts, twelve primary two-axis labels and twelve 2x2 count cells.", "Checked training metadata arrays against preserved six saved notebook means. Read all three Markdown explanations for scientific interpretation.", "No bootstrap, model fitting, new weighted score, continuous-box calculation or manuscript/state modification."],
              "independence_boundary": "B authored continuous_box_v1.py. This check is independent of C selection/weight/domain exporter only; it is NOT independent validation of B's own continuous-box code or results.",
              "new_material_scientific_findings": [] if not differences else ["Resolve listed differences before adoption."]}
    target = ROOT / "reviews/selection-weight-domain-independent-B"
    target.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    md = ["# Independent check of selection, depth-weight and domain displays — B", "",
          "Result: **"+report["status"]+"**. All inputs are already exposed. This check is not a new review cycle or new validation.", "",
          "I rebuilt the classification from four original CSVs without importing or copying C's exporter. The original if/elif gives one first-matching terminal category per union ID; the underlying causes need not be mutually exclusive. Zero terminal counts do not mean that later untested causes were absent.", "",
          "|Leg|REE rows|pH rows|ID union|Shared IDs|Accepted union|Primary bottles|Memberships|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        s = statistics[leg]
        md.append("|"+"|".join(map(str,[leg,s["raw_ree_data_records"],s["raw_ph_data_records"],s["raw_sample_ID_union"],s["raw_shared_sample_IDs"],s["eligible_depth_union_unique_bottles"],s["primary_unique_bottles"],s["primary_layer_memberships"]]))+"|")
    md += ["", "All "+str(len(computed))+" original terminal classifications and C provenance rows were checked; all38 flow counts,36 flow ID sets, ordered entering/remaining counts and12 primary memberships were independently compared. Differences: **"+str(len(differences))+"**. Detailed per-field comparison counts and exact input/output SHA identities are in the JSON.", "",
           "The12 squared-error differences were recalculated exactly from stored decimal error tokens with Fraction. Leg1 has two positive and four negative differences; Leg2 has six strictly positive differences. For nonnegative normalized depth weights on these unchanged six means, the weighted MSE difference is their weighted average. Hence Leg2's ordering holds for every such vector; Leg1 permits either sign. No weight vector or new weighted score was chosen.", "",
           "The48-row domain source was restricted to its12 original primary rows. Both directional labels and within/outside booleans were rederived from the extrema of the six saved global training alpha/pH means. Leg1 cells (within/within,within/outside,outside/within,outside/outside) are1/0/0/5; Leg2 cells are0/3/2/1, total1/3/2/6. These ranges are not the full range of1755 source records and do not define a calibrated joint validity region. Observed target pH is required; there is no prospective suitability rule or new subset score.", "",
           "I read the three Markdown explanations; their limits match these calculations. No bootstrap or continuous-box calculation was repeated. Root may use this evidence when drafting a later manuscript, which still needs its own complete-version review.", "",
           "**Independence boundary:** I authored the continuous-box program. This independent check addresses C's selection/weight/domain display only and provides no independent validation of my continuous-box code/results."]
    if differences:
        md += ["", "Differences must be resolved before adoption; see JSON."]
    target.with_suffix(".md").write_text("\n".join(md)+"\n")
    print(json.dumps({"status": report["status"], "comparison_counts": dict(checked), "n_differences": len(differences),
                      "flow": statistics, "reports": [str(target.with_suffix(".json")), str(target.with_suffix(".md"))]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
