#!/usr/bin/env python3
"""Exact-rational envelope of the existing hypothetical shared REE box.

Root-freeze gated; standard library only. All GP15 responses are already exposed.
This is a mathematical calculation for declared decimal constants, not new
validation, a real measurement-error bound, or formal verification of IEEE code.
The author of this program cannot independently validate its numerical outputs.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import io
import itertools
import json
from math import isqrt
from pathlib import Path
import sys

SCRIPT = "iteration-2026-10-27/analysis/continuous_box_v1.py"
WHITELIST = {
    "protocol": "ree/round8/protocol-frozen-v1.json",
    "samples": "results/ree_ph_fixed_transfer_v1/sample_predictions.csv",
    "layers": "results/ree_ph_fixed_transfer_v1/layer_summary.csv",
    "stress": "results/ree_ph_fixed_transfer_v1/ree_repeatability_stress.json",
    "paas": "ree/round8/raw/Lin2024-paas.csv",
    "mass": "ree/round8/raw/Lin2024-molar_mass_mgmol.csv",
}
LEGS = ("leg1", "leg2")
ELEMENTS = ("Sm", "Gd", "Dy", "Er")
TOL = Fraction("1e-10")
DISPLAY_PRECISION = 50
SQRT_ENCLOSURE_PLACES = 60
SCOPE = ("Post-exposure mathematical envelope for the declared exact-decimal "
         "fixed model, original shared four-element REE box and nine discrete "
         "pH-offset pairs only. No fitting, new independent validation, "
         "real measurement-error hard bound or joint-covariance propagation.")
SUCCESS = ("layer_coefficients.csv", "pair_residual_lower_bounds.csv",
           "pair_envelope.csv", "ranking_certificates.json",
           "vertex_matching.csv", "continuous_box_supplement.md")
FAILURE = ("vertex_matching.csv", "failure.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rat(value):
    if isinstance(value, Fraction):
        return value
    require(not isinstance(value, float), "Binary float is not an arithmetic input")
    token = str(value).strip()
    try:
        parsed = Decimal(token)
    except InvalidOperation as error:
        raise ValueError("Invalid finite decimal token: " + token) from error
    require(parsed.is_finite(), "Nonfinite decimal token")
    return Fraction(parsed)


def ident(value):
    token = str(value).strip()
    parsed = Decimal(token)
    require(parsed.is_finite(), "Nonfinite identifier")
    return format(parsed.normalize(), "f")


def decimal50(value):
    with localcontext() as context:
        context.prec = DISPLAY_PRECISION
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def sqrt_display(value):
    require(value >= 0, "Negative MSE")
    with localcontext() as context:
        context.prec = DISPLAY_PRECISION
        return str((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())


def delta_rmse_display(model_mse, reference_mse):
    with localcontext() as context:
        context.prec = DISPLAY_PRECISION
        model = (Decimal(model_mse.numerator) / Decimal(model_mse.denominator)).sqrt()
        reference = (Decimal(reference_mse.numerator) / Decimal(reference_mse.denominator)).sqrt()
        return str(model - reference)


def sqrt_interval(value):
    """Exact rational enclosure with width <= 10^-60; no Decimal decisions."""
    require(value >= 0, "Negative squared norm")
    scale = 10 ** SQRT_ENCLOSURE_PLACES
    floor_root = isqrt((value.numerator * scale * scale) // value.denominator)
    low = Fraction(floor_root, scale)
    high = low if low * low == value else Fraction(floor_root + 1, scale)
    require(low * low <= value <= high * high, "Square-root enclosure identity failed")
    return low, high


def json_read(path):
    return json.loads(path.read_text(), parse_float=Decimal)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def csv_read(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row, _csv_record=index, _csv_line=reader.line_num)
                for index, row in enumerate(reader, 1)]


def csv_text(rows):
    require(bool(rows), "Empty required CSV artifact")
    fields = list(rows[0])
    require(all(list(row) == fields for row in rows), "Inconsistent CSV artifact schema")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, sort_keys=True, ensure_ascii=False)
                         if isinstance(value, (list, dict)) else value
                         for key, value in row.items()})
    return output.getvalue()


def index_unique(rows, key):
    result = {}
    for row in rows:
        item = key(row)
        require(item not in result, "Duplicate source identity: " + str(item))
        result[item] = row
    return result


def mean(values):
    require(bool(values), "Empty fixed layer")
    return sum(values, Fraction(0)) / len(values)


def mse(errors):
    require(len(errors) == 6, "Six-layer denominator required")
    return sum((value * value for value in errors), Fraction(0)) / 6


def exact_record(value):
    return {"fraction": str(value), "decimal50_display": decimal50(value)}


class Matches:
    def __init__(self):
        self.rows = []

    def interval(self, low, high, legacy, stage, leg, corner, pair, layer, quantity, locator):
        legacy = rat(legacy)
        require(low <= high, "Inverted candidate interval")
        difference = max(abs(low - legacy), abs(high - legacy))
        self.rows.append({"comparison_id": len(self.rows) + 1, "stage": stage,
                          "leg": leg, "corner": corner, "offset_pair": pair,
                          "bin_index": layer, "quantity": quantity,
                          "candidate_lower_exact": str(low), "candidate_upper_exact": str(high),
                          "legacy_decimal_value_exact": str(legacy),
                          "absolute_error_upper_exact": str(difference),
                          "absolute_error_upper_decimal50": decimal50(difference),
                          "tolerance_ph_exact": str(TOL), "passed": difference <= TOL,
                          "legacy_locator": locator})

    def scalar(self, value, legacy, **context):
        self.interval(value, value, legacy, **context)

    def rmse(self, value, legacy, **context):
        low, high = sqrt_interval(value)
        self.interval(low, high, legacy, **context)

    def delta(self, model, reference, legacy, **context):
        ml, mh = sqrt_interval(model)
        bl, bh = sqrt_interval(reference)
        self.interval(ml - bh, mh - bl, legacy, **context)


def settings(protocol):
    model = protocol["model"]
    require(protocol.get("frozen") is True and protocol.get("frozen_by") == "root",
            "Original protocol is not root frozen")
    require(tuple(model["elements"]) == ELEMENTS, "Changed element set/order")
    require(model["atomic_numbers"] == [62, 64, 66, 68], "Changed original atomic numbers")
    require(model["depth_edges_m"] == [200, 300, 400, 500, 600, 700, 800], "Changed layer edges")
    require(rat(model["beta"]) == rat("-3.9879452461785675e-08")
            and rat(model["intercept"]) == rat("3.339192211653703e-07"), "Changed fixed coefficients")
    primary = protocol["primary"]
    require(primary["depth_source"] == "ph_reported" and primary["bin_rule"] == "inclusive"
            and primary["weighting"] == "record", "Changed primary definition")
    rsd = {key: rat(value) for key, value in protocol["ree_repeatability_stress"]["relative_rsd"].items()}
    require(rsd == dict(zip(ELEMENTS, map(rat, (".041", ".050", ".040", ".039")))), "Changed RSD box")
    offsets = protocol["systematic_scenarios"]
    observed = list(map(rat, offsets["observed_ph_offsets"]))
    training = list(map(rat, offsets["training_reference_offsets"]))
    require(observed == list(map(rat, ("0", "-.0043", "-.0125")))
            and training == list(map(rat, ("-.02", "0", ".02"))), "Changed nine offset pairs")
    require(len(model["training_depth_mean_ph"]) == 6, "Exactly six original reference values required")
    return {"beta": rat(model["beta"]), "b": rat(model["intercept"]),
            "Z": model["atomic_numbers"], "reference": list(map(rat, model["training_depth_mean_ph"])),
            "rsd": rsd, "pairs": list(itertools.product(observed, training))}


def pair_id(observed, training):
    return "o=" + decimal50(observed) + ";t=" + decimal50(training)


def corner_id(signs):
    return "_".join(element + ("+" if value > 0 else "-") for element, value in zip(ELEMENTS, signs))


def construct_layers(samples, stored, paas, mass, config, matches):
    require(len(paas) == len(mass) == 1, "One normalization row required")
    norm = {element: rat(paas[0][element]) * rat("1e-6") / (rat(mass[0][element]) * rat("1e-3"))
            for element in ELEMENTS}
    require(all(value > 0 for value in norm.values()), "Nonpositive normalization denominator")
    sample_index = index_unique(samples, lambda row: (row["leg"], ident(row["sample_id"])))
    primary = [row for row in stored if row["variant"] == "primary"]
    require(len(primary) == 12, "Exactly twelve original primary rows required")
    original = index_unique(primary, lambda row: (row["leg"], int(row["bin_index"])))
    require(set(original) == {(leg, index) for leg in LEGS for index in range(6)}, "Fixed layers missing")
    result = {}
    union = {leg: set() for leg in LEGS}
    for leg in LEGS:
        for index in range(6):
            row = original[leg, index]
            ids = json.loads(row["sample_ids"])
            require(len(ids) == len(set(ids)) == int(row["n_records"]), "Duplicate/miscounted layer membership")
            expected = {ident(sample["sample_id"]) for sample in samples if sample["leg"] == leg
                        and 200 + 100 * index <= rat(sample["ph_reported_depth"]) <= 300 + 100 * index}
            require(set(ids) == expected, "Changed inclusive primary membership")
            members = [sample_index[leg, sid] for sid in ids]
            require(all(sample["status"] == "eligible_in_at_least_one_depth_definition" for sample in members),
                    "Unexpected original eligible status")
            require(len({sample["station"] for sample in members}) == int(row["n_stations"]), "Station count differs")
            union[leg].update(ids)
            rbar = [mean([rat(sample[element]) * rat("1e-15") / norm[element] for sample in members])
                    for element in ELEMENTS]
            require(all(value > 0 for value in rbar), "Nonpositive normalized layer concentration")
            alpha = sum(((z - 65) * value for z, value in zip(config["Z"], rbar)), Fraction(0)) / 20
            prediction = (alpha - config["b"]) / config["beta"]
            observed = mean([rat(sample["observed_ph"]) for sample in members])
            reference = config["reference"][index]
            coefficients = [(z - 65) * config["rsd"][element] * value / (20 * config["beta"])
                            for z, element, value in zip(config["Z"], ELEMENTS, rbar)]
            require(coefficients[0] > 0 and coefficients[1] > 0
                    and coefficients[2] < 0 and coefficients[3] < 0, "Coefficient-sign consistency failed")
            layer = {"leg": leg, "index": index, "low_m": 200 + 100 * index,
                     "high_m": 300 + 100 * index, "n": len(ids), "n_stations": int(row["n_stations"]),
                     "ids": ids, "rbar": rbar, "alpha": alpha, "prediction": prediction,
                     "observed": observed, "reference": reference, "C": coefficients,
                     "base_error": prediction - observed, "base_reference_error": reference - observed,
                     "source_layer_csv_record": row["_csv_record"],
                     "sample_prediction_csv_records": [sample["_csv_record"] for sample in members]}
            result[leg, index] = layer
            for field, candidate in (("observed_ph", observed), ("predicted_ph", prediction),
                                     ("baseline_ph", reference), ("prediction_error", prediction - observed),
                                     ("baseline_error", reference - observed)):
                matches.scalar(candidate, row[field], stage="primary_identity", leg=leg, corner="0",
                               pair="o=0;t=0", layer=index, quantity=field,
                               locator="layers primary CSV record " + str(row["_csv_record"]) + "/" + field)
    require({leg: len(ids) for leg, ids in union.items()} == {"leg1": 24, "leg2": 55}, "Changed 24/55 support")
    return result


def compare_corners(stress, layers, config, matches):
    cases = stress["cases"]
    require(len(cases) == 32, "Exactly sixteen corners per leg required")
    require({key: rat(value) for key, value in stress["relative_rsd"].items()} == config["rsd"],
            "Stored corner RSDs differ")
    seen, values = set(), {}
    for ci, case in enumerate(cases):
        leg = case["leg"]
        signs = tuple(case["signs_in_reported_rsd_units"][element] for element in ELEMENTS)
        require(leg in LEGS and all(value in (-1, 1) for value in signs), "Invalid corner identity")
        key = leg, signs
        require(key not in seen, "Duplicate corner")
        seen.add(key)
        require(case["scenario_id"] == corner_id(signs), "Scenario label/sign disagreement")
        old_layers = index_unique(case["layers_before_ph_offset_scenarios"], lambda row: row["bin_index"])
        require(set(old_layers) == set(range(6)), "Stored corner layers differ")
        for index in range(6):
            layer = layers[leg, index]
            old = old_layers[index]
            require(set(old["sample_ids"]) == set(layer["ids"]), "Stored corner support differs")
            predicted = layer["prediction"] + sum((c * u for c, u in zip(layer["C"], signs)), Fraction(0))
            for field, candidate in (("predicted_ph", predicted), ("observed_ph", layer["observed"]),
                                     ("baseline_ph", layer["reference"])):
                matches.scalar(candidate, old[field], stage="corner_layer", leg=leg, corner=corner_id(signs),
                               pair="before_pH_offsets", layer=index, quantity=field,
                               locator=f"stress/cases/{ci}/layers_before_ph_offset_scenarios/{index}/{field}")
        pair_seen = set()
        require(len(case["ph_offset_scenarios"]) == 9, "Stored corner offset count differs")
        for pi, old in enumerate(case["ph_offset_scenarios"]):
            o, t = rat(old["observed_offset"]), rat(old["training_offset"])
            require((o, t) in config["pairs"] and (o, t) not in pair_seen, "Changed/duplicate offset pair")
            pair_seen.add((o, t))
            model = [layers[leg, index]["base_error"] + t - o
                     + sum((c * u for c, u in zip(layers[leg, index]["C"], signs)), Fraction(0))
                     for index in range(6)]
            reference = [layers[leg, index]["base_reference_error"] + t - o for index in range(6)]
            mm, bm = mse(model), mse(reference)
            values[leg, o, t, signs] = (mm, bm)
            errors = index_unique(old["layer_errors"], lambda row: row["bin_index"])
            require(set(errors) == set(range(6)), "Stored shifted layer errors differ")
            context = {"stage": "corner_shifted_error", "leg": leg, "corner": corner_id(signs),
                       "pair": pair_id(o, t)}
            for index in range(6):
                for field, candidate in (("model_error", model[index]), ("baseline_error", reference[index])):
                    matches.scalar(candidate, errors[index][field], **context, layer=index, quantity=field,
                                   locator=f"stress/cases/{ci}/ph_offset_scenarios/{pi}/layer_errors/{index}/{field}")
            context.update(stage="corner_aggregate", layer="all6")
            for field, candidate in (("model_bias", mean(model)), ("model_mae", mean(list(map(abs, model)))),
                                     ("baseline_bias", mean(reference)), ("baseline_mae", mean(list(map(abs, reference))))):
                matches.scalar(candidate, old[field], **context, quantity=field,
                               locator=f"stress/cases/{ci}/ph_offset_scenarios/{pi}/{field}")
            for field, candidate in (("model_rmse", mm), ("baseline_rmse", bm)):
                matches.rmse(candidate, old[field], **context, quantity=field,
                             locator=f"stress/cases/{ci}/ph_offset_scenarios/{pi}/{field}")
            matches.delta(mm, bm, old["delta_rmse_model_minus_baseline"], **context,
                          quantity="delta_rmse_model_minus_baseline",
                          locator=f"stress/cases/{ci}/ph_offset_scenarios/{pi}/delta_rmse_model_minus_baseline")
        require(pair_seen == set(config["pairs"]), "Stored offset coverage incomplete")
    require(seen == {(leg, signs) for leg in LEGS for signs in itertools.product((-1, 1), repeat=4)},
            "Stored sixteen-corner coverage incomplete")
    return values


def envelope(layers, config, corners, matches):
    coefficients = []
    for leg in LEGS:
        for index in range(6):
            layer = layers[leg, index]
            row = {"leg": leg, "bin_index": index, "low_m": layer["low_m"], "high_m": layer["high_m"],
                   "n_records": layer["n"], "n_stations": layer["n_stations"], "sample_ids": layer["ids"],
                   "mean_alpha_exact": str(layer["alpha"]), "predicted_ph_exact": str(layer["prediction"]),
                   "observed_ph_exact": str(layer["observed"]), "reference_ph_exact": str(layer["reference"]),
                   "base_error_exact": str(layer["base_error"]), "base_error_decimal50": decimal50(layer["base_error"])}
            for element, value, normalized in zip(ELEMENTS, layer["C"], layer["rbar"]):
                row["mean_R_" + element + "_exact"] = str(normalized)
                row["C_" + element + "_exact"] = str(value)
                row["C_" + element + "_decimal50"] = decimal50(value)
            row.update(source_layer_csv_record=layer["source_layer_csv_record"],
                       sample_prediction_csv_records=layer["sample_prediction_csv_records"])
            coefficients.append(row)
    residual_rows, envelope_rows, certificates = [], [], []
    min_signs, max_signs = (-1, -1, 1, 1), (1, 1, -1, -1)
    vertices = list(itertools.product((-1, 1), repeat=4))
    si = ["# Post-exposure analytic envelope of the original hypothetical REE box", "", SCOPE, "",
          "The decimal input tokens, released coefficients and original offsets are treated as exact rational numbers. "
          "This mathematical model approximates the legacy binary-float implementation to the recorded 10⁻¹⁰ pH "
          "matching tolerance; it is not formal verification of every IEEE operation. All original layer and corner "
          "matching records passed before any envelope was released.", "",
          "For each of the nine discrete offset pairs, e_k(u)=e_k(0)+(t−o)+Σ_j C_kj u_j with −1≤u_j≤1. "
          "The four u values are shared coherently by all target bottles. The reference error is independent of u. "
          "RMSE is a convex norm of an affine vector, so its maximum is attained at a vertex. A finite vertex minimum "
          "does not establish an interior lower bound.", "",
          "C has layer-common signs (+,+,−,−). If every exact component lower limit e_k(0)+(t−o)−Σ_j|C_kj| "
          "is strictly positive, u=(−1,−1,+1,+1) simultaneously minimizes all errors in the positive orthant and hence "
          "their norm. Only then is the minimum below certified; otherwise it is unavailable under this criterion. "
          "The exact MSE difference has the same sign as the RMSE difference. Fractions determine certificates; "
          "50-digit Decimal square roots are display values only.", "",
          "|Leg|o|t|Positive-error condition|Lower ΔRMSE display|Upper ΔRMSE display|Strict ranking certificate|",
          "|---|---|---|---|---|---|---|"]
    for leg in LEGS:
        for o, t in config["pairs"]:
            lower = [layers[leg, index]["base_error"] + t - o
                     - sum(map(abs, layers[leg, index]["C"]), Fraction(0)) for index in range(6)]
            positive = all(value > 0 for value in lower)
            for index, value in enumerate(lower):
                residual_rows.append({"leg": leg, "offset_pair": pair_id(o, t), "o_exact": str(o), "t_exact": str(t),
                                      "bin_index": index, "component_lower_exact": str(value),
                                      "component_lower_decimal50": decimal50(value), "strictly_positive_exact": value > 0,
                                      "pair_all_six_strictly_positive": positive})
            all_values = {signs: corners[leg, o, t, signs][0] for signs in vertices}
            baseline_values = {corners[leg, o, t, signs][1] for signs in vertices}
            require(len(baseline_values) == 1, "Reference unexpectedly varies with REE corner")
            baseline = next(iter(baseline_values))
            upper = max(all_values.values())
            maximum_vertices = [corner_id(signs) for signs in vertices if all_values[signs] == upper]
            minimum = all_values[min_signs] if positive else None
            if positive:
                require(minimum == min(all_values.values()), "Positive-orthant minimum consistency failed")
                require(all_values[max_signs] == upper, "Positive-orthant maximum consistency failed")
            low_difference = minimum - baseline if minimum is not None else None
            high_difference = upper - baseline
            ranking = ("model_better_throughout_box" if high_difference < 0 else
                       "reference_better_throughout_box" if low_difference is not None and low_difference > 0
                       else "strict_uniform_ordering_not_certified")
            certificate = {"leg": leg, "offset_pair": pair_id(o, t), "o": exact_record(o), "t": exact_record(t),
                           "all_component_lower_limits_positive": positive,
                           "component_lower_limits": [exact_record(value) for value in lower],
                           "minimum_status": "certified_positive_orthant" if positive else "unproved_by_selected_criterion",
                           "minimum_vertex": corner_id(min_signs) if positive else None,
                           "model_mse_lower": exact_record(minimum) if minimum is not None else None,
                           "model_mse_upper": exact_record(upper), "maximum_vertices": maximum_vertices,
                           "reference_mse": exact_record(baseline),
                           "delta_mse_lower": exact_record(low_difference) if low_difference is not None else None,
                           "delta_mse_upper": exact_record(high_difference), "strict_ranking": ranking,
                           "ranking_uses_exact_mse_sign": True}
            certificates.append(certificate)
            low_display = delta_rmse_display(minimum, baseline) if minimum is not None else ""
            upper_display = delta_rmse_display(upper, baseline)
            envelope_rows.append({"leg": leg, "offset_pair": pair_id(o, t), "o_exact": str(o), "t_exact": str(t),
                                  "all_component_lower_limits_positive": positive,
                                  "minimum_status": certificate["minimum_status"],
                                  "minimum_component_margin_exact": str(min(lower)),
                                  "model_mse_lower_exact": str(minimum) if minimum is not None else "",
                                  "model_mse_upper_exact": str(upper), "reference_mse_exact": str(baseline),
                                  "delta_mse_lower_exact": str(low_difference) if low_difference is not None else "",
                                  "delta_mse_upper_exact": str(high_difference),
                                  "model_rmse_lower_decimal50": sqrt_display(minimum) if minimum is not None else "",
                                  "model_rmse_upper_decimal50": sqrt_display(upper),
                                  "reference_rmse_decimal50": sqrt_display(baseline),
                                  "delta_rmse_lower_decimal50": low_display, "delta_rmse_upper_decimal50": upper_display,
                                  "minimum_vertex": certificate["minimum_vertex"] or "",
                                  "maximum_vertices": maximum_vertices, "strict_ranking_certificate": ranking})
            low_si = format(Decimal(low_display), "+.6f") if low_display else "unproved"
            upper_si = format(Decimal(upper_display), "+.6f")
            si.append("|" + "|".join([leg, decimal50(o), decimal50(t), "yes" if positive else "no",
                                       low_si, upper_si, ranking]) + "|")
    require(len(envelope_rows) == 18 and len(residual_rows) == 108 and len(coefficients) == 12, "Output coverage differs")
    si += ["", "The SI table rounds displayed RMSE differences to six decimals; CSV uses 50 significant digits. "
           "Displayed square-root values are approximations, not directed-rounded bound endpoints; the exact MSE "
           "certificates determine ranking. The accompanying CSV/JSON retains exact "
           "rational MSE bounds/differences and complete component margins. The certificate covers the continuous "
           "four-coordinate REE box only at the nine original discrete pH choices. No continuous pH interval, "
           "alternative covariance structure, sample-specific random errors, coefficient propagation, causal attribution "
           "or real-world error bound is established. Unproved minima remain blank; no sub-box or new optimization is used.", "",
           "The program author is reviewer B. Author checks are not independent numerical validation; a separate "
           "source/code/results review is required before this output can be adopted in the manuscript.", ""]
    return {"layer_coefficients.csv": csv_text(coefficients),
            "pair_residual_lower_bounds.csv": csv_text(residual_rows),
            "pair_envelope.csv": csv_text(envelope_rows),
            "ranking_certificates.json": json_text({"scope": SCOPE, "pairs": certificates,
                                                     "independent_numerical_validation": False}),
            "vertex_matching.csv": csv_text(matches.rows),
            "continuous_box_supplement.md": "\n".join(si)}


def main(plan_path, output_path):
    require(sys.version_info[:2] == (3, 12), "Approved Python3.12 runtime required")
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text())
    require(plan.get("schema") == "gp15-continuous-box-plan/v1", "Unknown plan schema")
    require(plan.get("frozen") is True and plan.get("frozen_by") == "root", "ROOT_FREEZE_REQUIRED")
    require(plan.get("task_root_relative") == "../..", "Unexpected task-root contract")
    root = (plan_path.parent / "../..").resolve()
    script = Path(__file__).resolve()
    require(script == root / SCRIPT and sha(script) == plan["analysis_script_sha256"], "Script identity mismatch")
    require(plan["comparison_tolerance_ph"] == "1e-10" and plan["decimal_display_precision"] == DISPLAY_PRECISION
            and plan["sqrt_enclosure_decimal_places"] == SQRT_ENCLOSURE_PLACES, "Changed numerical contract")
    entries = index_unique(plan["inputs"], lambda entry: entry["role"])
    require(set(entries) == set(WHITELIST), "Exactly six input roles required")
    sources = {}
    for role, relative in WHITELIST.items():
        require(entries[role]["path"] == relative, "Input path not on whitelist")
        path = root / relative
        require(path.resolve() == path and sha(path) == entries[role]["sha256"], "Input identity mismatch: " + role)
        sources[role] = path
    relative_out = Path(plan["output_relative"])
    require(not relative_out.is_absolute() and ".." not in relative_out.parts, "Unsafe output-relative path")
    target = (root / relative_out).resolve()
    require(target.parent == (root / "iteration-2026-10-27/analysis").resolve(), "Output must be a new analysis child")
    require(output_path.resolve() == target and not target.exists(), "Output differs from plan or already exists")
    protocol = json_read(sources["protocol"])
    config = settings(protocol)
    matches = Matches()
    layers = construct_layers(csv_read(sources["samples"]), csv_read(sources["layers"]),
                              csv_read(sources["paas"]), csv_read(sources["mass"]), config, matches)
    corners = compare_corners(json_read(sources["stress"]), layers, config, matches)
    require(plan["expected_matching_records"] == 6108 and len(matches.rows) == 6108,
            "Incomplete fixed primary/corner comparison coverage")
    failed = [row for row in matches.rows if not row["passed"]]
    if failed:
        status = "UNAVAILABLE_LEGACY_MATCH_FAILED"
        artifacts = {"vertex_matching.csv": csv_text(matches.rows),
                     "failure.json": json_text({"status": status, "scope": SCOPE,
                                                 "n_failed": len(failed), "failed_comparison_ids": [row["comparison_id"] for row in failed],
                                                 "envelope_results_available": False,
                                                 "reason": "At least one fixed 1e-10 pH match failed. No C/envelope/ranking/SI result is released; no tolerance adjustment or alternative box is attempted."})}
        require(set(artifacts) == set(plan["failure_outputs"]), "Failure artifact contract differs")
    else:
        status = "ENVELOPE_CALCULATED_PENDING_INDEPENDENT_REVIEW"
        artifacts = envelope(layers, config, corners, matches)
        require(set(artifacts) == set(plan["success_outputs"]), "Success artifact contract differs")
    source_manifest = {"status": status, "scope": SCOPE, "plan_path": str(plan_path.relative_to(root)),
                       "plan_sha256": sha(plan_path), "script_path": SCRIPT, "script_sha256": sha(script),
                       "inputs": plan["inputs"], "arithmetic": plan["arithmetic"],
                       "stop_rules": plan["stop_rules"], "python_version": sys.version.split()[0],
                       "n_matching_records": len(matches.rows), "n_failed_matches": len(failed),
                       "program_author_role": "reviewer_B", "self_check_is_independent_validation": False,
                       "independent_numerical_validation_completed": False,
                       "all_GP15_already_exposed": True, "no_network": True,
                       "original_sources_results_weights_and_coefficients_unchanged": True}
    artifacts["source_manifest.json"] = json_text(source_manifest)
    for role, path in sources.items():
        require(sha(path) == entries[role]["sha256"], "Source changed before output write")
    require(sha(script) == plan["analysis_script_sha256"] and sha(plan_path) == source_manifest["plan_sha256"],
            "Plan/code changed before output write")
    target.mkdir(exist_ok=False)
    hashes = []
    for name in sorted(artifacts):
        payload = artifacts[name].encode("utf-8")
        with (target / name).open("xb") as handle:
            handle.write(payload)
        hashes.append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    output_manifest = {"status": status, "scope": SCOPE, "outputs": hashes,
                       "output_relative": str(relative_out),
                       "self_hash_omitted": "output_manifest.json cannot contain its own hash; every other output is hashed."}
    with (target / "output_manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(output_manifest))
    print(json_text({"status": status, "output": str(target), "n_matches": len(matches.rows),
                     "n_failed_matches": len(failed), "independent_numerical_validation_completed": False}), end="")
    return 2 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    sys.exit(main(arguments.plan, arguments.out))
