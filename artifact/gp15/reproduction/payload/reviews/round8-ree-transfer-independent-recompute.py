#!/usr/bin/env python3
"""Independent post-freeze audit: original CSV + protocol, never import the analysis.
Outputs only the review JSON beside this script. Uses general Decimal regression,
fsum means and station-multiplicity bootstrap rather than the production functions.
"""
from pathlib import Path
from decimal import Decimal, getcontext
from collections import Counter, defaultdict
import csv, hashlib, json, math, random, datetime, re
getcontext().prec=45
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'ree/round8'
OUT=ROOT/'results/ree_ph_fixed_transfer_v1'
PROTOCOL=WORK/'protocol-frozen-v1.json'
CFG=json.loads(PROTOCOL.read_text())
MANIFEST=WORK/'transfer-source-input.json'
SRC=json.loads(MANIFEST.read_text())['sources']
E=('Sm','Gd','Dy','Er')
D=Decimal

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def records(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
paths={r['role']:(WORK/r['path']).resolve() for r in SRC}
for s in SRC:assert sha(paths[s['role']])==s['sha256'],s['role']
assert sha(ROOT/'plans/ree-ph-fixed-transfer-v1.json')=='6f5defd55abc658c961b7a8396fbe490aa2f38dcda84e8ded1bd9287e4c22820'
assert sha(WORK/'analyze_ree_ph_transfer.py')==CFG['analysis_script_sha256']
assert sha(MANIFEST)==CFG['input_sha256']
base=[float(x) for x in CFG['model']['training_depth_mean_ph']]
beta=D(str(CFG['model']['beta']));intercept=D(str(CFG['model']['intercept']))
paas=records(paths['paas'])[0];mass=records(paths['molar_mass'])[0];atoms=records(paths['atomic_numbers'])[0]
norm={e:D(paas[e])*D('1e-6')/(D(mass[e])*D('1e-3')) for e in E}
xs=[D(atoms[e]) for e in E];mx=sum(xs)/len(xs)

def prediction(conc,factors=None):
    y=[D(str(conc[e]))*D('1e-15')/norm[e]*(D(str(factors[e])) if factors else D(1)) for e in E]
    my=sum(y)/len(y)
    a=sum((x-mx)*(v-my) for x,v in zip(xs,y))/sum((x-mx)**2 for x in xs)
    return float(a),float((a-intercept)/beta)

def canon(x):return format(D(x.strip()).normalize(),'f')
def num(x):
    try:v=float(x);return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None

def cols(rows,kind):
    names=list(rows[0]);out={k:k for k in ('Sample_ID','Station_ID','Event_ID','Sample_Depth')}
    for e in E if kind=='ree' else ('PH_TOT',):
        prefix=e+'_D_CONC_BOTTLE_' if kind=='ree' else e+'_BOTTLE_'
        found=[k for k in names if k.startswith(prefix)];assert len(found)==1
        out[e]=found[0];out[e+'_flag']='Flag_'+found[0];assert out[e+'_flag'] in names
    return out

allraw={};byid={};maps={}
for leg in ('leg1','leg2'):
    for kind in ('ree','ph'):
        key=leg+'_'+kind;rows=records(paths[key]);allraw[key]=rows;maps[key]=cols(rows,kind)
        index={}
        for line,row in enumerate(rows,2):
            sid=canon(row['Sample_ID']);assert sid not in index
            index[sid]=(line,row)
        byid[key]=index
oldids=set(byid['leg1_ree'])|set(byid['leg1_ph'])
oldstations={canon(r['Station_ID']) for k in ('leg1_ree','leg1_ph') for r in allraw[k]}
pairs=[];audit={};spotted=[]
for leg in ('leg1','leg2'):
    rk=leg+'_ree';pk=leg+'_ph';rc=maps[rk];pc=maps[pk]
    for sid in sorted(set(byid[rk])|set(byid[pk])):
        label=(leg,sid)
        if sid not in byid[rk] or sid not in byid[pk]:
            audit[label]='not_shared_sample_id';continue
        rl,r=byid[rk][sid];pl,p=byid[pk][sid]
        st=canon(r['Station_ID']);pst=canon(p['Station_ID']);ev=canon(r['Event_ID']);pev=canon(p['Event_ID'])
        rd=num(r['Sample_Depth']);pd=num(p['Sample_Depth'])
        if (st,ev)!=(pst,pev):status='station_or_event_mismatch'
        elif leg=='leg2' and (sid in oldids or st in oldstations):status='leg2_station_or_sample_seen_in_leg1'
        elif rd is None or pd is None:status='missing_finite_depth'
        elif not(200<=rd<=800 or 200<=pd<=800):status='outside_predeclared_depth_union'
        elif any(r[rc[e+'_flag']].strip()!='2' for e in E) or p[pc['PH_TOT_flag']].strip()!='2':status='bottle_flag_not_allowed'
        else:
            cc={e:num(r[rc[e]]) for e in E};ph=num(p[pc['PH_TOT']])
            if ph is None or any(v is None or v<=0 for v in cc.values()):status='missing_or_invalid_response'
            else:
                status='eligible_in_at_least_one_depth_definition';a,pr=prediction(cc)
                pairs.append(dict(leg=leg,sample_id=sid,station=st,event=ev,ree_line=rl,ph_line=pl,
                                  ree_reported_depth=rd,ph_reported_depth=pd,alpha=a,predicted_ph=pr,observed_ph=ph,**cc))
        audit[label]=status

checks=Counter();bad=[];maxdiff=defaultdict(float)
def test(want,got,label):
    if isinstance(want,dict):
        for key,value in want.items():
            if key not in got:bad.append({'path':label+'/'+key,'missing':True})
            else:test(value,got[key],label+'/'+key)
    elif isinstance(want,list):
        if len(want)!=len(got):bad.append({'path':label,'lengths':[len(want),len(got)]});return
        for i,(w,g) in enumerate(zip(want,got)):test(w,g,label+f'/{i}')
    elif isinstance(want,(int,float)) and not isinstance(want,bool):
        checks['numeric']+=1
        try:
            difference=abs(want-float(got));maxdiff[label.split('/')[0]]=max(maxdiff[label.split('/')[0]],difference)
            tol=1e-19 if label.endswith('/alpha') else 2e-12
            good=math.isclose(want,float(got),rel_tol=2e-12,abs_tol=tol)
        except (ValueError,TypeError):good=False
        if not good:bad.append({'path':label,'independent':want,'stored':got})
    else:
        checks['identity_or_status']+=1
        if want!=got:bad.append({'path':label,'independent':want,'stored':got})

def decode(row):
    ret={}
    for k,v in row.items():
        if v=='':ret[k]=None
        elif v in ('True','False'):ret[k]=(v=='True')
        elif v[:1] in ('[','{'):ret[k]=json.loads(v)
        elif k in ('leg','variant','sample_id','station','event','status','ree_source_sha','ph_source_sha','ph_field'):ret[k]=v
        else:
            try:ret[k]=float(v)
            except ValueError:ret[k]=v
    return ret

storedpairs={(r['leg'],r['sample_id']):decode(r) for r in records(OUT/'sample_predictions.csv')}
assert set(storedpairs)=={(r['leg'],r['sample_id']) for r in pairs}
for r in pairs:test(r,storedpairs[(r['leg'],r['sample_id'])],'samples/'+r['leg']+'/'+r['sample_id'])
stored_audit={(r['leg'],r['sample_id']):r['status'] for r in records(OUT/'pairing_audit.csv')}
assert audit==stored_audit
checks['audit_status_rows']=len(audit)

def avg(v):return math.fsum(v)/len(v)
def layers(rows,variant='primary',factor=None,multiplicity=None):
    # Multiplicities represent station resampling without expanding copied rows.
    dep='ree_reported_depth' if variant=='ree_reported_depth' else 'ph_reported_depth'
    result=[]
    for bi in range(6):
        low=200+100*bi;high=low+100
        pick=[r for r in rows if low<=r[dep]<=high and not(variant=='half_open' and r[dep]==high and bi<5)]
        if multiplicity is not None:pick=[r for r in pick if multiplicity.get(r['station'],0)]
        counts=[multiplicity[r['station']] if multiplicity is not None else 1 for r in pick]
        stations=sorted({r['station'] for r in pick})
        item=dict(bin_index=bi,low_m=low,high_m=high,n_records=sum(counts),n_stations=len(stations),stations=stations,
                  sample_ids=[r['sample_id'] for r in pick],domain_support=len(stations)>=3,observed_ph=None,predicted_ph=None,
                  baseline_ph=base[bi],prediction_error=None,baseline_error=None)
        if pick:
            obs=[r['observed_ph'] for r in pick]
            pred=[prediction(r,factor)[1] if factor else r['predicted_ph'] for r in pick]
            if variant=='station_equal':
                oo=[];pp=[]
                for s in stations:
                    indices=[j for j,r in enumerate(pick) if r['station']==s]
                    oo.append(avg([obs[j] for j in indices]));pp.append(avg([pred[j] for j in indices]))
                om,pm=avg(oo),avg(pp)
            else:
                om=math.fsum(v*w for v,w in zip(obs,counts))/sum(counts)
                pm=math.fsum(v*w for v,w in zip(pred,counts))/sum(counts)
            item.update(observed_ph=om,predicted_ph=pm,prediction_error=pm-om,baseline_error=base[bi]-om)
        result.append(item)
    return result

metricnames=['model_bias','model_mae','model_rmse','baseline_bias','baseline_mae','baseline_rmse','delta_rmse_model_minus_baseline']
def metrics(ll,obs=0.,train=0.):
    use=[r for r in ll if r['observed_ph'] is not None]
    out=dict(n_nonempty_layers=len(use),nonempty_bin_indexes=[r['bin_index'] for r in use],n_supported_layers=sum(r['domain_support'] for r in ll),full_domain_support=all(r['domain_support'] for r in ll),observed_offset=obs,training_offset=train)
    for short,field in [('model','predicted_ph'),('baseline','baseline_ph')]:
        errs=[r[field]+train-r['observed_ph']-obs for r in use]
        out.update({short+'_bias':avg(errs) if errs else None,short+'_mae':avg([abs(e) for e in errs]) if errs else None,short+'_rmse':math.sqrt(avg([e*e for e in errs])) if errs else None})
    out['delta_rmse_model_minus_baseline']=out['model_rmse']-out['baseline_rmse'] if use else None
    return out

storedlayers={(r['leg'],r['variant'],int(r['bin_index'])):decode(r) for r in records(OUT/'layer_summary.csv')}
summary=json.loads((OUT/'aggregate_summary.json').read_text())
agg={(r['leg'],r['variant']):r for r in summary['summaries']}
indlayers={};indagg={}
for leg in ('leg1','leg2'):
    rows=[r for r in pairs if r['leg']==leg]
    for v in ('primary','half_open','ree_reported_depth','station_equal'):
        ll=layers(rows,v);indlayers[leg,v]=ll;mm=metrics(ll);indagg[leg,v]=mm
        for item in ll:test(item,storedlayers[(leg,v,item['bin_index'])],f'layers/{leg}/{v}/{item["bin_index"]}')
        test(mm,agg[leg,v],f'aggregate/{leg}/{v}')

phscenarios=records(OUT/'systematic_scenarios.csv');indsc=[]
for leg in ('leg1','leg2'):
    for o in CFG['systematic_scenarios']['observed_ph_offsets']:
        for t in CFG['systematic_scenarios']['training_reference_offsets']:
            m=metrics(indlayers[leg,'primary'],o,t);m['leg']=leg;indsc.append(m)
for n,m in enumerate(indsc):test(m,decode(phscenarios[n]),f'pHscenarios/{n}')

saved_loso=json.loads((OUT/'leave_one_station_out.json').read_text());loso={}
for leg in ('leg1','leg2'):
    rows=[r for r in pairs if r['leg']==leg];req={i['bin_index'] for i in indlayers[leg,'primary'] if i['n_records']}
    sts=sorted({r['station'] for r in rows if 200<=r['ph_reported_depth']<=800});cases=[]
    for s in sts:
        ll=layers([r for r in rows if r['station']!=s]);present={i['bin_index'] for i in ll if i['n_records']};complete=(present==req)
        cases.append(dict(deleted_station=s,same_layer_support_as_point=complete,missing_bin_indexes=sorted(req-present),layers=ll,aggregate_on_same_layer_support=metrics(ll) if complete else None))
    loso[leg]={'required_bin_indexes':sorted(req),'point_aggregate':metrics(indlayers[leg,'primary']),'cases':cases}
    test(loso[leg],saved_loso[leg],'LOSO/'+leg)

saved_stress=json.loads((OUT/'ree_repeatability_stress.json').read_text());indstress=[]
rsd=CFG['ree_repeatability_stress']['relative_rsd']
# Enumerate binary masks independently; no response-dependent scenario selection.
for mask in range(16):
    signs={e:(1 if mask&(1<<(3-j)) else -1) for j,e in enumerate(E)}
    factors={e:1+signs[e]*rsd[e] for e in E}
    sid='_'.join(e+('+' if signs[e]>0 else '-') for e in E)
    for leg in ('leg1','leg2'):
        ll=layers([r for r in pairs if r['leg']==leg],factor=factors);cc=[]
        for o in CFG['systematic_scenarios']['observed_ph_offsets']:
            for t in CFG['systematic_scenarios']['training_reference_offsets']:
                m=metrics(ll,o,t)
                m['layer_errors']=[dict(bin_index=r['bin_index'],model_error=r['predicted_ph']+t-r['observed_ph']-o if r['observed_ph'] is not None else None,baseline_error=r['baseline_ph']+t-r['observed_ph']-o if r['observed_ph'] is not None else None) for r in ll]
                cc.append(m)
        item=dict(leg=leg,scenario_id=sid,signs_in_reported_rsd_units=signs,multiplicative_factors=factors,layers_before_ph_offset_scenarios=ll,ph_offset_scenarios=cc)
        indstress.append(item)
for i,c in enumerate(indstress):test(c,saved_stress['cases'][i],f'stress/{i}')

def pct(values,p):
    a=sorted(values);u=(len(a)-1)*p;j=math.floor(u);k=math.ceil(u)
    return a[j]*(1-(u-j))+a[k]*(u-j)

saved_boot=json.loads((OUT/'cluster_bootstrap.json').read_text());boot={}
for i,leg in enumerate(('leg1','leg2')):
    rows=[r for r in pairs if r['leg']==leg and 200<=r['ph_reported_depth']<=800]
    sts=sorted({r['station'] for r in rows});rng=random.Random(271828+i);draws=[]
    for _ in range(1000):
        weights=Counter(sts[math.floor(rng.random()*len(sts))] for __ in sts)
        ll=layers(rows,multiplicity=weights)
        if all(x['n_records'] for x in ll):draws.append(metrics(ll))
    valid=len(draws);report=dict(n_stations=len(sts),stations=sts,seed=271828+i,required_bins=list(range(6)),valid_replicates=valid,attempted_replicates=1000,valid_fraction=valid/1000)
    if len(sts)>=6 and valid>=900:
        report['intervals']={f:[pct([d[f] for d in draws],p) for p in (.025,.975)] for f in metricnames}
        report['status']='conditional_station_resampling_interval_available'
    else:report['intervals']=None;report['status']='insufficient_complete_support_replicates'
    boot[leg]=report;test(report,saved_boot[leg],'bootstrap/'+leg)

# Fixed source spot checks: first/last eligible IDs in each leg, actual cell text preserved.
for leg in ('leg1','leg2'):
    pp=[r for r in pairs if r['leg']==leg]
    for row in pp[:2]+pp[-2:]:
        sid=row['sample_id'];rl,rr=byid[leg+'_ree'][sid];pl,pr=byid[leg+'_ph'][sid]
        spotted.append({'leg':leg,'sample_id':sid,'ree_line':rl,'ph_line':pl,'ree_raw':{maps[leg+'_ree'][e]:rr[maps[leg+'_ree'][e]] for e in E},'ph_raw':{maps[leg+'_ph']['PH_TOT']:pr[maps[leg+'_ph']['PH_TOT']]},'independent_prediction':row['predicted_ph']})

support={};stress_ranges={};loso_ranges={}
for leg in ('leg1','leg2'):
    ll=indlayers[leg,'primary'];pused={x for l in ll for x in l['sample_ids']};rows=[r for r in pairs if r['leg']==leg]
    memberships=Counter(x for l in ll for x in l['sample_ids'])
    support[leg]={'eligible_depth_union':len(rows),'primary_unique_bottles':len(pused),'primary_layer_memberships':sum(memberships.values()),'duplicate_boundary_ids':{k:v for k,v in memberships.items() if v>1},'primary_stations':sorted({r['station'] for r in rows if r['sample_id'] in pused}),'layer_records':[x['n_records'] for x in ll],'layer_stations':[x['n_stations'] for x in ll]}
    cc=[s for s in indstress if s['leg']==leg];dd=[m['delta_rmse_model_minus_baseline'] for s in cc for m in s['ph_offset_scenarios']]
    rawdd=[metrics(s['layers_before_ph_offset_scenarios'])['delta_rmse_model_minus_baseline'] for s in cc]
    perr=[m['model_rmse'] for s in cc for m in s['ph_offset_scenarios']]
    ldd=[s['aggregate_on_same_layer_support']['delta_rmse_model_minus_baseline'] for s in loso[leg]['cases'] if s['aggregate_on_same_layer_support']]
    stress_ranges[leg]={'n_corner_ph_cases':len(dd),'delta_rmse_min':min(dd),'delta_rmse_max':max(dd),'positive':sum(v>0 for v in dd),'negative':sum(v<0 for v in dd),'no_ph_shift_16corners_min':min(rawdd),'no_ph_shift_16corners_max':max(rawdd),'model_rmse_min':min(perr),'model_rmse_max':max(perr)}
    loso_ranges[leg]={'n_cases':len(loso[leg]['cases']),'n_same_support':len(ldd),'delta_rmse_min':min(ldd),'delta_rmse_max':max(ldd)}

# Post-run domain annotation, never a new eligibility filter or subgroup score.
notebook=json.loads(paths['author_notebook'].read_text())
texts=[]
for cell in notebook.get('cells',[]):
    for output in cell.get('outputs',[]):
        t=output.get('text',output.get('data',{}).get('text/plain',''))
        texts.append(''.join(t) if isinstance(t,list) else t)
saved='\n'.join(texts)
original_alpha=[float(x) for x in re.findall(r'average REE_slope of\s+\d+\s+-\s+\d+\s+m:\s+([0-9.eE+-]+)',saved)]
original_ph=[float(x) for x in re.findall(r'Average pH of depth \d+-\d+ m:\s+([0-9.eE+-]+)',saved)]
assert len(original_alpha)==6 and original_ph==base
alow,ahigh=min(original_alpha),max(original_alpha)
plow,phigh=min(original_ph),max(original_ph)
domain={'annotation_status':'Post-run source-domain interpretation only; no exclusions, recomputed subgroup score or revised rule',
        'saved_training_alpha':original_alpha,'saved_training_ph':original_ph,'legs':{}}
for leg in ('leg1','leg2'):
    cases=[]
    for layer in indlayers[leg,'primary']:
        low,high=layer['low_m'],layer['high_m']
        rr=[r for r in pairs if r['leg']==leg and low<=r['ph_reported_depth']<=high]
        a=avg([r['alpha'] for r in rr]);y=layer['observed_ph']
        cases.append({'bin_index':layer['bin_index'],'mean_alpha':a,
                      'feature_domain':'below' if a<alow else 'above' if a>ahigh else 'inside',
                      'observed_ph_domain':'below' if y<plow else 'above' if y>phigh else 'inside',
                      'observed_ph':y,'predicted_ph':layer['predicted_ph']})
    domain['legs'][leg]=cases

result={
 'review':'Independent original-CSV recomputation after root freeze; no import/execution of production analysis',
 'recorded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'source_hashes':{s['role']:s['sha256'] for s in SRC},
 'bindings':{'plan':sha(ROOT/'plans/ree-ph-fixed-transfer-v1.json'),'protocol':sha(PROTOCOL),'input':sha(MANIFEST),'analysis':sha(WORK/'analyze_ree_ph_transfer.py'),'review_script':sha(Path(__file__))},
 'original_result_hashes':{p.name:sha(p) for p in OUT.iterdir() if p.is_file()},
 'comparison_counts':dict(checks),'mismatches':bad,'max_absolute_numeric_difference_by_family':dict(maxdiff),
 'audit_counts':dict(Counter(leg+':'+status for (leg,sid),status in audit.items())),
 'source_spotchecks':spotted,'support':support,
 'four_variant_aggregates':[{**v,'leg':leg,'variant':variant} for (leg,variant),v in indagg.items()],
 'primary_layers':{leg:indlayers[leg,'primary'] for leg in ('leg1','leg2')},
 'training_domain_annotation':domain,'ph_scenarios':indsc,'loso_ranges':loso_ranges,'repeatability_scenario_ranges':stress_ranges,'bootstrap':boot,
 'scope':'All 84 eligible bottle predictions, 962 union-ID statuses, 48 layer rows, 8 aggregate cases, 18 pH scenarios, 14 LOSO cases, 32 leg-corner cases with 288 crossed pH metrics and layer errors, and both complete 1000-draw bootstraps independently recomputed. Only original reference/protocol inputs used to calculate; frozen outputs read only for comparisons.'
}
path=ROOT/'reviews/round8-ree-transfer-independent-numeric.json';path.write_text(json.dumps(result,indent=2,allow_nan=False))
print(json.dumps({'numeric':str(path),'sha256':sha(path),'checks':dict(checks),'mismatches':bad[:8],'support':support,'stress':stress_ranges,'loso':loso_ranges},indent=2))
assert not bad, 'Independent recomputation mismatch'
