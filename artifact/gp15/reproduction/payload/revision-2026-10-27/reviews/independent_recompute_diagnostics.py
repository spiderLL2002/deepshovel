"""Independent raw-CSV check of reviewer diagnostics; no analysis imports.

Uses Decimal arithmetic for normalization/regression and a separate construction
of grouped estimates. Writes only this review receipt, never scientific outputs.
"""
from pathlib import Path
import csv, json, hashlib, math, re, statistics
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, getcontext
getcontext().prec=40
REV=Path(__file__).resolve().parents[1]
TASK=REV.parent
OUT=REV/'analysis/results-v2'
D=Decimal
ELEMENTS=('Sm','Gd','Dy','Er')
Z=tuple(map(D,('62','64','66','68')))
EDGES=(200,300,400,500,600,700,800)
numeric_checks=0
identity_checks=0
max_abs_difference=0.0
mismatches=[]

def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def csvrows(f):
    with f.open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream)
        records=[]
        for row in reader:
            records.append((reader.line_num,row))
        return records
def sid(x):
    try:return format(D(x).normalize(),'f')
    except InvalidOperation:return x.strip()
def finite(x):
    try:
        v=D(x)
        return v if v.is_finite() else None
    except InvalidOperation:return None
def nsort(xs):return sorted(xs,key=D)
def fone(row,prefix):
    ff=[x for x in row if x.startswith(prefix)]
    assert len(ff)==1,(prefix,ff)
    return ff[0]
def check(label,want,got):
    global numeric_checks,identity_checks,max_abs_difference
    if isinstance(want,(int,float,Decimal)) and not isinstance(want,bool):
        numeric_checks+=1
        try:
            want,got=float(want),float(got)
            diff=abs(want-got);max_abs_difference=max(max_abs_difference,diff)
            tol=1e-20 if 0<abs(want)<1e-5 else 1e-12
            if abs(want)==0:tol=1e-14
            if not math.isclose(want,got,rel_tol=1e-12,abs_tol=tol):mismatches.append([label,want,got,diff])
        except Exception as e:mismatches.append([label,str(want),str(got),str(e)])
    elif isinstance(want,bool):
        identity_checks+=1
        actual=got if isinstance(got,bool) else {'True':True,'False':False,'true':True,'false':False}.get(got,got)
        if actual!=want:mismatches.append([label,want,actual])
    elif isinstance(want,list):
        if isinstance(got,str):got=json.loads(got)
        check(label+'.length',len(want),len(got))
        for i,(a,b) in enumerate(zip(want,got)):check(f'{label}[{i}]',a,b)
    elif isinstance(want,dict):
        if isinstance(got,str):got=json.loads(got)
        for k,v in want.items():
            if k not in got:mismatches.append([label,'missing key',k])
            else:check(label+'.'+k,v,got[k])
    else:
        identity_checks+=1
        if got is None:actual=None
        else:actual=str(got)
        if want!=actual:mismatches.append([label,want,actual])
def keyed_csv(name,keys):
    rows=[r for _,r in csvrows(OUT/name)]
    return {tuple(r[k] for k in keys):r for r in rows}
def mean(xs):return sum(xs,D(0))/D(len(xs))
def ols(values):
    zm=mean(Z)
    return sum((z-zm)*v for z,v in zip(Z,values))/sum((z-zm)**2 for z in Z)
def ibins(depth):return [] if depth is None else [i for i in range(6) if D(EDGES[i])<=depth<=D(EDGES[i+1])]
def same_time(a,b):
    try:return datetime.fromisoformat(a.replace('Z','+00:00'))==datetime.fromisoformat(b.replace('Z','+00:00'))
    except ValueError:return None

