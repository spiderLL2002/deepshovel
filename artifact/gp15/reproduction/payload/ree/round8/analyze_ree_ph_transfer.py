#!/usr/bin/env python3
"""Fixed Lin–Catling transfer comparison; source-bound and root-freeze gated.

No source script is executed. A check-only invocation verifies hashes and headers,
never computing a chemical response. Full computation needs a root-frozen protocol.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import statistics
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

ELEMENTS = ('Sm', 'Gd', 'Dy', 'Er')
FIXED_BETA = -3.9879452461785675e-08
FIXED_INTERCEPT = 3.339192211653703e-07
FIXED_BASELINE = [7.73087871249336, 7.6898707001764945,
                  7.658636222030023, 7.6315530378663246,
                  7.606188333846088, 7.584325607003087]
REQUIRED_ROLES = ('leg1_ree', 'leg1_ph', 'leg2_ree', 'leg2_ph', 'paas',
                  'molar_mass', 'atomic_numbers', 'author_model_code',
                  'author_proxy_code', 'author_notebook', 'leg2_ree_intercal')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(base, path):
    path = Path(path).expanduser()
    return path if path.is_absolute() else base / path


def load_sources(input_path):
    raw = json.loads(input_path.read_text())
    sources = {}
    for item in raw['sources']:
        role = item['role']
        if role in sources:
            raise ValueError('Duplicate source role: ' + role)
        path = resolve(input_path.parent, item['path'])
        if sha(path) != item['sha256']:
            raise ValueError('Source SHA mismatch: ' + role)
        sources[role] = dict(item, resolved_path=path)
    missing = set(REQUIRED_ROLES) - set(sources)
    if missing:
        raise ValueError('Missing required roles: ' + ', '.join(sorted(missing)))
    return raw, sources


def headers(source):
    with source['resolved_path'].open(encoding='utf-8-sig', newline='') as handle:
        return next(csv.reader(handle))


def read_csv(source):
    with source['resolved_path'].open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def column_mapping(source, kind):
    fields = headers(source)
    supplied = source.get('columns', {})
    mapping = {}
    for name, default in [('sample_id', 'Sample_ID'), ('station', 'Station_ID'),
                          ('event', 'Event_ID'), ('depth', 'Sample_Depth')]:
        mapping[name] = supplied.get(name, default)
    def one(key, prefix):
        found = [f for f in fields if f.startswith(prefix)]
        if key in supplied:
            return supplied[key]
        if len(found) != 1:
            raise ValueError(f'Ambiguous {source["role"]} {key}: {found}')
        return found[0]
    if kind == 'ree':
        for e in ELEMENTS:
            mapping[e] = one(e, e + '_D_CONC_BOTTLE_')
            mapping[e + '_flag'] = one(e + '_flag', 'Flag_' + e + '_D_CONC_BOTTLE_')
    else:
        mapping['ph'] = one('ph', 'PH_TOT_BOTTLE_')
        mapping['ph_flag'] = one('ph_flag', 'Flag_PH_TOT_BOTTLE_')
    for name in mapping.values():
        if name not in fields:
            raise ValueError(f'Missing mapped field {name} in {source["role"]}')
    return mapping


def finite(text):
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def identity(text):
    text = str(text).strip()
    if not text or text.lower() in ('nd', 'nan', 'na'):
        raise ValueError('Missing source identity')
    try:
        value = Decimal(text)
        return format(value.normalize(), 'f') if value.is_finite() else text
    except InvalidOperation:
        return text


def index_rows(rows, mapping, role):
    out = {}
    for number, row in enumerate(rows, 2):
        key = identity(row[mapping['sample_id']])
        if key in out:
            raise ValueError(f'Duplicate sample ID in {role}: {key}')
        out[key] = (number, row)
    return out


def normalizers(sources, cfg):
    values = {}
    for role in ('paas', 'molar_mass', 'atomic_numbers'):
        rows = read_csv(sources[role])
        if len(rows) != 1:
            raise ValueError('Expected one normalization row: ' + role)
        values[role] = {e: float(rows[0][e]) for e in ELEMENTS}
    atom = [values['atomic_numbers'][e] for e in ELEMENTS]
    if atom != [62, 64, 66, 68]:
        raise ValueError('Atomic number source differs from original selected elements')
    norm = {e: values['paas'][e] * 1e-6 /
            (values['molar_mass'][e] * 1e-3) for e in ELEMENTS}
    if not all(v > 0 and math.isfinite(v) for v in norm.values()):
        raise ValueError('Invalid PAAS normalization')
    model = cfg['model']
    if model['beta'] != FIXED_BETA or model['intercept'] != FIXED_INTERCEPT:
        raise ValueError('Protocol changed fixed published calibration')
    if model['training_depth_mean_ph'] != FIXED_BASELINE:
        raise ValueError('Protocol changed fixed original depth-only baseline')
    if model['depth_edges_m'] != [200, 300, 400, 500, 600, 700, 800]:
        raise ValueError('Protocol changed fixed depth layers')
    return norm


def predict(concentrations, norm):
    # Explicit OLS slope with intercept against 62,64,66,68; sum(x-xbar)^2=20.
    normalized = [concentrations[e] * 1e-15 / norm[e] for e in ELEMENTS]
    alpha = sum((x - 65) * y for x, y in zip((62, 64, 66, 68), normalized)) / 20
    return alpha, (alpha - FIXED_INTERCEPT) / FIXED_BETA


def prepare_pairs(sources, cfg, norm):
    tables, maps, indexes = {}, {}, {}
    for leg in ('leg1', 'leg2'):
        for kind in ('ree', 'ph'):
            role = leg + '_' + kind
            maps[role] = column_mapping(sources[role], kind)
            tables[role] = read_csv(sources[role])
            indexes[role] = index_rows(tables[role], maps[role], role)
    leg1_ids = set(indexes['leg1_ree']) | set(indexes['leg1_ph'])
    leg1_stations = {identity(row[maps[role]['station']])
                     for role in ('leg1_ree', 'leg1_ph') for row in tables[role]}
    allowed = set(cfg['eligibility']['allowed_flag_values'])
    lo, hi = cfg['eligibility']['depth_union_for_response_processing_m']
    pairs, audit = [], []
    for leg in ('leg1', 'leg2'):
        rr, pr = leg + '_ree', leg + '_ph'
        rm, pm = maps[rr], maps[pr]
        for sid in sorted(set(indexes[rr]) | set(indexes[pr])):
            entry = {'leg': leg, 'sample_id': sid, 'status': None}
            if sid not in indexes[rr] or sid not in indexes[pr]:
                entry['status'] = 'not_shared_sample_id'
                audit.append(entry)
                continue
            rline, r = indexes[rr][sid]
            pline, p = indexes[pr][sid]
            rs, ps = identity(r[rm['station']]), identity(p[pm['station']])
            re, pe = identity(r[rm['event']]), identity(p[pm['event']])
            rd, pd = finite(r[rm['depth']]), finite(p[pm['depth']])
            entry.update(station=rs, event=re, ree_line=rline, ph_line=pline,
                         ree_reported_depth=rd, ph_reported_depth=pd)
            if rs != ps or re != pe:
                entry['status'] = 'station_or_event_mismatch'
            elif leg == 'leg2' and (sid in leg1_ids or rs in leg1_stations):
                entry['status'] = 'leg2_station_or_sample_seen_in_leg1'
            elif rd is None or pd is None:
                entry['status'] = 'missing_finite_depth'
            elif not ((lo <= rd <= hi) or (lo <= pd <= hi)):
                entry['status'] = 'outside_predeclared_depth_union'
            elif p[pm['ph_flag']].strip() not in allowed or any(
                    r[rm[e + '_flag']].strip() not in allowed for e in ELEMENTS):
                entry['status'] = 'bottle_flag_not_allowed'
            else:
                concentrations = {e: finite(r[rm[e]]) for e in ELEMENTS}
                observed = finite(p[pm['ph']])
                if observed is None or any(v is None or v <= 0
                                           for v in concentrations.values()):
                    entry['status'] = 'missing_or_invalid_response'
                else:
                    alpha, predicted = predict(concentrations, norm)
                    entry['status'] = 'eligible_in_at_least_one_depth_definition'
                    pairs.append(dict(entry, alpha=alpha, predicted_ph=predicted,
                                      observed_ph=observed, **concentrations,
                                      ree_source_sha=sources[rr]['sha256'],
                                      ph_source_sha=sources[pr]['sha256'],
                                      ree_fields={e: rm[e] for e in ELEMENTS},
                                      ph_field=pm['ph']))
            audit.append(entry)
    return pairs, audit, maps


def in_bin(depth, low, high, rule, last):
    if rule == 'inclusive':
        return low <= depth <= high
    if rule == 'half_open':
        return low <= depth < high or (last and depth == high)
    raise ValueError('Unknown bin rule: ' + rule)


def weighted_mean(rows, field, weighting):
    if weighting == 'record':
        return statistics.mean(row[field] for row in rows)
    if weighting == 'station':
        groups = defaultdict(list)
        for row in rows:
            groups[row['station']].append(row[field])
        return statistics.mean(statistics.mean(values) for values in groups.values())
    raise ValueError('Unknown weighting: ' + weighting)


def layer_summary(rows, variant, cfg):
    edges = cfg['model']['depth_edges_m']
    depth_key = variant['depth_source'] + '_depth'
    result = []
    for i, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        selected = [r for r in rows if in_bin(r[depth_key], low, high,
                    variant['bin_rule'], i == len(edges) - 2)]
        stations = sorted({r['station'] for r in selected})
        item = {'bin_index': i, 'low_m': low, 'high_m': high, 'n_records': len(selected),
                'n_stations': len(stations), 'stations': stations,
                'sample_ids': [r['sample_id'] for r in selected],
                'domain_support': len(stations) >= cfg['support']['min_stations_per_layer_for_domain_statement'],
                'observed_ph': None, 'predicted_ph': None,
                'baseline_ph': FIXED_BASELINE[i], 'prediction_error': None,
                'baseline_error': None}
        if selected:
            item['observed_ph'] = weighted_mean(selected, 'observed_ph', variant['weighting'])
            item['predicted_ph'] = weighted_mean(selected, 'predicted_ph', variant['weighting'])
            item['prediction_error'] = item['predicted_ph'] - item['observed_ph']
            item['baseline_error'] = item['baseline_ph'] - item['observed_ph']
        result.append(item)
    return result


def aggregate(layers, observed_offset=0.0, training_offset=0.0):
    available = [r for r in layers if r['observed_ph'] is not None]
    ret = {'n_nonempty_layers': len(available),
           'nonempty_bin_indexes': [r['bin_index'] for r in available],
           'n_supported_layers': sum(r['domain_support'] for r in layers),
           'full_domain_support': all(r['domain_support'] for r in layers),
           'observed_offset': observed_offset, 'training_offset': training_offset}
    for name, field in [('model', 'predicted_ph'), ('baseline', 'baseline_ph')]:
        errors = [r[field] + training_offset - r['observed_ph'] - observed_offset
                  for r in available]
        ret[name + '_bias'] = statistics.mean(errors) if errors else None
        ret[name + '_mae'] = statistics.mean(abs(e) for e in errors) if errors else None
        ret[name + '_rmse'] = math.sqrt(statistics.mean(e*e for e in errors)) if errors else None
    ret['delta_rmse_model_minus_baseline'] = (ret['model_rmse'] - ret['baseline_rmse']) if available else None
    return ret


def quantile(values, probability):
    values = sorted(values)
    pos = (len(values) - 1) * probability
    left = int(pos)
    return values[left] + (values[min(left+1, len(values)-1)] - values[left]) * (pos-left)


def station_bootstrap(rows, primary, cfg, seed):
    groups = defaultdict(list)
    for row in rows:
        if 200 <= row['ph_reported_depth'] <= 800:
            groups[row['station']].append(row)
    stations = sorted(groups)
    point_layers = layer_summary(rows, primary, cfg)
    needed_bins = {r['bin_index'] for r in point_layers if r['n_records']}
    report = {'n_stations': len(stations), 'stations': stations,
              'interval_scope': cfg['bootstrap']['scope'], 'seed': seed,
              'required_bins': sorted(needed_bins), 'status': 'insufficient_stations',
              'intervals': None, 'valid_replicates': 0, 'attempted_replicates': 0}
    if len(stations) < cfg['support']['min_stations_for_cluster_interval'] or not needed_bins:
        return report
    randomizer = random.Random(seed)
    collected = []
    for _ in range(cfg['bootstrap']['replicates']):
        chosen = randomizer.choices(stations, k=len(stations))
        # Repetition repeats the entire station's bottle block, preserving within-station dependence.
        sample = [row for station in chosen for row in groups[station]]
        layers = layer_summary(sample, primary, cfg)
        present = {r['bin_index'] for r in layers if r['n_records']}
        if present == needed_bins:
            collected.append(aggregate(layers))
    report.update(attempted_replicates=cfg['bootstrap']['replicates'], valid_replicates=len(collected))
    fraction = len(collected) / cfg['bootstrap']['replicates']
    report['valid_fraction'] = fraction
    if fraction < cfg['support']['min_valid_bootstrap_fraction']:
        report['status'] = 'insufficient_complete_support_replicates'
        return report
    percentiles = cfg['bootstrap']['interval_percentiles']
    fields = ['model_bias', 'model_mae', 'model_rmse', 'baseline_bias',
              'baseline_mae', 'baseline_rmse', 'delta_rmse_model_minus_baseline']
    report['intervals'] = {f: [quantile([r[f] for r in collected], q/100)
                             for q in percentiles] for f in fields}
    report['status'] = 'conditional_station_resampling_interval_available'
    return report


def leave_one_station_out(rows, primary, cfg):
    point_layers = layer_summary(rows, primary, cfg)
    required = {r['bin_index'] for r in point_layers if r['n_records']}
    stations = sorted({r['station'] for r in rows
                       if 200 <= r['ph_reported_depth'] <= 800})
    cases = []
    for station in stations:
        kept = [r for r in rows if r['station'] != station]
        layers = layer_summary(kept, primary, cfg)
        present = {r['bin_index'] for r in layers if r['n_records']}
        complete = present == required
        cases.append({'deleted_station': station, 'same_layer_support_as_point': complete,
                      'missing_bin_indexes': sorted(required - present),
                      'layers': layers,
                      'aggregate_on_same_layer_support': aggregate(layers) if complete else None})
    return {'required_bin_indexes': sorted(required), 'point_aggregate': aggregate(point_layers),
            'rule': 'Keep all six layers. A deletion missing an originally nonempty layer has no aggregate comparison; never change the layer denominator.',
            'cases': cases}


def repeatability_stress(pairs, primary, cfg, norm, sources):
    settings = cfg['ree_repeatability_stress']
    rsd = settings['relative_rsd']
    if set(rsd) != set(ELEMENTS) or not all(0 <= rsd[e] < 1 for e in ELEMENTS):
        raise ValueError('Invalid fixed element-specific reference RSD stress')
    reference = sources[settings['source_role']]
    cases = []
    for signs in itertools.product((-1, 1), repeat=len(ELEMENTS)):
        shifts = dict(zip(ELEMENTS, signs))
        factors = {e: 1 + shifts[e] * rsd[e] for e in ELEMENTS}
        scenario_id = '_'.join(e + ('+' if shifts[e] > 0 else '-') for e in ELEMENTS)
        perturbed = []
        for row in pairs:
            concentrations = {e: row[e] * factors[e] for e in ELEMENTS}
            alpha, predicted = predict(concentrations, norm)
            # One direction per element is shared by every bottle, not random bottle noise.
            perturbed.append(dict(row, alpha=alpha, predicted_ph=predicted))
        for leg in ('leg1', 'leg2'):
            rows = [r for r in perturbed if r['leg'] == leg]
            layers = layer_summary(rows, primary, cfg)
            ph_cases = []
            for observed in cfg['systematic_scenarios']['observed_ph_offsets']:
                for training in cfg['systematic_scenarios']['training_reference_offsets']:
                    combined = aggregate(layers, observed, training)
                    combined['layer_errors'] = [
                        {'bin_index': r['bin_index'],
                         'model_error': (r['predicted_ph'] + training - r['observed_ph'] - observed)
                                        if r['observed_ph'] is not None else None,
                         'baseline_error': (r['baseline_ph'] + training - r['observed_ph'] - observed)
                                           if r['observed_ph'] is not None else None}
                        for r in layers]
                    ph_cases.append(combined)
            cases.append({'leg': leg, 'scenario_id': scenario_id,
                          'signs_in_reported_rsd_units': shifts, 'multiplicative_factors': factors,
                          'layers_before_ph_offset_scenarios': layers,
                          'ph_offset_scenarios': ph_cases})
    return {'interpretation': settings['interpretation'],
            'source_role': settings['source_role'], 'source_sha256': reference['sha256'],
            'source_locator': settings['source_locator'], 'relative_rsd': rsd,
            'n_element_vectors': 16, 'n_leg_vector_cases': len(cases),
            'n_crossed_ph_scenarios_per_leg_vector': 9,
            'baseline_rule': 'No REE perturbation of depth baseline. Both model and baseline receive identical training-reference pH shifts and are compared with identical observed-target shifts and layer support.',
            'cases': cases}


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})


def plot_result(layers, summaries, out):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['svg.hashsalt'] = 'ree-r8-fixed-transfer'
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    for leg, ax in zip(('leg1', 'leg2'), axes[0]):
        rr = [r for r in layers if r['leg'] == leg and r['variant'] == 'primary']
        depth = [(r['low_m'] + r['high_m'])/2 for r in rr]
        for field, label, color, marker in [('observed_ph', 'Observed', '#1b7565', 'o'),
                                          ('predicted_ph', 'Fixed REE model', '#b2483d', 's'),
                                          ('baseline_ph', 'Fixed depth baseline', '#666666', '^')]:
            ax.plot([r[field] if r[field] is not None else float('nan') for r in rr],
                    depth, marker=marker, color=color, label=label)
        ax.invert_yaxis(); ax.set_xlabel('pH, total scale at 25°C'); ax.set_ylabel('Layer midpoint (m)')
        ax.set_title('A. Leg1 (exposure acknowledged)' if leg == 'leg1' else 'B. Leg2 reserved-station response check')
        ax.legend(frameon=False, fontsize=8)
    ax = axes[1, 0]
    rr = [r for r in layers if r['leg'] == 'leg2' and r['variant'] == 'primary']
    for field, label, color in [('prediction_error', 'Fixed REE model', '#b2483d'),
                              ('baseline_error', 'Fixed depth baseline', '#666666')]:
        ax.plot([r['bin_index'] for r in rr],
                [r[field] if r[field] is not None else float('nan') for r in rr],
                'o-', label=label, color=color)
    ax.axhline(0, color='#aaaaaa', lw=.8)
    ax.set_xticks(range(6), ['200–300', '300–400', '400–500', '500–600', '600–700', '700–800'], rotation=25)
    ax.set_ylabel('Predicted minus observed pH'); ax.set_title('C. All six fixed layers (Leg2)')
    ax.legend(frameon=False, fontsize=8)
    ax = axes[1, 1]
    ss = [r for r in summaries if r['leg'] == 'leg2']
    for j, row in enumerate(ss):
        if row['model_rmse'] is not None:
            ax.plot(row['model_rmse'], j, 's', color='#b2483d')
            ax.plot(row['baseline_rmse'], j, '^', color='#666666')
    ax.set_yticks(range(len(ss)), [r['variant'].replace('_', ' ') for r in ss]); ax.invert_yaxis()
    ax.set_xlabel('Across-layer RMSE (pH)'); ax.set_title('D. Fixed sensitivities, matched support')
    fig.suptitle('Regional transfer of a fixed REE–pH calibration; no new fit', fontsize=14)
    fig.text(.04, .02, 'No clipping. Layer means share stations and may share boundary samples. Systematic scenarios are not confidence intervals.\n'
             'Legs share cruise and laboratory methods; this figure does not test paleo-carbonate preservation.', fontsize=9)
    fig.tight_layout(rect=(0, .07, 1, .95))
    fig.savefig(out/'ree_ph_transfer.png', dpi=180)
    fig.savefig(out/'ree_ph_transfer.svg', metadata={'Date': None})
    plt.close(fig)


def run(sources, cfg, out):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory is nonempty; use task resume instead of overwriting')
    out.mkdir(parents=True, exist_ok=True)
    norm = normalizers(sources, cfg)
    pairs, audit, mappings = prepare_pairs(sources, cfg, norm)
    primary = dict(cfg['primary'], name='primary')
    variants = [primary] + cfg['sensitivities']
    all_layers, summaries, scenarios, boot, loso = [], [], [], {}, {}
    for leg_index, leg in enumerate(('leg1', 'leg2')):
        rows = [r for r in pairs if r['leg'] == leg]
        for variant in variants:
            layers = layer_summary(rows, variant, cfg)
            all_layers.extend(dict(r, leg=leg, variant=variant['name']) for r in layers)
            summaries.append(dict(aggregate(layers), leg=leg, variant=variant['name']))
            if variant['name'] == 'primary':
                for observed in cfg['systematic_scenarios']['observed_ph_offsets']:
                    for training in cfg['systematic_scenarios']['training_reference_offsets']:
                        scenarios.append(dict(aggregate(layers, observed, training), leg=leg))
        boot[leg] = station_bootstrap(rows, primary, cfg, cfg['bootstrap']['seed'] + leg_index)
        loso[leg] = leave_one_station_out(rows, primary, cfg)
    write_csv(out/'pairing_audit.csv', audit)
    write_csv(out/'sample_predictions.csv', pairs)
    write_csv(out/'layer_summary.csv', all_layers)
    write_csv(out/'systematic_scenarios.csv', scenarios)
    report = {'candidate_id': 'REE-R8-01', 'purpose': 'exploratory',
              'validation_source_ids': [], 'source_columns': mappings,
              'source_sha256': {k: s['sha256'] for k, s in sources.items()},
              'published_fixed_model': cfg['model'], 'paas_mol_per_g': norm,
              'pairing_counts': dict(Counter(r['leg'] + ':' + r['status'] for r in audit)),
              'summaries': summaries, 'paper_eligibility': 'not_automatically_assessed',
              'limitations': cfg['stopping_and_interpretation'],
              'systematic_scenario_meaning': cfg['systematic_scenarios']['meaning']}
    (out/'aggregate_summary.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    (out/'cluster_bootstrap.json').write_text(json.dumps(boot, indent=2, allow_nan=False))
    (out/'leave_one_station_out.json').write_text(json.dumps(loso, indent=2, allow_nan=False))
    stress = repeatability_stress(pairs, primary, cfg, norm, sources)
    (out/'ree_repeatability_stress.json').write_text(json.dumps(stress, indent=2, allow_nan=False))
    plot_result(all_layers, summaries, out)
    return {'n_eligible_pairs_in_depth_union': len(pairs), 'leg_counts': dict(Counter(r['leg'] for r in pairs)),
            'n_layer_rows': len(all_layers), 'n_systematic_scenarios': len(scenarios),
            'prediction_clipping': False, 'new_model_fit': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if args.plan:
        plan = json.loads(args.plan.read_text())['config']
        args.input = resolve(args.plan.parent, plan['input_path'])
        args.protocol = resolve(args.plan.parent, plan['protocol_path'])
        args.out = resolve(args.plan.parent, plan['out_dir'])
    if not args.input:
        parser.error('--input or --plan is required')
    _, sources = load_sources(args.input)
    if args.check_only:
        mapping = {leg+'_'+kind: column_mapping(sources[leg+'_'+kind], kind)
                   for leg in ('leg1', 'leg2') for kind in ('ree', 'ph')}
        print(json.dumps({'check_only': True, 'source_sha_verified': True,
                          'headers_only': True, 'chemical_response_computed': False,
                          'columns': mapping}, indent=2))
        return
    if not args.protocol or not args.out:
        parser.error('--protocol and --out are required for computation')
    cfg = json.loads(args.protocol.read_text())
    if cfg.get('frozen') is not True or cfg.get('frozen_by') != 'root':
        raise ValueError('Only a root-frozen protocol permits response computation')
    if cfg['input_sha256'] != sha(args.input) or cfg['analysis_script_sha256'] != sha(__file__):
        raise ValueError('Input or script changed since freeze')
    result = run(sources, cfg, args.out)
    result.update(input_sha256=sha(args.input), protocol_sha256=sha(args.protocol),
                  analysis_script_sha256=sha(__file__))
    result['output_files'] = [{'file': p.name, 'sha256': sha(p)} for p in sorted(args.out.iterdir()) if p.is_file()]
    (args.out/'run_manifest.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
