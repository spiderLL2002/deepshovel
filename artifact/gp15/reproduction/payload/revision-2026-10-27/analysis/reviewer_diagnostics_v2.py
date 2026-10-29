#!/usr/bin/env python3
"""Post-review GP15 diagnostics, requiring a source/code-bound root freeze.

This program independently reads the original CSVs. It never imports or modifies
the original analysis, fits a coefficient, corrects target pH, or selects results.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

ELEMENTS=('Sm','Gd','Dy','Er')
ATOMIC=(62,64,66,68)
BETA=-3.9879452461785675e-08
INTERCEPT=3.339192211653703e-07
BASELINE=(7.73087871249336,7.6898707001764945,7.658636222030023,
          7.6315530378663246,7.606188333846088,7.584325607003087)
EDGES=(200,300,400,500,600,700,800)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def ident(value):
    value=str(value).strip()
    if value.lower() in ('','nd','nan','na'):raise ValueError('Missing identifier')
    try:
        num=Decimal(value)
        return format(num.normalize(),'f') if num.is_finite() else value
    except InvalidOperation:return value
def num(value):
    try:
        x=float(value)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None
def sortid(values):
    def key(value):
        try:return (0,Decimal(value))
        except InvalidOperation:return (1,value)
    return sorted(values,key=key)
def read(path):
    with path.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        return [(n,row) for n,row in enumerate(reader,2)],reader.fieldnames
def csvout(path,rows):
    if not rows:raise ValueError('Empty declared table: '+str(path))
    fields=list(rows[0])
    if any(set(row)!=set(fields) for row in rows):raise ValueError('Inconsistent output schema')
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(list,dict)) else v for k,v in row.items()})
def column(fields,prefix):
    found=[f for f in fields if f.startswith(prefix)]
    if len(found)!=1:raise ValueError('Ambiguous field '+prefix)
    return found[0]
def tableindex(rows):
    out={}
    for line,row in rows:
        sid=ident(row['Sample_ID'])
        if sid in out:raise ValueError('Duplicate raw sample ID '+sid)
        out[sid]=(line,row)
    return out
def isosame(left,right):
    try:return datetime.fromisoformat(left.replace('Z','+00:00'))==datetime.fromisoformat(right.replace('Z','+00:00'))
    except ValueError:return None
def bins(depth):return [i for i,(lo,hi) in enumerate(zip(EDGES[:-1],EDGES[1:])) if depth is not None and lo<=depth<=hi]
def alphav(r):return sum((z-65)*v for z,v in zip(ATOMIC,r))/20
def decompose(errors):
    if len(errors)!=6:raise ValueError('Diagnostic denominator must retain all six layers')
    bias=statistics.mean(errors);mse=statistics.mean(e*e for e in errors)
    centered=statistics.mean((e-bias)**2 for e in errors)
    residual=mse-bias*bias-centered
    if abs(residual)>1e-14:raise ValueError('MSE decomposition identity failed')
    return {'n_layers':6,'mean_error':bias,'mse':mse,'rmse':math.sqrt(mse),
            'squared_mean_error':bias*bias,'centered_mse':centered,'centered_rmse':math.sqrt(centered),
            'squared_mean_error_fraction_of_mse':bias*bias/mse if mse else None,
            'identity_residual':residual}
def weights(rows,method):
    if method=='record':return [1/len(rows)]*len(rows)
    counts=Counter(r['station'] for r in rows)
    return [1/(len(counts)*counts[r['station']]) for r in rows]
def wmean(values,ww):return sum(v*w for v,w in zip(values,ww))
def layers(rows,method='record'):
    out=[]
    for i,(lo,hi) in enumerate(zip(EDGES[:-1],EDGES[1:])):
        ss=[r for r in rows if i in r['ph_bins']]
        if not ss:raise ValueError('Empty layer; do not change denominator')
        ww=weights(ss,method)
        observed=wmean([r['observed_ph'] for r in ss],ww)
        predicted=wmean([r['predicted_ph'] for r in ss],ww)
        out.append({'bin_index':i,'low_m':lo,'high_m':hi,'n_records':len(ss),
                    'n_stations':len({r['station'] for r in ss}),'stations':sortid({r['station'] for r in ss}),
                    'sample_ids':[r['sample_id'] for r in ss],
                    'observed_ph':observed,'predicted_ph':predicted,'baseline_ph':BASELINE[i],
                    'prediction_error':predicted-observed,'baseline_error':BASELINE[i]-observed})
    return out

def run(protocol_path,out,check_only=False):
    cfg=json.loads(protocol_path.read_text());task=(protocol_path.parent/Path(cfg['task_root_relative'])).resolve()
    source={}
    for item in cfg['inputs']:
        f=task/item['path']
        if sha(f)!=item['sha256']:raise ValueError('Input SHA mismatch: '+item['role'])
        source[item['role']]={**item,'path':f,'task_relative_path':item['path']}
    headers={role:read(source[role]['path'])[1] for role in ('leg1_ree','leg1_ph','leg2_ree','leg2_ph')}
    if check_only:return {'status':'INPUTS_AND_HEADERS_VERIFIED_NO_DIAGNOSTICS','headers':headers}
    if not cfg.get('frozen') or cfg.get('frozen_by')!='root':raise ValueError('ROOT_FREEZE_REQUIRED')
    if cfg.get('analysis_script_sha256')!=sha(Path(__file__)):raise ValueError('Code SHA differs from root freeze')
    if cfg['model']!={'beta':BETA,'intercept':INTERCEPT,'baseline_ph':list(BASELINE),'depth_edges_m':list(EDGES)}:raise ValueError('Fixed model changed')
    if out.exists():raise ValueError('Output directory already exists; use a new empty destination')
    raw={};ix={};mapping={}
    for role in ('leg1_ree','leg1_ph','leg2_ree','leg2_ph'):
        raw[role],hh=read(source[role]['path']);ix[role]=tableindex(raw[role])
        mapping[role]=({e:column(hh,e+'_D_CONC_BOTTLE_') for e in ELEMENTS} if role.endswith('ree') else {'ph':column(hh,'PH_TOT_BOTTLE_')})
        mapping[role].update({e+'_flag':column(hh,'Flag_'+e+'_D_CONC_BOTTLE_') for e in ELEMENTS} if role.endswith('ree') else {'ph_flag':column(hh,'Flag_PH_TOT_BOTTLE_')})
    nrm={role:read(source[role]['path'])[0][0][1] for role in ('paas','molar_mass')}
    norm={e:float(nrm['paas'][e])*1e-6/(float(nrm['molar_mass'][e])*1e-3) for e in ELEMENTS}
    if not all(v>0 for v in norm.values()):raise ValueError('Nonpositive normalizer')
    leg1ids=set(ix['leg1_ree'])|set(ix['leg1_ph'])
    leg1stations={ident(row['Station_ID']) for role in ('leg1_ree','leg1_ph') for _,row in raw[role]}
    pairs=[];audit=[];depth=[]
    for leg in ('leg1','leg2'):
        rr,pr=leg+'_ree',leg+'_ph';rm,pm=mapping[rr],mapping[pr]
        for sid in sortid(set(ix[rr])|set(ix[pr])):
            status='not_shared_sample_id';entry={'leg':leg,'sample_id':sid,'status':status,'time_matches_after_iso_normalization':None}
            if sid not in ix[rr] or sid not in ix[pr]:audit.append(entry);continue
            rline,r=ix[rr][sid];pline,p=ix[pr][sid]
            station,event=ident(r['Station_ID']),ident(r['Event_ID'])
            rs,ps=station,ident(p['Station_ID']);ree_event,ph_event=event,ident(p['Event_ID'])
            rd,pd=num(r['Sample_Depth']),num(p['Sample_Depth'])
            flags={e:r[rm[e+'_flag']].strip() for e in ELEMENTS};flags['pH']=p[pm['ph_flag']].strip()
            concentrations={e:num(r[rm[e]]) for e in ELEMENTS};observed=num(p[pm['ph']])
            sameidentity=rs==ps and ree_event==ph_event
            if not sameidentity:status='station_or_event_mismatch'
            elif leg=='leg2' and (sid in leg1ids or station in leg1stations):status='leg2_station_or_sample_seen_in_leg1'
            elif rd is None or pd is None:status='missing_finite_depth'
            elif not (200<=rd<=800 or 200<=pd<=800):status='outside_predeclared_depth_union'
            elif any(v!='2' for v in flags.values()):status='bottle_flag_not_allowed'
            elif observed is None or any(v is None or v<=0 for v in concentrations.values()):status='missing_or_invalid_response'
            else:status='eligible_in_at_least_one_depth_definition'
            eligible=status=='eligible_in_at_least_one_depth_definition';primary=eligible and 200<=pd<=800
            rb,pb=bins(rd),bins(pd)
            rawbase={'leg':leg,'sample_id':sid,'station':station,'event':event,'ree_csv_line':rline,'ph_csv_line':pline,
                     'ree_reported_depth_m':rd,'ph_reported_depth_m':pd,'depth_difference_ree_minus_ph_m':rd-pd if rd is not None and pd is not None else None,
                     'ree_bins':rb,'ph_bins':pb,'bin_membership_differs':rb!=pb,'identity_matches':sameidentity,
                     'ree_time':r['Start_ISO_DateTime_UTC'],'ph_time':p['Start_ISO_DateTime_UTC'],
                     'time_matches_after_iso_normalization':isosame(r['Start_ISO_DateTime_UTC'],p['Start_ISO_DateTime_UTC']),
                     'ree_latitude':num(r['Start_Latitude']),'ree_longitude':num(r['Start_Longitude']),
                     'ph_latitude':num(p['Start_Latitude']),'ph_longitude':num(p['Start_Longitude']),
                     'flags':flags,'status':status,'eligible_union':eligible,'eligible_primary':primary,
                     'ree_source':source[rr]['task_relative_path'],
                     'ree_sha256':source[rr]['sha256'],'ph_source':source[pr]['task_relative_path'],
                     'ph_sha256':source[pr]['sha256']}
            if rd is not None and pd is not None and rd!=pd:depth.append(rawbase)
            audit.append(dict(entry,status=status,ree_csv_line=rline,ph_csv_line=pline,
                              time_matches_after_iso_normalization=rawbase['time_matches_after_iso_normalization']))
            if not eligible:continue
            normalized=[concentrations[e]*1e-15/norm[e] for e in ELEMENTS]
            alpha=alphav(normalized);A=statistics.mean(normalized);shape=[v/A for v in normalized];s=alphav(shape)
            delta=alpha-A*s
            if abs(delta)>1e-20:raise ValueError('alpha=A*s identity failed')
            pairs.append({**rawbase,'normalized':normalized,'A':A,'s':s,'alpha':alpha,'alpha_minus_A_times_s':delta,
                          'predicted_ph':(alpha-INTERCEPT)/BETA,'observed_ph':observed,**concentrations})
    primary=[r for r in pairs if r['eligible_primary']]
    # Independent reconstruction must equal the frozen primary means.
    old=read(source['original_layers']['path'])[0];old={(r['leg'],int(r['bin_index'])):r for _,r in old if r['variant']=='primary'}
    reconstructed=[];mse=[];support=[];support_long=[];geo=[];common=[];commonlayers=[];shape_layers=[];checks=[]
    for leg in ('leg1','leg2'):
        ss=[r for r in primary if r['leg']==leg];ll=layers(ss)
        for layer in ll:
            layer['leg']=leg;reconstructed.append(layer)
            for field in ('observed_ph','predicted_ph','baseline_ph','prediction_error','baseline_error'):
                diff=abs(layer[field]-float(old[(leg,layer['bin_index'])][field]));checks.append(diff)
                if diff>1e-12:raise ValueError('Original primary mean mismatch '+leg+' '+field)
        for predictor,field in [('REE_model','prediction_error'),('fixed_depth_baseline','baseline_error')]:
            mse.append({'leg':leg,'predictor':predictor,**decompose([r[field] for r in ll]),'scope':'post-review descriptive error decomposition; centering is not validation or a correction'})
        stations=sortid({r['station'] for r in ss})
        inter=set.intersection(*(set(r['stations']) for r in ll))
        for station in stations:
            block=[r for r in ss if r['station']==station]
            lat=[r['ree_latitude'] for r in block];lon=[r['ree_longitude'] for r in block]
            if any(v is None for v in lat+lon):raise ValueError('Missing primary geometry')
            bybin=[[r for r in block if i in r['ph_bins']] for i in range(6)]
            row={'leg':leg,'station':station,'n_unique_bottles':len(block),'all_six_layers':station in inter}
            row.update({f'n_{EDGES[i]}_{EDGES[i+1]}':len(bb) for i,bb in enumerate(bybin)})
            row.update(latitude_min=min(lat),latitude_max=max(lat),longitude_min=min(lon),longitude_max=max(lon))
            support.append(row)
            for i,bb in enumerate(bybin):support_long.append({'leg':leg,'station':station,'bin_index':i,'low_m':EDGES[i],'high_m':EDGES[i+1],
                'n_records':len(bb),'sample_ids':[r['sample_id'] for r in bb],'ree_csv_lines':[r['ree_csv_line'] for r in bb],'ph_csv_lines':[r['ph_csv_line'] for r in bb]})
            geo.append({'leg':leg,'station':station,'n_unique_primary_bottles':len(block),'all_six_layers':station in inter,
                'latitude_range':[min(lat),max(lat)],'longitude_range':[min(lon),max(lon)],
                'recorded_ree_coordinates':sorted({(r['ree_latitude'],r['ree_longitude']) for r in block}),
                'recorded_ph_coordinates':sorted({(r['ph_latitude'],r['ph_longitude']) for r in block}),
                'representative_latitude_for_map':statistics.median(lat),'representative_longitude_for_map':statistics.median(lon),
                'bottles':[{'sample_id':r['sample_id'],'event':r['event'],'ree_csv_line':r['ree_csv_line'],'ph_csv_line':r['ph_csv_line'],'ree_latitude':r['ree_latitude'],'ree_longitude':r['ree_longitude'],'ree_time':r['ree_time']} for r in block],
                'coordinate_basis':'REE file start coordinates; all distinct recorded coordinates preserved; pH rounded coordinates also retained'})
        subset=[r for r in ss if r['station'] in inter]
        if not inter:raise ValueError('No all-six-layer common station; report this before any aggregate')
        cc=layers(subset,'station')
        for row in cc:commonlayers.append(dict(row,leg=leg,weighting='station_equal',analysis_status='post_review_exploratory'))
        a=decompose([r['prediction_error'] for r in cc]);b=decompose([r['baseline_error'] for r in cc])
        common.append({'leg':leg,'stations':sortid(inter),'n_stations':len(inter),'n_unique_bottles':len(subset),
            'layer_memberships':sum(r['n_records'] for r in cc),'model':a,'baseline':b,
            'delta_rmse_model_minus_baseline':a['rmse']-b['rmse'],'scope':'Post-review common-station station-equal diagnostic; not a replacement primary analysis, regional validation, or independent study; no intervals'})
        for method in ('record','station_equal'):
            for i in range(6):
                bb=[r for r in ss if i in r['ph_bins']];ww=weights(bb,'record' if method=='record' else 'station')
                rbar=[wmean([r['normalized'][j] for r in bb],ww) for j in range(4)]
                abar=wmean([r['A'] for r in bb],ww);sbar=wmean([r['s'] for r in bb],ww)
                alphabar=wmean([r['alpha'] for r in bb],ww);smean=alphav([v/abar for v in rbar])
                covariance=wmean([(r['A']-abar)*(r['s']-sbar) for r in bb],ww)
                err1=alphabar-abar*smean;err2=alphabar-(abar*sbar+covariance)
                if max(abs(err1),abs(err2))>1e-20:raise ValueError('Weighted shape identity failed')
                shape_layers.append({'leg':leg,'weighting':method,'bin_index':i,'low_m':EDGES[i],'high_m':EDGES[i+1],
                    'n_records':len(bb),'n_stations':len({r['station'] for r in bb}),'sample_ids':[r['sample_id'] for r in bb],
                    'mean_alpha':alphabar,'mean_A':abar,'mean_s':sbar,'s_of_mean_composition':smean,
                    'weighted_covariance_A_s':covariance,'mean_A_times_mean_s':abar*sbar,
                    'A_mean_times_s_of_mean_composition':abar*smean,
                    'identity_residual_composition':err1,'identity_residual_covariance':err2,
                    'interpretation':'Algebraic abundance-shape description only; not attribution of observed cross-leg contrast'})
    traintext=source['training_saved_output']['path'].read_text()
    training_alpha=[float(v) for v in re.findall(r'average REE_slope of\s+\d+\s*-\s*\d+\s*m:\s*([\deE.+-]+)',traintext)]
    if len(training_alpha)!=6:raise ValueError('Expected six saved original training alpha values')
    sensitivity=[{'bin_index':i,'low_m':EDGES[i],'high_m':EDGES[i+1],'original_training_alpha':a,
        'common_concentration_multiplier':1.1,'delta_alpha':.1*a,'delta_predicted_ph':.1*a/BETA,
        'relative_shape_unchanged':True,'scope':'Exact algebraic illustration; no GP15 causal attribution'} for i,a in enumerate(training_alpha)]
    depth_scopes={name:[r for r in depth if predicate(r)] for name,predicate in [
        ('all_shared_identity_matched',lambda r:r['identity_matches']),('eligible_union',lambda r:r['eligible_union']),('eligible_primary',lambda r:r['eligible_primary'])]}
    depthsummary={name:{'n_nonzero_depth_conflicts':len(rows),'max_abs_difference_m':max((abs(r['depth_difference_ree_minus_ph_m']) for r in rows),default=None),
        'max_difference_sample_ids':[r['sample_id'] for r in rows if abs(r['depth_difference_ree_minus_ph_m'])==max((abs(x['depth_difference_ree_minus_ph_m']) for x in rows),default=None)]} for name,rows in depth_scopes.items()}
    summary={'analysis_status':'post_review_exploratory_diagnostics','estimand':'Means on actual eligible samples and fixed layer support; not area-weighted or complete regional profiles',
        'n_eligible_union_by_leg':dict(Counter(r['leg'] for r in pairs)),'n_primary_bottles_by_leg':dict(Counter(r['leg'] for r in primary)),
        'eligible_pair_time_match_counts':{leg:{label:sum(r['leg']==leg and r['time_matches_after_iso_normalization'] is value for r in pairs)
            for label,value in [('true',True),('false',False),('unknown',None)]} for leg in ('leg1','leg2')},
        'primary_pair_time_match_counts':{leg:{label:sum(r['leg']==leg and r['time_matches_after_iso_normalization'] is value for r in primary)
            for label,value in [('true',True),('false',False),('unknown',None)]} for leg in ('leg1','leg2')},
        'eligible_pair_nonmatching_or_unknown_times':[{k:r[k] for k in ('leg','sample_id','station','event','ree_csv_line','ph_csv_line','ree_time','ph_time','time_matches_after_iso_normalization','ree_source','ree_sha256','ph_source','ph_sha256')}
            for r in pairs if r['time_matches_after_iso_normalization'] is not True],
        'maximum_primary_reconstruction_absolute_difference':max(checks),'mse_decomposition':mse,'common_station_diagnostic':common,
        'depth_conflicts':depthsummary,'sample14984':[r for r in depth if r['sample_id']=='14984'],
        'ten_percent_concentration_illustration':{'delta_ph_range':[min(x['delta_predicted_ph'] for x in sensitivity),max(x['delta_predicted_ph'] for x in sensitivity)],'applies_to':'original six saved training alpha values'},
        'maximum_abs_alpha_equals_A_s_residual':max(abs(r['alpha_minus_A_times_s']) for r in pairs),
        'limitations':['All target responses previously exposed; all new diagnostics post-review exploratory','Two-leg shared-cruise/laboratory dependence retained','Centering error is descriptive; not a target-trained bias correction or validation','Common-station diagnostic neither recovers absent stations nor establishes regional representativeness','No causal abundance-versus-shape partition from alpha=A*s']}
    out.mkdir(parents=True)
    # Harmonize audit schema without inventing unavailable identities.
    for row in audit:
        row.setdefault('ree_csv_line',None);row.setdefault('ph_csv_line',None)
    csvout(out/'pair_identity_audit.csv',audit);csvout(out/'depth_conflicts.csv',depth)
    csvout(out/'station_layer_support.csv',support);csvout(out/'station_layer_support_long.csv',support_long)
    dump(out/'station_geometry.json',geo);csvout(out/'reconstructed_primary_layers.csv',reconstructed)
    csvout(out/'mse_decomposition.csv',mse);csvout(out/'common_station_layers.csv',commonlayers);dump(out/'common_station_summary.json',common)
    shape_bottle=[{k:r[k] for k in ('leg','sample_id','station','event','ree_csv_line','ph_csv_line','eligible_primary','normalized','A','s','alpha','alpha_minus_A_times_s')} for r in pairs]
    csvout(out/'abundance_shape_bottles.csv',shape_bottle);csvout(out/'abundance_shape_layers.csv',shape_layers);csvout(out/'training_common_multiplier.csv',sensitivity)
    dump(out/'summary.json',summary)
    manifest={'protocol_sha256':sha(protocol_path),'analysis_script_sha256':sha(Path(__file__)),
        'inputs':cfg['inputs'],'outputs':[{'path':f.name,'sha256':sha(f),'bytes':f.stat().st_size} for f in sorted(out.iterdir())],
        'deterministic':True,'scientific_scope':'Post-review diagnostic only; original frozen results unchanged'}
    dump(out/'run_manifest.json',manifest)
    return {'status':'DIAGNOSTICS_COMPLETE','output':str(out),'n_output_files':len(list(out.iterdir()))}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--protocol',type=Path,required=True);ap.add_argument('--out',type=Path);ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
    if not args.check_only and args.out is None:ap.error('--out required for execution')
    print(json.dumps(run(args.protocol.resolve(),args.out.resolve() if args.out else None,args.check_only),ensure_ascii=False))