protocol=json.loads((REV/'analysis/protocol-frozen-v2.json').read_text())
inputs={x['role']:TASK/x['path'] for x in protocol['inputs']}
for inp in protocol['inputs']:assert sha(TASK/inp['path'])==inp['sha256'],inp['path']
author=inputs['author_model_code'].read_text()
beta_txt,intercept_txt=re.search(r'beta_coeff\s*=\s*\[\s*([^,]+),\s*([^\]]+)\]',author).groups()
beta,intercept=D(beta_txt.strip()),D(intercept_txt.strip())
saved=inputs['training_saved_output'].read_text()
baseline=[D(x) for x in re.findall(r'Average pH of depth \d+-\d+ m:\s*([\d.eE+\-]+)',saved)]
training=[D(x) for x in re.findall(r'average REE_slope of\s+\d+\s*-\s*\d+\s*m:\s*([\d.eE+\-]+)',saved)]
assert len(baseline)==len(training)==6
paas=csvrows(inputs['paas'])[0][1];mm=csvrows(inputs['molar_mass'])[0][1]
standard={e:D(paas[e])*D('0.000001')/(D(mm[e])*D('0.001')) for e in ELEMENTS}
raw={role:csvrows(inputs[role]) for role in ('leg1_ree','leg1_ph','leg2_ree','leg2_ph')}
indexed={role:{sid(row['Sample_ID']):(line,row) for line,row in rows} for role,rows in raw.items()}
for role in raw:assert len(raw[role])==len(indexed[role]),role
old_ids=set(indexed['leg1_ree'])|set(indexed['leg1_ph'])
old_stations={sid(row['Station_ID']) for role in ('leg1_ree','leg1_ph') for _,row in raw[role]}
pairs=[];conflicts=[];audit={}
for leg in ('leg1','leg2'):
    reetable,phtable=indexed[leg+'_ree'],indexed[leg+'_ph']
    for sample in nsort(set(reetable)|set(phtable)):
        rowaudit={'leg':leg,'sample_id':sample,'status':'not_shared_sample_id','ree_csv_line':None,'ph_csv_line':None,'time_matches_after_iso_normalization':None}
        if sample not in reetable or sample not in phtable:
            audit[(leg,sample)]=rowaudit;continue
        rline,rr=reetable[sample];pline,pp=phtable[sample]
        station,event=sid(rr['Station_ID']),sid(rr['Event_ID'])
        rd,pd=finite(rr['Sample_Depth']),finite(pp['Sample_Depth'])
        rfields={e:fone(rr,e+'_D_CONC_BOTTLE_') for e in ELEMENTS};pfield=fone(pp,'PH_TOT_BOTTLE_')
        flags={e:rr['Flag_'+rfields[e]].strip() for e in ELEMENTS};flags['pH']=pp['Flag_'+pfield].strip()
        vals={e:finite(rr[rfields[e]]) for e in ELEMENTS};observed=finite(pp[pfield])
        identity=(station==sid(pp['Station_ID']) and event==sid(pp['Event_ID']))
        if not identity:status='station_or_event_mismatch'
        elif leg=='leg2' and (sample in old_ids or station in old_stations):status='leg2_station_or_sample_seen_in_leg1'
        elif rd is None or pd is None:status='missing_finite_depth'
        elif not (D(200)<=rd<=D(800) or D(200)<=pd<=D(800)):status='outside_predeclared_depth_union'
        elif set(flags.values())!={'2'}:status='bottle_flag_not_allowed'
        elif observed is None or any(v is None or v<=0 for v in vals.values()):status='missing_or_invalid_response'
        else:status='eligible_in_at_least_one_depth_definition'
        eligible=status=='eligible_in_at_least_one_depth_definition'
        primary=eligible and D(200)<=pd<=D(800)
        time_match=same_time(rr['Start_ISO_DateTime_UTC'],pp['Start_ISO_DateTime_UTC'])
        rowaudit.update(status=status,ree_csv_line=rline,ph_csv_line=pline,time_matches_after_iso_normalization=time_match)
        audit[(leg,sample)]=rowaudit
        meta={'leg':leg,'sample_id':sample,'station':station,'event':event,'ree_csv_line':rline,'ph_csv_line':pline,
              'ree_reported_depth_m':rd,'ph_reported_depth_m':pd,'depth_difference_ree_minus_ph_m':rd-pd if rd is not None and pd is not None else None,
              'ree_bins':ibins(rd),'ph_bins':ibins(pd),'bin_membership_differs':ibins(rd)!=ibins(pd),'identity_matches':identity,
              'ree_time':rr['Start_ISO_DateTime_UTC'],'ph_time':pp['Start_ISO_DateTime_UTC'],'time_matches_after_iso_normalization':time_match,
              'ree_latitude':finite(rr['Start_Latitude']),'ree_longitude':finite(rr['Start_Longitude']),
              'ph_latitude':finite(pp['Start_Latitude']),'ph_longitude':finite(pp['Start_Longitude']),
              'flags':flags,'status':status,'eligible_union':eligible,'eligible_primary':primary,
              'ree_source':str(inputs[leg+'_ree'].relative_to(TASK)),'ph_source':str(inputs[leg+'_ph'].relative_to(TASK)),
              'ree_sha256':sha(inputs[leg+'_ree']),'ph_sha256':sha(inputs[leg+'_ph'])}
        if rd is not None and pd is not None and rd!=pd:conflicts.append(meta)
        if eligible:
            normalized=[vals[e]*D('1e-15')/standard[e] for e in ELEMENTS]
            amp=mean(normalized);shape=ols([v/amp for v in normalized]);alpha=ols(normalized)
            pairs.append(dict(meta,R=normalized,A=amp,s=shape,alpha=alpha,observed=observed,predicted=(alpha-intercept)/beta))
