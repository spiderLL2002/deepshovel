#!/usr/bin/env python3
"""Display existing GP15 selection, fixed layer errors and domain labels.

Python 3.12 standard library only. Refuses unfrozen plans or existing outputs.
No fitting, new sample selection, response evaluation, or original-file writes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import sys

SCRIPT = "iteration-2026-10-27/analysis/export_selection_weight_domain_v1.py"
OUTPUT = "iteration-2026-10-27/analysis/selection-weight-domain-v1"
INPUTS = {
    "pairing_audit": "results/ree_ph_fixed_transfer_v1/pairing_audit.csv",
    "leg1_ree": "ree/round8/raw/GP15-leg1-REE.csv",
    "leg1_ph": "ree/round8/raw/GP15-leg1-pH.csv",
    "leg2_ree": "ree/round8/raw/GP15-leg2-REE.csv",
    "leg2_ph": "ree/round8/raw/GP15-leg2-pH.csv",
    "layers": "results/ree_ph_fixed_transfer_v1/layer_summary.csv",
    "domain_rows": "ree/round8/training-domain-annotation.csv",
    "domain_metadata": "ree/round8/training-domain-annotation.json",
}
FILES = [
    "eligibility_flow.csv", "eligibility_flow.md",
    "audit_status_provenance.csv",
    "layer_squared_error_difference.csv", "layer_squared_error_difference.md",
    "joint_domain_labels.csv", "joint_domain_counts.csv", "joint_domain.md",
    "input_manifest.json", "output_manifest.json",
]
LEGS = ("leg1", "leg2")
STATUSES = (
    "not_shared_sample_id",
    "station_or_event_mismatch",
    "leg2_station_or_sample_seen_in_leg1",
    "missing_finite_depth",
    "outside_predeclared_depth_union",
    "bottle_flag_not_allowed",
    "missing_or_invalid_response",
    "eligible_in_at_least_one_depth_definition",
)
SCOPE = ("Already-exposed original inputs: deterministic SI display and reviewer QC. "
         "No new validation, fitting, sample exclusion, score replacement, "
         "measurement uncertainty model, geographical weighting or causal inference.")
FLOW_NOTE = ("One terminal status is assigned to each sample-ID union record by the "
             "original if/elif order. Terminal counts partition records, but possible "
             "failure causes are not mutually exclusive. Later causes were not evaluated "
             "after an earlier assignment. This exporter copies statuses, not their causes.")
WEIGHT_NOTE = ("For the six fixed primary layer means only, let d_k=e_model,k^2-e_reference,k^2. "
               "For arbitrary w_k>=0 with sum(w_k)=1, the weighted MSE difference is sum(w_k*d_k); "
               "weighted RMSE has the same ordering because square root is increasing. "
               "The exporter does not choose or optimize weights or generate a new weighted score. "
               "This is about stored point errors, not the RSD scenarios, an uncertainty box, "
               "area-weighted geography, new intra-layer weights or bottle-level errors.")
DOMAIN_NOTE = ("Labels describe the rectangle formed by extrema of the original six training "
               "alpha means and original six observed-pH means, with endpoints included. "
               "These are six global calibration-layer means, not the full feature range "
               "of the 1,755 source-compilation records or of all source bottles. "
               "They do not estimate a joint calibrated validity region, create new subset "
               "scores or establish prospective suitability; observed target pH is required "
               "to assign the second axis. Rows are layer means, not independent bottles.")

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def identity(value):
    value = str(value).strip()
    require(value.lower() not in ("", "nd", "na", "nan"), "Missing sample identity")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return value
    require(parsed.is_finite(), "Nonfinite identity")
    return format(parsed.normalize(), "f")

def number(token):
    value = Decimal(token)
    require(value.is_finite(), "Nonfinite numeric source token")
    return value

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        require(header is not None and len(header) == len(set(header)), "Bad CSV header")
        previous = reader.line_num
        rows = []
        for ordinal, row in enumerate(reader, 1):
            require(None not in row and all(v is not None for v in row.values()), "Ragged CSV")
            rows.append(dict(row=row, record=ordinal,
                             physical_start=previous + 1, physical_end=reader.line_num))
            previous = reader.line_num
    return header, rows

def ids_from_layers(rows):
    return set(sid for item in rows for sid in json.loads(item["row"]["sample_ids"]))

def fraction_decimal(value):
    """Exact terminating decimal for rationals derived from finite decimal tokens."""
    n, d = value.numerator, value.denominator
    twos = fives = 0
    while d % 2 == 0:
        twos += 1
        d //= 2
    while d % 5 == 0:
        fives += 1
        d //= 5
    require(d == 1, "Nonterminating decimal unexpectedly derived")
    places = max(twos, fives)
    scaled = abs(n) * (10 ** places // value.denominator)
    digits = str(scaled).zfill(places + 1)
    text = digits if places == 0 else digits[:-places] + "." + digits[-places:]
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return ("-" if n < 0 else "") + text

def csv_text(rows):
    require(bool(rows), "Empty export table")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()

def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"

def table(headers, rows):
    return "\n".join(["|" + "|".join(headers) + "|",
                      "|" + "|".join("---" for _ in headers) + "|"] +
                     ["|" + "|".join(str(v) for v in row) + "|" for row in rows])

def build(root, paths):
    source = {role: read_csv(path) for role, path in paths.items()
              if role != "domain_metadata"}
    metadata = json.loads(paths["domain_metadata"].read_text(),
                          parse_float=Decimal, parse_int=Decimal)
    original_alpha = metadata["training_alpha_six_layer_means"]
    original_ph = metadata["training_ph_six_layer_means"]
    require(len(original_alpha) == len(original_ph) == 6, "Original ranges require six means")
    alpha_bounds = (min(original_alpha), max(original_alpha))
    ph_bounds = (min(original_ph), max(original_ph))
    require(metadata["layer_result_sha256"] == sha(paths["layers"]),
            "Domain metadata is bound to a different layer output")
    all_layers = source["layers"][1]
    layers = [x for x in all_layers if x["row"]["variant"] == "primary"]
    require(len(layers) == 12, "Exactly twelve original primary rows required")
    layer_index = {}
    for item in layers:
        r = item["row"]
        key = (r["leg"], int(r["bin_index"]))
        require(key[0] in LEGS and key[1] in range(6) and key not in layer_index,
                "Unexpected/repeated primary layer key")
        require(int(r["low_m"]) == 200 + 100 * key[1] and
                int(r["high_m"]) == 300 + 100 * key[1], "Unexpected original layer limits")
        ids = json.loads(r["sample_ids"])
        require(len(ids) == len(set(ids)) == int(r["n_records"]), "Layer ID/count mismatch")
        layer_index[key] = item
    require(set(layer_index) == {(leg, k) for leg in LEGS for k in range(6)},
            "Incomplete original primary layers")

    raw = {}
    for leg in LEGS:
        for kind in ("ree", "ph"):
            role = leg + "_" + kind
            header, items = source[role]
            require(set(("Sample_ID", "Station_ID", "Event_ID", "Sample_Depth")) <= set(header),
                    "Required raw identity fields absent")
            raw[role] = {}
            for item in items:
                sid = identity(item["row"]["Sample_ID"])
                require(sid not in raw[role], "Duplicate raw source sample ID")
                raw[role][sid] = item
    audit_by_leg = {leg: {} for leg in LEGS}
    for item in source["pairing_audit"][1]:
        r = item["row"]
        leg, sid = r["leg"], identity(r["sample_id"])
        require(leg in LEGS and r["status"] in STATUSES, "Unknown original audit value")
        require(sid not in audit_by_leg[leg], "Duplicate original audit identity")
        audit_by_leg[leg][sid] = item

    flow, provenance = [], []
    flow_md = ["# Original ordered selection flow", "", SCOPE, "", FLOW_NOTE, "",
               "Raw record and sample-ID counts describe these files; file count is not "
               "independent-study count. GeoFish rows can be present before eligibility. "
               "A layer membership can repeat the same boundary bottle.", ""]
    for leg in LEGS:
        ri, pi, ai = raw[leg + "_ree"], raw[leg + "_ph"], audit_by_leg[leg]
        rids, pids = set(ri), set(pi)
        union, shared = rids | pids, rids & pids
        require(set(ai) == union, "Audit sample-ID universe differs from original raw files")
        accepted = {sid for sid, item in ai.items() if item["row"]["status"] == STATUSES[-1]}
        primary = [layer_index[(leg, k)] for k in range(6)]
        primary_ids = ids_from_layers(primary)
        require(primary_ids <= accepted, "Primary samples not in original accepted union")
        require(len(primary_ids) == (24 if leg == "leg1" else 55), "Original primary n changed")
        memberships = sum(int(x["row"]["n_records"]) for x in primary)
        metrics = [
            ("raw_ree_data_records", "CSV data record", len(ri), rids),
            ("raw_ph_data_records", "CSV data record", len(pi), pids),
            ("raw_ree_unique_sample_IDs", "unique source sample ID", len(rids), rids),
            ("raw_ph_unique_sample_IDs", "unique source sample ID", len(pids), pids),
            ("raw_sample_ID_union", "unique sample ID", len(union), union),
            ("raw_shared_sample_IDs", "unique sample ID", len(shared), shared),
            ("raw_ree_only_sample_IDs", "unique sample ID", len(rids - pids), rids - pids),
            ("raw_ph_only_sample_IDs", "unique sample ID", len(pids - rids), pids - rids),
        ]
        for metric, unit, count, ids in metrics:
            flow.append(dict(leg=leg,kind="raw_identity",order="",metric=metric,
                             unit=unit,count=count,entering_ordered_stage="",
                             unassigned_after_stage="",sample_ids=json.dumps(sorted(ids)),
                             source="original raw CSV identity columns"))
        remaining = len(union)
        for order, status in enumerate(STATUSES, 1):
            ids = {sid for sid, item in ai.items() if item["row"]["status"] == status}
            require((ids == union - shared) if status == STATUSES[0] else True,
                    "Original not-shared assignment disagrees with raw identity")
            entering = remaining
            remaining -= len(ids)
            flow.append(dict(leg=leg,kind="ordered_terminal_status",order=order,
                             metric=status,unit="unique audit sample ID",count=len(ids),
                             entering_ordered_stage=entering,
                             unassigned_after_stage=remaining,sample_ids=json.dumps(sorted(ids)),
                             source="pairing_audit.status copied; zero categories retained"))
        require(remaining == 0, "Original terminal statuses do not partition union")
        for metric, unit, count, ids in [
            ("eligible_depth_union_unique_bottles", "unique paired parent-bottle ID", len(accepted), accepted),
            ("primary_unique_bottles", "unique paired parent-bottle ID", len(primary_ids), primary_ids),
            ("primary_layer_memberships", "sample-layer membership", memberships, primary_ids),
        ]:
            flow.append(dict(leg=leg,kind="reported_support",order="",metric=metric,
                             unit=unit,count=count,entering_ordered_stage="",
                             unassigned_after_stage="",sample_ids=json.dumps(sorted(ids)),
                             source="accepted original status / original primary layer IDs"))
        for sid, item in ai.items():
            r = item["row"]
            for role, index, field in [(leg + "_ree", ri, "ree_line"),
                                       (leg + "_ph", pi, "ph_line")]:
                if sid in shared:
                    require(int(r[field]) == index[sid]["physical_start"] ==
                            index[sid]["physical_end"], "Original audit raw line mismatch")
            provenance.append(dict(
                leg=leg,sample_id=sid,original_status=r["status"],
                audit_csv_record=item["record"],audit_physical_line=item["physical_start"],
                present_ree=sid in ri,present_ph=sid in pi,
                raw_ree_physical_line=ri[sid]["physical_start"] if sid in ri else "",
                raw_ph_physical_line=pi[sid]["physical_start"] if sid in pi else "",
                original_station_token=r["station"],original_event_token=r["event"],
                original_ree_depth_token=r["ree_reported_depth"],
                original_ph_depth_token=r["ph_reported_depth"]))
        flow_md += ["## " + leg, "",
                    table(["Metric / original status", "Unit", "Count"],
                          [(x["metric"], x["unit"], x["count"]) for x in flow if x["leg"] == leg]), ""]
    flow_md += ["The entering/unassigned columns in CSV describe the ordered partition "
                "of original terminal assignments. They are not new independent "
                "cause-specific failure rates.", ""]

    squared = []
    weight_md = ["# Fixed primary layer squared-error differences", "", SCOPE, "",
                 WEIGHT_NOTE, "",
                 "Exact arithmetic below is on the serialized decimal signed-error tokens. "
                 "It does not increase measurement precision or claim exact physical error.", ""]
    for leg in LEGS:
        signs = []
        mdrows = []
        for k in range(6):
            item = layer_index[(leg, k)]
            r = item["row"]
            em = Fraction(r["prediction_error"])
            er = Fraction(r["baseline_error"])
            d = em * em - er * er
            sign = "positive" if d > 0 else "negative" if d < 0 else "zero"
            signs.append(sign)
            squared.append(dict(
                leg=leg,bin_index=k,low_m=r["low_m"],high_m=r["high_m"],
                model_signed_error_source_token=r["prediction_error"],
                reference_signed_error_source_token=r["baseline_error"],
                model_squared_exact_decimal=fraction_decimal(em * em),
                reference_squared_exact_decimal=fraction_decimal(er * er),
                difference_exact_decimal=fraction_decimal(d),
                difference_numerator=d.numerator,difference_denominator=d.denominator,
                sign=sign,source_csv_record=item["record"],
                source_physical_line=item["physical_start"],
                sample_ids=r["sample_ids"]))
            mdrows.append((r["low_m"] + "-" + r["high_m"], sign,
                           format(Decimal(fraction_decimal(d)), ".6E")))
        if all(s == "positive" for s in signs):
            conclusion = ("Every d_k is strictly positive. For every nonnegative normalized "
                          "weight vector on these six unchanged layer means, model weighted "
                          "MSE and weighted RMSE are strictly greater than the reference.")
        elif all(s == "negative" for s in signs):
            conclusion = ("Every d_k is strictly negative. Every such weight vector strictly "
                          "favors the model at these unchanged point layer means.")
        elif "positive" in signs and "negative" in signs:
            conclusion = ("Signs are mixed. Weight concentrated on a positive layer favors "
                          "the reference, while weight concentrated on a negative layer "
                          "favors the model. No all-depth-weight ordering follows.")
        else:
            conclusion = ("Zeros or one-sided signs require distinguishing weak ordering "
                          "from strict ordering; weights supported only on zero d_k can tie.")
        weight_md += ["## " + leg, "", conclusion, "",
                      table(["Layer m", "Sign of d", "d in pH-units squared (display)"], mdrows), ""]
    weight_md += ["CSV retains exact terminating decimals and numerator/denominator, along "
                  "with original signed-error tokens. No scenario or bottle errors are rescored.", ""]

    annotations = [x for x in source["domain_rows"][1] if x["row"]["variant"] == "primary"]
    require(len(annotations) == 12, "Exactly twelve primary annotations required")
    labels, counts = [], Counter()
    seen = set()
    for item in annotations:
        r = item["row"]
        key = (r["leg"], int(r["bin_index"]))
        require(key in layer_index and key not in seen, "Bad primary domain key")
        seen.add(key)
        original = layer_index[key]["row"]
        for field in ("sample_ids","n_records","n_stations","observed_ph","predicted_ph",
                      "baseline_ph","prediction_error","baseline_error"):
            require(r[field] == original[field], "Domain annotation source tokens changed")
        a = number(r["alpha_from_fixed_prediction"])
        ph = number(r["observed_ph"])
        al = r["alpha_relative_to_training_layer_means"]
        pl = r["observed_ph_relative_to_training_layer_means"]
        expected_a = "below" if a < alpha_bounds[0] else "above" if a > alpha_bounds[1] else "within"
        expected_p = "below" if ph < ph_bounds[0] else "above" if ph > ph_bounds[1] else "within"
        require(al == expected_a and pl == expected_p, "Original domain labels disagree with original ranges")
        ain, pin = al == "within", pl == "within"
        counts[(r["leg"],ain,pin)] += 1
        counts[("all_primary_rows",ain,pin)] += 1
        labels.append(dict(
            leg=r["leg"],bin_index=r["bin_index"],low_m=r["low_m"],high_m=r["high_m"],
            alpha_source_token=r["alpha_from_fixed_prediction"],observed_ph_source_token=r["observed_ph"],
            original_alpha_directional_label=al,original_observed_ph_directional_label=pl,
            alpha_within_original_range=ain,observed_ph_within_original_range=pin,
            two_axis_label=("alpha_within" if ain else "alpha_outside") +
                           "__" + ("observed_ph_within" if pin else "observed_ph_outside"),
            sample_ids=r["sample_ids"],annotation_csv_record=item["record"],
            annotation_physical_line=item["physical_start"],
            primary_layer_csv_record=layer_index[key]["record"]))
    count_rows = []
    for leg in (*LEGS, "all_primary_rows"):
        for ain in (True,False):
            for pin in (True,False):
                count_rows.append(dict(leg=leg,alpha_within_original_range=ain,
                                       observed_ph_within_original_range=pin,
                                       n_layer_means=counts[(leg,ain,pin)]))
        require(sum(counts[(leg,a,p)] for a in (True,False) for p in (True,False)) ==
                (12 if leg == "all_primary_rows" else 6), "Domain count denominator changed")
    domain_md = ["# Existing primary two-axis range labels", "", SCOPE, "", DOMAIN_NOTE, "",
                 "Original alpha range: " + str(alpha_bounds[0]) + " to " + str(alpha_bounds[1]) + ".",
                 "Original observed-pH range: " + str(ph_bounds[0]) + " to " + str(ph_bounds[1]) + ".", "",
                 table(["Leg","Layer m","Original alpha label","Original observed-pH label"],
                       [(x["leg"],x["low_m"]+"-"+x["high_m"],
                         x["original_alpha_directional_label"],
                         x["original_observed_ph_directional_label"]) for x in labels]), ""]
    for leg in (*LEGS, "all_primary_rows"):
        domain_md += ["## " + leg, "",
                      table(["Alpha range","Observed pH within","Observed pH outside"],
                            [("within",counts[(leg,True,True)],counts[(leg,True,False)]),
                             ("outside",counts[(leg,False,True)],counts[(leg,False,False)])]), ""]
    payload = {
        "eligibility_flow.csv": csv_text(flow),
        "eligibility_flow.md": "\n".join(flow_md),
        "audit_status_provenance.csv": csv_text(provenance),
        "layer_squared_error_difference.csv": csv_text(squared),
        "layer_squared_error_difference.md": "\n".join(weight_md),
        "joint_domain_labels.csv": csv_text(labels),
        "joint_domain_counts.csv": csv_text(count_rows),
        "joint_domain.md": "\n".join(domain_md),
    }
    definitions = dict(scope=SCOPE,flow_status_order=list(STATUSES),
                       flow_semantics=FLOW_NOTE,weight_semantics=WEIGHT_NOTE,
                       domain_semantics=DOMAIN_NOTE,
                       original_training_alpha_values=[str(v) for v in original_alpha],
                       original_training_ph_values=[str(v) for v in original_ph],
                       source_rows_total_domain=len(source["domain_rows"][1]),
                       exported_domain_selection="Exactly variant=primary, six rows per leg",
                       no_fitting=True,no_new_validation=True,no_new_weighted_score=True)
    return payload, definitions

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    require(sys.version_info[:2] == (3,12), "Python 3.12 is required")
    script_path = Path(__file__).resolve()
    root = script_path.parents[2]
    require(script_path == (root / SCRIPT).resolve(), "Unexpected script location")
    plan_path = Path(args.plan).resolve()
    require(plan_path.is_relative_to(root / "iteration-2026-10-27/analysis"), "Plan outside analysis")
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    require(plan.get("status") == "ROOT_FROZEN" and plan.get("frozen") is True and
            plan.get("frozen_by") == "root" and bool(plan.get("frozen_at_utc")),
            "Root must inspect and freeze this plan before execution")
    require(plan["analysis_script"] == SCRIPT and
            plan["analysis_script_sha256"] == sha(script_path), "Script differs from frozen plan")
    require(plan.get("required_python_minor") == "3.12", "Wrong planned runtime")
    entries = plan["inputs"]
    require(len(entries) == len(INPUTS) and
            {x["role"] for x in entries} == set(INPUTS), "Input roles differ from whitelist")
    paths = {}
    for entry in entries:
        role = entry["role"]
        require(entry["path"] == INPUTS[role], "Input path differs from whitelist")
        path = (root / entry["path"]).resolve()
        require(path.is_relative_to(root) and sha(path) == entry["sha256"], "Input SHA/path mismatch")
        paths[role] = path
    out = Path(args.out)
    out = (root / out).resolve() if not out.is_absolute() else out.resolve()
    require(plan["output_relative"] == OUTPUT and out == (root / OUTPUT).resolve(),
            "Output differs from exact approved new directory")
    require(not out.exists(), "Existing output directory refused")
    require(len(plan["outputs"]) == len(FILES) and set(plan["outputs"]) == set(FILES),
            "Output contract differs from this exporter")
    payload, definitions = build(root, paths)
    input_manifest = dict(
        plan_path=str(plan_path.relative_to(root)),
        plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
        script_path=SCRIPT,script_sha256=sha(script_path),
        inputs=entries,definitions=definitions,
        interpreter=sys.version,post_exposure=True,cycle_count_delta=0)
    payload["input_manifest.json"] = json_text(input_manifest)
    require(set(payload) == set(FILES) - {"output_manifest.json"}, "Unexpected artifact set")
    require(plan_path.read_bytes() == plan_bytes and sha(script_path) ==
            plan["analysis_script_sha256"], "Plan/script changed before output write")
    require(all(sha(paths[e["role"]]) == e["sha256"] for e in entries),
            "Source changed before output write")
    out.mkdir(exist_ok=False)
    for name, text in payload.items():
        with (out / name).open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    manifest = dict(scope=SCOPE,outputs=[
        dict(path=name,sha256=sha(out/name),bytes=(out/name).stat().st_size)
        for name in sorted(payload)],
        self_hash_omitted="output_manifest.json cannot contain its own SHA",
        original_inputs_mutated=False,cycle_count_delta=0)
    with (out / "output_manifest.json").open("x", encoding="utf-8") as handle:
        handle.write(json_text(manifest))
    print(json.dumps(dict(output=str(out),files=len(FILES),scope=SCOPE),indent=2))

if __name__ == "__main__":
    main()