summary=json.loads((OUT/'summary.json').read_text())
audit_actual=keyed_csv('pair_identity_audit.csv',('leg','sample_id'))
check('pair audit rowcount',len(audit),len(audit_actual))
for key,row in audit.items():
    got=audit_actual[key]
    for field,val in row.items():
        if val is None:
            identity_checks+=1
            if got[field] not in ('',None):mismatches.append(['audit.'+str(key)+'.'+field,None,got[field]])
        else:check('audit.'+str(key)+'.'+field,val,got[field])
depth_actual=keyed_csv('depth_conflicts.csv',('leg','sample_id'))
check('depth conflict rows',len(conflicts),len(depth_actual))
for row in conflicts:check('depth.'+row['leg']+'.'+row['sample_id'],row,depth_actual[(row['leg'],row['sample_id'])])
for scope,predicate in [('all_shared_identity_matched',lambda r:r['identity_matches']),('eligible_union',lambda r:r['eligible_union']),('eligible_primary',lambda r:r['eligible_primary'])]:
    subset=[r for r in conflicts if predicate(r)]
    largest=max(abs(r['depth_difference_ree_minus_ph_m']) for r in subset)
    check('depth scope '+scope,{'n_nonzero_depth_conflicts':len(subset),'max_abs_difference_m':largest,'max_difference_sample_ids':[r['sample_id'] for r in subset if abs(r['depth_difference_ree_minus_ph_m'])==largest]},summary['depth_conflicts'][scope])
check('sample14984',next(r for r in conflicts if r['sample_id']=='14984'),summary['sample14984'][0])

def layer_vector(records,layer,station_equal=False):
    rr=[r for r in records if layer in r['ph_bins']]
    bystation={st:[r for r in rr if r['station']==st] for st in nsort({r['station'] for r in rr})}
    if station_equal:
        weights=[D(1)/(D(len(bystation))*D(len(bystation[r['station']]))) for r in rr]
    else:weights=[D(1)/D(len(rr))]*len(rr)
    wm=lambda key:sum(r[key]*w for r,w in zip(rr,weights))
    observed,predicted=wm('observed'),wm('predicted')
    result={'bin_index':layer,'low_m':EDGES[layer],'high_m':EDGES[layer+1],'n_records':len(rr),'n_stations':len(bystation),
            'stations':list(bystation),'sample_ids':[r['sample_id'] for r in rr],
            'observed_ph':observed,'predicted_ph':predicted,'baseline_ph':baseline[layer],
            'prediction_error':predicted-observed,'baseline_error':baseline[layer]-observed}
    return result,rr,weights
def error_description(errors):
    mu=mean(errors);sq=mean([x*x for x in errors]);center=mean([(x-mu)**2 for x in errors])
    assert abs(sq-mu*mu-center)<D('1e-35')
    return {'n_layers':6,'mean_error':mu,'mse':sq,'rmse':sq.sqrt(),'squared_mean_error':mu*mu,
            'centered_mse':center,'centered_rmse':center.sqrt(),'squared_mean_error_fraction_of_mse':mu*mu/sq,'identity_residual':0}

support_actual=keyed_csv('station_layer_support.csv',('leg','station'))
support_long_actual=keyed_csv('station_layer_support_long.csv',('leg','station','bin_index'))
geometry_actual={(r['leg'],r['station']):r for r in json.loads((OUT/'station_geometry.json').read_text())}
reconstructed=keyed_csv('reconstructed_primary_layers.csv',('leg','bin_index'))
common_layers_actual=keyed_csv('common_station_layers.csv',('leg','bin_index'))
common_actual={r['leg']:r for r in json.loads((OUT/'common_station_summary.json').read_text())}
mse_actual=keyed_csv('mse_decomposition.csv',('leg','predictor'))
shape_layer_actual=keyed_csv('abundance_shape_layers.csv',('leg','weighting','bin_index'))
shape_bottle_actual=keyed_csv('abundance_shape_bottles.csv',('leg','sample_id'))
old_primary={(r['leg'],r['bin_index']):r for _,r in csvrows(inputs['original_layers']) if r['variant']=='primary'}
old_samples={(r['leg'],r['sample_id']):r for _,r in csvrows(inputs['original_samples'])}
primary=[r for r in pairs if r['eligible_primary']]
science_values={}
layer_geography=[]
for leg in ('leg1','leg2'):
    union=[r for r in pairs if r['leg']==leg];pr=[r for r in primary if r['leg']==leg]
    check('unioncount.'+leg,len(union),summary['n_eligible_union_by_leg'][leg]);check('primarycount.'+leg,len(pr),summary['n_primary_bottles_by_leg'][leg])
    for name,records in [('eligible_pair_time_match_counts',union),('primary_pair_time_match_counts',pr)]:
        counts={label:sum(r['time_matches_after_iso_normalization'] is val for r in records) for label,val in [('true',True),('false',False),('unknown',None)]}
        check(name+'.'+leg,counts,summary[name][leg])
    all_layers=[layer_vector(pr,k)[0] for k in range(6)]
    intersection=set(all_layers[0]['stations'])
    for lay in all_layers:
        intersection&=set(lay['stations'])
        check('primary layer.'+leg+str(lay['bin_index']),dict(lay,leg=leg),reconstructed[(leg,str(lay['bin_index']))])
        for field in ('n_records','n_stations','stations','sample_ids','observed_ph','predicted_ph','baseline_ph','prediction_error','baseline_error'):
            old_value=old_primary[(leg,str(lay['bin_index']))][field]
            if field=='stations':old_value=nsort(json.loads(old_value))
            check('original frozen primary.'+leg+str(lay['bin_index'])+'.'+field,lay[field],old_value)
        selected=[r for r in pr if lay['bin_index'] in r['ph_bins']]
        layer_geography.append({'leg':leg,'bin_index':lay['bin_index'],'low_m':lay['low_m'],'high_m':lay['high_m'],
                                'stations':lay['stations'],'n_unique_bottles':len(selected),
                                'ree_latitude_min':float(min(r['ree_latitude'] for r in selected)),
                                'ree_latitude_max':float(max(r['ree_latitude'] for r in selected)),
                                'ree_longitude_min':float(min(r['ree_longitude'] for r in selected)),
                                'ree_longitude_max':float(max(r['ree_longitude'] for r in selected))})
    for st in nsort({r['station'] for r in pr}):
        records=[r for r in pr if r['station']==st]
        lat=[r['ree_latitude'] for r in records];lon=[r['ree_longitude'] for r in records]
        counts=[sum(k in r['ph_bins'] for r in records) for k in range(6)]
        want={'leg':leg,'station':st,'n_unique_bottles':len(records),'all_six_layers':st in intersection,
              'latitude_min':min(lat),'latitude_max':max(lat),'longitude_min':min(lon),'longitude_max':max(lon)}
        want.update({f'n_{EDGES[k]}_{EDGES[k+1]}':counts[k] for k in range(6)})
        check('stationmatrix.'+leg+'.'+st,want,support_actual[(leg,st)])
        for k in range(6):
            occupied=[r for r in records if k in r['ph_bins']]
            check('stationlong.'+leg+'.'+st+str(k),{'leg':leg,'station':st,'bin_index':k,'low_m':EDGES[k],'high_m':EDGES[k+1],'n_records':len(occupied),'sample_ids':[r['sample_id'] for r in occupied],'ree_csv_lines':[r['ree_csv_line'] for r in occupied],'ph_csv_lines':[r['ph_csv_line'] for r in occupied]},support_long_actual[(leg,st,str(k))])
        geom={'leg':leg,'station':st,'n_unique_primary_bottles':len(records),'all_six_layers':st in intersection,
              'latitude_range':[min(lat),max(lat)],'longitude_range':[min(lon),max(lon)],
              'recorded_ree_coordinates':[list(x) for x in sorted({(r['ree_latitude'],r['ree_longitude']) for r in records})],
              'recorded_ph_coordinates':[list(x) for x in sorted({(r['ph_latitude'],r['ph_longitude']) for r in records})],
              'representative_latitude_for_map':statistics.median(lat),'representative_longitude_for_map':statistics.median(lon),
              'bottles':[{k:r[k] for k in ('sample_id','event','ree_csv_line','ph_csv_line','ree_latitude','ree_longitude','ree_time')} for r in records]}
        check('geometry.'+leg+'.'+st,geom,geometry_actual[(leg,st)])
    for predictor,field in [('REE_model','prediction_error'),('fixed_depth_baseline','baseline_error')]:
        desc=error_description([lay[field] for lay in all_layers]);check('MSE.'+leg+'.'+predictor,desc,mse_actual[(leg,predictor)])
        check('summary duplicate MSE.'+leg+'.'+predictor,desc,next(r for r in summary['mse_decomposition'] if r['leg']==leg and r['predictor']==predictor))
        science_values[leg+'_'+predictor]={k:float(v) for k,v in desc.items()}
    common_records=[r for r in pr if r['station'] in intersection]
    common_layers=[layer_vector(common_records,k,True)[0] for k in range(6)]
    for lay in common_layers:check('commonlayer.'+leg+str(lay['bin_index']),dict(lay,leg=leg),common_layers_actual[(leg,str(lay['bin_index']))])
    model=error_description([r['prediction_error'] for r in common_layers]);base=error_description([r['baseline_error'] for r in common_layers]);delta=model['rmse']-base['rmse']
    cwant={'leg':leg,'stations':nsort(intersection),'n_stations':len(intersection),'n_unique_bottles':len(common_records),'layer_memberships':sum(r['n_records'] for r in common_layers),'model':model,'baseline':base,'delta_rmse_model_minus_baseline':delta}
    check('common_summary.'+leg,cwant,common_actual[leg]);science_values[leg+'_common_station_delta_rmse']=float(delta);science_values[leg+'_common_stations']=nsort(intersection)
    check('summary duplicate common.'+leg,cwant,next(r for r in summary['common_station_diagnostic'] if r['leg']==leg))
    for method,eq in [('record',False),('station_equal',True)]:
        for k in range(6):
            lay,rows,ww=layer_vector(pr,k,eq)
            av=lambda key:sum(r[key]*w for r,w in zip(rows,ww))
            alpha,A,s=av('alpha'),av('A'),av('s')
            composition=[sum(r['R'][j]*w for r,w in zip(rows,ww)) for j in range(4)]
            shape_composition=ols([x/A for x in composition]);cov=sum((r['A']-A)*(r['s']-s)*w for r,w in zip(rows,ww))
            assert abs(alpha-A*shape_composition)<D('1e-35') and abs(alpha-A*s-cov)<D('1e-35')
            want={'leg':leg,'weighting':method,'bin_index':k,'low_m':EDGES[k],'high_m':EDGES[k+1],'n_records':len(rows),'n_stations':lay['n_stations'],'sample_ids':lay['sample_ids'],
                  'mean_alpha':alpha,'mean_A':A,'mean_s':s,'s_of_mean_composition':shape_composition,'weighted_covariance_A_s':cov,
                  'mean_A_times_mean_s':A*s,'A_mean_times_s_of_mean_composition':A*shape_composition,
                  'identity_residual_composition':0,'identity_residual_covariance':0}
            check('shape_layer.'+leg+'.'+method+str(k),want,shape_layer_actual[(leg,method,str(k))])
for r in pairs:
    assert abs(r['alpha']-r['A']*r['s'])<D('1e-35')
    want={k:r[k] for k in ('leg','sample_id','station','event','ree_csv_line','ph_csv_line','eligible_primary','A','s','alpha')}
    want.update(normalized=r['R'],alpha_minus_A_times_s=0)
    check('shape_bottle.'+r['leg']+'.'+r['sample_id'],want,shape_bottle_actual[(r['leg'],r['sample_id'])])
    check('original frozen sample.'+r['leg']+'.'+r['sample_id'],{'alpha':r['alpha'],'predicted_ph':r['predicted'],'observed_ph':r['observed'],'station':r['station'],'event':r['event'],'ree_line':r['ree_csv_line'],'ph_line':r['ph_csv_line']},old_samples[(r['leg'],r['sample_id'])])
multiplier_actual=keyed_csv('training_common_multiplier.csv',('bin_index',))
for k,a in enumerate(training):
    check('training10percent.'+str(k),{'bin_index':k,'low_m':EDGES[k],'high_m':EDGES[k+1],'original_training_alpha':a,'common_concentration_multiplier':D('1.1'),'delta_alpha':a/D(10),'delta_predicted_ph':a/(D(10)*beta),'relative_shape_unchanged':True},multiplier_actual[(str(k),)])
check('training10range',[min(training)/(D(10)*beta),max(training)/(D(10)*beta)][::-1],summary['ten_percent_concentration_illustration']['delta_ph_range'])
manifest=json.loads((OUT/'run_manifest.json').read_text())
for item in manifest['outputs']:check('output SHA '+item['path'],sha(OUT/item['path']),item['sha256'])
check('protocolSHA',sha(REV/'analysis/protocol-frozen-v2.json'),manifest['protocol_sha256'])
check('scriptSHA',sha(REV/'analysis/reviewer_diagnostics_v2.py'),manifest['analysis_script_sha256'])
receipt={'at_utc':datetime.now(timezone.utc).isoformat(),'reviewer':'revision_design_review independent implementation','method':'Original four CSVs and author normalizers;40-digit Decimal OLS, explicit station/layer grouping. No scientific analysis implementation imported.','source_inputs':protocol['inputs'],'independent_code_sha256':sha(Path(__file__)),'numeric_comparisons':numeric_checks,'identity_and_status_comparisons':identity_checks,'mismatch_count':len(mismatches),'mismatches':mismatches,'maximum_absolute_numeric_difference':max_abs_difference,'tolerances':'pH/counts/coordinates1e-12absolute+1e-12relative; nonzero smaller-than1e-5quantities1e-20absolute+1e-12relative; zeroidentityresiduals1e-14absolute','scientific_values':science_values,'sampled_layer_geography':layer_geography,'scientific_scope':'Post-review descriptive diagnostics verified, not independent environmental validation or expert certification.','checked_output_files':[f.name for f in sorted(OUT.iterdir())],'status':'PASS' if not mismatches else 'FAIL'}
(REV/'reviews/independent-diagnostics-numeric.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:receipt[k] for k in ('status','numeric_comparisons','identity_and_status_comparisons','mismatch_count','maximum_absolute_numeric_difference')}))
if mismatches:raise SystemExit(1)
