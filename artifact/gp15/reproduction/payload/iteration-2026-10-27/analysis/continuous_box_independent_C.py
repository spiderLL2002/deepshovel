#!/usr/bin/env python3
"""Independent C check of continuous-box B outputs, from original raw CSVs.

No B module import/execution. Scientific construction uses seven original inputs.
Legacy/B files are comparison targets only. No bootstrap, fitting or new data.
"""
from __future__ import annotations
import csv
from collections import defaultdict
from decimal import Decimal, localcontext, getcontext
from fractions import Fraction as Q
import hashlib
import io
import itertools
import json
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"iteration-2026-10-27"
B=BASE/"analysis/continuous-box-v1"
OUT=BASE/"reviews/cycle02"
TOL=Q("1e-10")
getcontext().prec=90
ORIGINAL={
 "protocol":"ree/round8/protocol-frozen-v1.json",
 "leg1_ree":"ree/round8/raw/GP15-leg1-REE.csv",
 "leg1_ph":"ree/round8/raw/GP15-leg1-pH.csv",
 "leg2_ree":"ree/round8/raw/GP15-leg2-REE.csv",
 "leg2_ph":"ree/round8/raw/GP15-leg2-pH.csv",
 "paas":"ree/round8/raw/Lin2024-paas.csv",
 "mass":"ree/round8/raw/Lin2024-molar_mass_mgmol.csv",
}
TARGETS={
 "old_layers":"results/ree_ph_fixed_transfer_v1/layer_summary.csv",
 "old_stress":"results/ree_ph_fixed_transfer_v1/ree_repeatability_stress.json",
 "B_coeff":"iteration-2026-10-27/analysis/continuous-box-v1/layer_coefficients.csv",
 "B_lower":"iteration-2026-10-27/analysis/continuous-box-v1/pair_residual_lower_bounds.csv",
 "B_envelope":"iteration-2026-10-27/analysis/continuous-box-v1/pair_envelope.csv",
 "B_certificates":"iteration-2026-10-27/analysis/continuous-box-v1/ranking_certificates.json",
 "B_matching":"iteration-2026-10-27/analysis/continuous-box-v1/vertex_matching.csv",
 "B_source_manifest":"iteration-2026-10-27/analysis/continuous-box-v1/source_manifest.json",
 "B_output_manifest":"iteration-2026-10-27/analysis/continuous-box-v1/output_manifest.json",
 "B_plan":"iteration-2026-10-27/analysis/continuous-boxplan-v1.frozen.json",
 "B_script":"iteration-2026-10-27/analysis/continuous_box_v1.py",
}
stats=defaultdict(lambda:dict(n=0,mismatches=0,max_abs_error=Decimal(0)))
failures=[]
details=[]
consumed={}

def digest(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()

def json_load(role):
 path=ROOT/(ORIGINAL|TARGETS)[role]
 consumed[role]=dict(path=str(path.relative_to(ROOT)),sha256=digest(path))
 return json.loads(path.read_text(),parse_float=Decimal)

def csv_load(role):
 path=ROOT/(ORIGINAL|TARGETS)[role]
 consumed[role]=dict(path=str(path.relative_to(ROOT)),sha256=digest(path))
 with path.open(encoding="utf-8-sig",newline="") as f:
  reader=csv.DictReader(f)
  rows=[]
  for n,r in enumerate(reader,1):
   if None in r or any(v is None for v in r.values()): raise ValueError("Ragged source CSV")
   rows.append(dict(r,_record=n,_line=reader.line_num))
 return rows

def dec(x):
 with localcontext() as c:
  c.prec=90
  return Decimal(x.numerator)/Decimal(x.denominator)

def sqrt_dec(x):
 with localcontext() as c:
  c.prec=90
  return (Decimal(x.numerator)/Decimal(x.denominator)).sqrt()

def canon(x):
 return format(Decimal(x).normalize(),"f")

def finite(x):
 try:
  d=Decimal(x)
  return Q(d) if d.is_finite() else None
 except Exception:
  return None

def avg(xs):
 return sum(xs,Q(0))/len(xs)

def norm2(xs):
 return sum((x*x for x in xs),Q(0))/len(xs)

def sign(x):
 return (x>0)-(x<0)

def delta_compare(m,b,q):
 """Exact sign of sqrt(m)-sqrt(b)-q, via rational squaring, not isqrt."""
 if q==0: return sign(m-b)
 if q<0: return -delta_compare(b,m,-q)
 left=m-b-q*q
 if left<0: return -1
 if left==0: return 0 if b==0 else -1
 return sign(left*left-4*q*q*b)

def record(group,key,passed,error=Decimal(0),info=""):
 s=stats[group];s["n"]+=1;s["mismatches"]+=not passed
 s["max_abs_error"]=max(s["max_abs_error"],abs(error))
 if not passed: failures.append(dict(group=group,key=str(key),detail=info,error=str(error)))
 details.append(dict(group=group,key=str(key),passed=bool(passed),absolute_error_decimal90=str(error),detail=info))

def exact(group,key,x,y):
 error=abs(dec(x-y))
 record(group,key,x==y,error,("expected="+str(x)+" observed="+str(y)) if x!=y else "")

def boolean(group,key,ok,info=""):
 record(group,key,bool(ok),Decimal(0),info)

def value_in_interval(kind,target,lo,hi):
 if lo>hi: return False
 if kind=="scalar": return lo<=target<=hi
 if kind=="root":
  return hi>=0 and hi*hi>=target and (lo<=0 or lo*lo<=target)
 m,b=target
 return delta_compare(m,b,lo)>=0 and delta_compare(m,b,hi)<=0

def display(kind,target):
 if kind=="scalar": return dec(target)
 if kind=="root": return sqrt_dec(target)
 with localcontext() as c:
  c.prec=90
  return sqrt_dec(target[0])-sqrt_dec(target[1])

def pair_name(o,t):
 return "o="+format(Decimal(o.numerator)/Decimal(o.denominator),"f")+";t="+format(Decimal(t.numerator)/Decimal(t.denominator),"f")

def corner_name(elements,u):
 return "_".join(e+("+" if x==1 else "-") for e,x in zip(elements,u))

def run():
 if sys.version_info[:2]!=(3,12): raise ValueError("Python3.12 required")
 protocol=json_load("protocol")
 if protocol["frozen"] is not True or protocol["frozen_by"]!="root": raise ValueError("Original protocol not frozen")
 model=protocol["model"]
 elements=model["elements"];z=list(map(Q,model["atomic_numbers"]))
 edges=list(map(Q,model["depth_edges_m"]))
 beta,b=Q(model["beta"]),Q(model["intercept"])
 reference=list(map(Q,model["training_depth_mean_ph"]))
 radii=[Q(protocol["ree_repeatability_stress"]["relative_rsd"][e]) for e in elements]
 offsets=list(itertools.product(map(Q,protocol["systematic_scenarios"]["observed_ph_offsets"]),
                                map(Q,protocol["systematic_scenarios"]["training_reference_offsets"])))
 corners=list(itertools.product((1,-1),repeat=4))
 if elements!=["Sm","Gd","Dy","Er"] or z!=list(map(Q,[62,64,66,68])): raise ValueError("Wrong model feature")
 if protocol["primary"]|{} != protocol["primary"]: raise ValueError("Bad protocol")
 if any(protocol["primary"][k]!=v for k,v in dict(depth_source="ph_reported",bin_rule="inclusive",weighting="record").items()):
  raise ValueError("Primary convention changed")
 paas=csv_load("paas")[0];mass=csv_load("mass")[0]
 # Independent unit route: PAAS ug/g equals mg/kg. Seawater pmol/kg times
 # source mass mg/mol times 1e-12 gives mg/kg; divide like mass concentrations.
 ratio_factor=[Q(mass[e])*Q("1e-12")/Q(paas[e]) for e in elements]
 sx=sum(z);den=4*sum(x*x for x in z)-sx*sx
 def predict(concentrations):
  ratios=[c*f for c,f in zip(concentrations,ratio_factor)]
  slope=(4*sum(x*y for x,y in zip(z,ratios))-sx*sum(ratios))/den
  return (slope-b)/beta,slope,ratios
 raw={};fields={}
 for leg in ("leg1","leg2"):
  for medium in ("ree","ph"):
   role=leg+"_"+medium;rows=csv_load(role);index={}
   for row in rows:
    sid=canon(row["Sample_ID"])
    if sid in index: raise ValueError("Duplicate raw Sample_ID")
    index[sid]=row
   raw[role]=index
  row=next(iter(raw[leg+"_ree"].values()))
  fields[leg]=[next(k for k in row if k.startswith(e+"_D_CONC_BOTTLE_")) for e in elements]
 old_leg1_ids=set(raw["leg1_ree"])|set(raw["leg1_ph"])
 old_leg1_stations={canon(r["Station_ID"]) for kind in ("ree","ph") for r in raw["leg1_"+kind].values()}
 primary={};accepted_union={}
 for leg in ("leg1","leg2"):
  rr,pp=raw[leg+"_ree"],raw[leg+"_ph"]
  phfield=next(k for k in next(iter(pp.values())) if k.startswith("PH_TOT_BOTTLE_"))
  good=[];union=[]
  for sid in sorted(set(rr)&set(pp)):
   r,p=rr[sid],pp[sid]
   identity_ok=(canon(r["Station_ID"])==canon(p["Station_ID"]) and canon(r["Event_ID"])==canon(p["Event_ID"]))
   separation_ok=not(leg=="leg2" and (sid in old_leg1_ids or canon(r["Station_ID"]) in old_leg1_stations))
   rd,pd=finite(r["Sample_Depth"]),finite(p["Sample_Depth"])
   conc=[finite(r[k]) for k in fields[leg]];obs=finite(p[phfield])
   flag_ok=all(r["Flag_"+k] in protocol["eligibility"]["allowed_flag_values"] for k in fields[leg]) and p["Flag_"+phfield] in protocol["eligibility"]["allowed_flag_values"]
   numeric_ok=all(x is not None and x>0 for x in conc) and obs is not None
   depth_ok=rd is not None and pd is not None
   if not(identity_ok and separation_ok and flag_ok and numeric_ok and depth_ok): continue
   lo,hi=map(Q,protocol["eligibility"]["depth_union_for_response_processing_m"])
   if not(lo<=rd<=hi or lo<=pd<=hi): continue
   entry=dict(id=sid,station=canon(r["Station_ID"]),event=canon(r["Event_ID"]),
              depth=pd,ree_depth=rd,concentrations=conc,observed=obs,
              ree_line=r["_line"],ph_line=p["_line"])
   union.append(entry)
   if edges[0]<=pd<=edges[-1]: good.append(entry)
  primary[leg]=good;accepted_union[leg]=union
  boolean("raw_primary_support",leg,len(good)==(24 if leg=="leg1" else 55))
 # Construct coefficients by unit perturbations and independent generic OLS,
 # rather than implementing B's closed-form coefficient expression.
 layers={}
 for leg,rows in primary.items():
  for k in range(6):
   members=[r for r in rows if edges[k]<=r["depth"]<=edges[k+1]]
   cc=[avg([r["concentrations"][j] for r in members]) for j in range(4)]
   pred,alpha,ratios=predict(cc)
   observed=avg([r["observed"] for r in members])
   coeff=[]
   for j in range(4):
    plus=list(cc);minus=list(cc)
    plus[j]*=1+radii[j];minus[j]*=1-radii[j]
    coeff.append((predict(plus)[0]-predict(minus)[0])/2)
   layers[leg,k]=dict(ids={r["id"] for r in members},n=len(members),
                     stations={r["station"] for r in members},concentrations=cc,
                     prediction=pred,alpha=alpha,ratios=ratios,C=coeff,
                     observed=observed,reference=reference[k],error=pred-observed)
 # Build all 6108 old-result targets before examining B matching candidates.
 legacy_layers=[r for r in csv_load("old_layers") if r["variant"]=="primary"]
 old_layer_index={(r["leg"],int(r["bin_index"])):r for r in legacy_layers}
 stress=json_load("old_stress")
 old_corners={(r["leg"],tuple(r["signs_in_reported_rsd_units"][e] for e in elements)):r for r in stress["cases"]}
 pending={}
 def add(stage,leg,corner,pair,k,quantity,kind,candidate,legacy):
  key=(stage,leg,corner,pair,str(k),quantity)
  if key in pending: raise ValueError("Repeated independent comparison key")
  pending[key]=(kind,candidate,Q(legacy))
 for key,L in layers.items():
  leg,k=key;r=old_layer_index[key]
  boolean("original_primary_layer_membership",key,L["ids"]==set(json.loads(r["sample_ids"])) and L["n"]==int(r["n_records"]) and len(L["stations"])==int(r["n_stations"]))
  for field,v in dict(observed_ph=L["observed"],predicted_ph=L["prediction"],baseline_ph=L["reference"],
                      prediction_error=L["error"],baseline_error=L["reference"]-L["observed"]).items():
   add("primary_identity",leg,"0","o=0;t=0",k,field,"scalar",v,r[field])
 corner_predictions={};corner_mse={}
 for leg in ("leg1","leg2"):
  for u in corners:
   old=old_corners[leg,u];name=corner_name(elements,u)
   pvals=[]
   ol={r["bin_index"]:r for r in old["layers_before_ph_offset_scenarios"]}
   for k in range(6):
    L=layers[leg,k]
    direct=predict([c*(1+s*r) for c,s,r in zip(L["concentrations"],u,radii)])[0]
    pvals.append(direct)
    exact("independent_affine_identity",(leg,k,name),direct,L["prediction"]+sum(c*s for c,s in zip(L["C"],u)))
    for field,v in dict(predicted_ph=direct,observed_ph=L["observed"],baseline_ph=L["reference"]).items():
     add("corner_layer",leg,name,"before_pH_offsets",k,field,"scalar",v,ol[k][field])
   corner_predictions[leg,u]=pvals
   old_pairs={(Q(r["observed_offset"]),Q(r["training_offset"])):r for r in old["ph_offset_scenarios"]}
   for o,t in offsets:
    oldpair=old_pairs[o,t];pair=pair_name(o,t)
    me=[v+t-(layers[leg,k]["observed"]+o) for k,v in enumerate(pvals)]
    re=[layers[leg,k]["reference"]+t-(layers[leg,k]["observed"]+o) for k in range(6)]
    mm,bm=norm2(me),norm2(re);corner_mse[leg,o,t,u]=(mm,bm)
    errors={r["bin_index"]:r for r in oldpair["layer_errors"]}
    for k in range(6):
     for field,v in (("model_error",me[k]),("baseline_error",re[k])):
      add("corner_shifted_error",leg,name,pair,k,field,"scalar",v,errors[k][field])
    for field,v in dict(model_bias=avg(me),model_mae=avg([abs(v) for v in me]),baseline_bias=avg(re),baseline_mae=avg([abs(v) for v in re])).items():
     add("corner_aggregate",leg,name,pair,"all6",field,"scalar",v,oldpair[field])
    for field,v in (("model_rmse",mm),("baseline_rmse",bm)):
     add("corner_aggregate",leg,name,pair,"all6",field,"root",v,oldpair[field])
    add("corner_aggregate",leg,name,pair,"all6","delta_rmse_model_minus_baseline","delta",(mm,bm),oldpair["delta_rmse_model_minus_baseline"])
 bm_rows=csv_load("B_matching")
 bmindex={(r["stage"],r["leg"],r["corner"],r["offset_pair"],r["bin_index"],r["quantity"]):r for r in bm_rows}
 boolean("comparison_key_coverage","all6108",len(pending)==6108 and len(bmindex)==6108 and set(pending)==set(bmindex))
 for key,(kind,candidate,legacy) in pending.items():
  lo,hi=legacy-TOL,legacy+TOL
  ok=value_in_interval(kind,candidate,lo,hi)
  err=abs(display(kind,candidate)-dec(legacy))
  record("legacy_"+key[0],key,ok,err)
  br=bmindex[key];blo,bhi=Q(br["candidate_lower_exact"]),Q(br["candidate_upper_exact"])
  enclose=value_in_interval(kind,candidate,blo,bhi)
  bound=max(abs(blo-legacy),abs(bhi-legacy))
  blog_ok=(enclose and Q(br["legacy_decimal_value_exact"])==legacy and
           Q(br["absolute_error_upper_exact"])==bound and Q(br["tolerance_ph_exact"])==TOL and
           (br["passed"]=="True")== (bound<=TOL))
  boolean("B_matching_enclosure_and_legacy_arithmetic",key,blog_ok,
          "" if blog_ok else "Independent raw-derived target or B interval arithmetic differs")
 coeffrows={(r["leg"],int(r["bin_index"])):r for r in csv_load("B_coeff")}
 for key,L in layers.items():
  br=coeffrows[key]
  boolean("B_layer_membership",key,L["ids"]==set(json.loads(br["sample_ids"])) and L["n"]==int(br["n_records"]) and len(L["stations"])==int(br["n_stations"]))
  for e,c,n in zip(elements,L["C"],L["ratios"]):
   exact("B_all48_C",(key,e),c,Q(br["C_"+e+"_exact"]))
   exact("B_all48_mean_R",(key,e),n,Q(br["mean_R_"+e+"_exact"]))
  for field,v in dict(mean_alpha_exact=L["alpha"],predicted_ph_exact=L["prediction"],observed_ph_exact=L["observed"],reference_ph_exact=L["reference"],base_error_exact=L["error"]).items():
   exact("B_layer_scalar_exact",(key,field),v,Q(br[field]))
 lower_rows={(r["leg"],Q(r["o_exact"]),Q(r["t_exact"]),int(r["bin_index"])):r for r in csv_load("B_lower")}
 certs=json_load("B_certificates")["pairs"]
 certindex={(r["leg"],Q(r["o"]["fraction"]),Q(r["t"]["fraction"])):r for r in certs}
 envelopes={(r["leg"],Q(r["o_exact"]),Q(r["t_exact"])):r for r in csv_load("B_envelope")}
 pair_summaries=[]
 for leg in ("leg1","leg2"):
  for o,t in offsets:
   key=(leg,o,t);pair=pair_name(o,t);br=certindex[key];env=envelopes[key]
   residual_corners={u:[corner_predictions[leg,u][k]+t-(layers[leg,k]["observed"]+o) for k in range(6)] for u in corners}
   lower=[min(residual_corners[u][k] for u in corners) for k in range(6)]
   positive=all(v>0 for v in lower)
   common=set(corners)
   for k in range(6):
    common &= {u for u in corners if residual_corners[u][k]==lower[k]}
    exact("B_all108_component_lower",(leg,pair,k),lower[k],Q(lower_rows[leg,o,t,k]["component_lower_exact"]))
   positive_and_common=positive and bool(common)
   maxima=max(corner_mse[leg,o,t,u][0] for u in corners)
   maxnames=sorted(corner_name(elements,u) for u in corners if corner_mse[leg,o,t,u][0]==maxima)
   baselines={corner_mse[leg,o,t,u][1] for u in corners}
   if len(baselines)!=1: raise ValueError("Reference varies across corners")
   baseline=next(iter(baselines))
   minimum=corner_mse[leg,o,t,next(iter(common))][0] if positive_and_common else None
   upper_difference=maxima-baseline
   lower_difference=minimum-baseline if minimum is not None else None
   ordering=("model_better_throughout_box" if upper_difference<0 else
             "reference_better_throughout_box" if lower_difference is not None and lower_difference>0 else
             "strict_uniform_ordering_not_certified")
   needed=[br["all_component_lower_limits_positive"]==positive,
           br["minimum_status"]==("certified_positive_orthant" if positive_and_common else "unproved_by_selected_criterion"),
           sorted(br["maximum_vertices"])==maxnames,br["strict_ranking"]==ordering,
           br["ranking_uses_exact_mse_sign"] is True,
           Q(br["model_mse_upper"]["fraction"])==maxima,
           Q(br["reference_mse"]["fraction"])==baseline,
           Q(br["delta_mse_upper"]["fraction"])==upper_difference,
           all(Q(x["fraction"])==v for x,v in zip(br["component_lower_limits"],lower))]
   if minimum is None:
    needed.extend(br[f] is None for f in ["model_mse_lower","delta_mse_lower","minimum_vertex"])
   else:
    needed.extend([Q(br["model_mse_lower"]["fraction"])==minimum,Q(br["delta_mse_lower"]["fraction"])==lower_difference,
                   br["minimum_vertex"] in {corner_name(elements,u) for u in common}])
   boolean("B_all18_pair_certificates",key,all(needed))
   fields={"model_mse_upper_exact":maxima,"reference_mse_exact":baseline,"delta_mse_upper_exact":upper_difference,
           "minimum_component_margin_exact":min(lower)}
   if minimum is not None: fields.update(model_mse_lower_exact=minimum,delta_mse_lower_exact=lower_difference)
   env_ok=all(Q(env[k])==v for k,v in fields.items()) and env["strict_ranking_certificate"]==ordering
   if minimum is None: env_ok=env_ok and env["model_mse_lower_exact"]=="" and env["delta_mse_lower_exact"]==""
   boolean("B_all18_envelope_exact_fields",key,env_ok)
   pair_summaries.append(dict(leg=leg,offset_pair=pair,positive_criterion=positive,
        common_component_minimum_vertices=[corner_name(elements,u) for u in sorted(common)],
        minimum_status="certified_positive_orthant" if minimum is not None else "unproved_by_selected_criterion",
        minimum_component_lower_exact=str(min(lower)),minimum_component_lower_decimal90=str(dec(min(lower))),
        model_mse_lower_exact=str(minimum) if minimum is not None else None,
        model_mse_upper_exact=str(maxima),reference_mse_exact=str(baseline),
        delta_mse_lower_exact=str(lower_difference) if lower_difference is not None else None,
        delta_mse_upper_exact=str(upper_difference),strict_ranking=ordering))
 # Frozen-target identity checks read manifests, not B computation functions.
 source_manifest=json_load("B_source_manifest");output_manifest=json_load("B_output_manifest");plan=json_load("B_plan")
 for item in output_manifest["outputs"]:
  boolean("B_output_manifest_hash",item["path"],digest(B/item["path"])==item["sha256"])
 for item in plan["inputs"]:
  boolean("B_frozen_input_hash",item["role"],digest(ROOT/item["path"])==item["sha256"])
 boolean("B_frozen_script_hash","script",digest(ROOT/TARGETS["B_script"])==plan["analysis_script_sha256"])
 consumed["B_script"]=dict(path=TARGETS["B_script"],sha256=digest(ROOT/TARGETS["B_script"]),runtime_use="identity only; never imported or executed")
 boolean("B_source_manifest_counts","6108/0",source_manifest["n_matching_records"]==6108 and source_manifest["n_failed_matches"]==0)
 return dict(raw_primary_unique_ids={leg:sorted(r["id"] for r in rows) for leg,rows in primary.items()},
             raw_primary_counts={leg:len(rows) for leg,rows in primary.items()},
             raw_eligible_union_counts={leg:len(rows) for leg,rows in accepted_union.items()},
             layer_memberships={leg:sum(layers[leg,k]["n"] for k in range(6)) for leg in primary},
             primary_source_rows={leg:[dict(sample_id=r["id"],ree_csv_line=r["ree_line"],ph_csv_line=r["ph_line"]) for r in rows] for leg,rows in primary.items()},
             n_C=48,n_component_lower=108,n_pair_certificates=18,n_corners=32,n_corner_offset_cases=288,
             n_legacy_comparisons=len(pending),pair_summaries=pair_summaries)

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 path=OUT/"continuous-box-independent-C.json"
 mdpath=OUT/"continuous-box-independent-C.md"
 csvpath=OUT/"continuous-box-independent-C-comparisons.csv"
 if any(p.exists() for p in (path,mdpath,csvpath)):
  raise FileExistsError("Refuse to replace an existing independent-review artifact")
 result={};exception=None
 try:
  result=run()
 except Exception:
  exception=traceback.format_exc()
  failures.append(dict(group="execution_or_structure",key="exception",detail=exception))
 report=dict(schema="gp15-continuous-box-independent-review/v1",reviewer="C",cycle="cycle02",
   review_kind="Independent implementation/source numerical check within the same cycle; not new scientific validation or another review cycle",
   checker_path=str(Path(__file__).resolve().relative_to(ROOT)),checker_sha256=digest(Path(__file__)),
   construction_inputs=[dict(role=k,**v) for k,v in consumed.items() if k in ORIGINAL],
   comparison_targets=[dict(role=k,**v) for k,v in consumed.items() if k not in ORIGINAL],
   scientific_construction="Original raw CSV joining and flag/finite/primary-depth rules; normalization independently via like mg/kg mass ratios; generic four-point OLS normal equation; C from symmetric single-element perturbations; direct all-element corner predictions; lower residuals from complete corner-coordinate ranges and intersection of minimizing vertices",
   arithmetic="Exact Fraction on source decimal tokens; exact symbolic repeated-squaring comparisons for square-root differences; Decimal90 for error display only. No B import/copy, no numpy, no old sample_predictions in construction.",
   legacy_absolute_tolerance_ph=str(TOL),
   stats={g:dict(n=v["n"],mismatches=v["mismatches"],max_abs_error_decimal90=str(v["max_abs_error"])) for g,v in stats.items()},
   results=result,mismatches=failures,exception=exception,
   passed=not failures,cycle_count_delta=0,
   limits=["Exposed existing data; independent code is not an independent field study.",
           "Exact declared-decimal hypothetical shared four-coordinate box and nine discrete pH pairs only.",
           "Leg1 lower minimum remains unproved by the chosen positive-orthant criterion; a strict upper MSE difference can nevertheless certify better model ranking.",
           "Leg2 minimum certification requires positive component lower limits plus a common minimizing vertex; exact positive minimum-minus-reference MSE certifies worse ranking.",
           "No real measurement-error hard bounds, continuous pH interval, joint coefficient covariance, mechanism or bootstrap reanalysis."])
 stream=io.StringIO(newline="")
 writer=csv.DictWriter(stream,fieldnames=["group","key","passed","absolute_error_decimal90","detail"])
 writer.writeheader();writer.writerows(details)
 csvpath.write_text(stream.getvalue())
 report["comparison_csv"]=dict(path=str(csvpath.relative_to(ROOT)),sha256=digest(csvpath),rows=len(details))
 path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
 leg_counts={}
 for row in result.get("pair_summaries",[]):
  leg_counts.setdefault(row["leg"],defaultdict(int))[row["strict_ranking"]]+=1
 md=["# Independent continuous-box numerical review - C","",
     "Status: "+("PASS" if report["passed"] else "FAILED; diagnostics retained")+". Same cycle02 scientific check, not new validation or another review cycle.","",
     "Construction uses four original raw CSVs, the original PAAS/mass rows and original frozen protocol. "
     "The B module was read for its contract but not imported, executed or used as a construction function. "
     "C coefficients come from symmetric element perturbations through an independent generic OLS route. "
     "Complete corner predictions are evaluated directly from changed concentrations.","",
     "Exact rational arithmetic controls equality and certificate signs. Legacy agreement uses a fixed 1e-10 pH tolerance; "
     "square-root differences are checked with independent rational repeated-squaring inequalities. Decimal90 errors are display values only.","",
     "|Comparison group|Checked|Mismatches|Maximum absolute displayed error|","|---|---|---|---|"]
 for group,values in report["stats"].items():
  md.append("|"+group+"|"+str(values["n"])+"|"+str(values["mismatches"])+"|"+values["max_abs_error_decimal90"]+"|")
 md += ["","Raw primary counts: "+str(result.get("raw_primary_counts"))+
        "; memberships: "+str(result.get("layer_memberships"))+".",
        "Pair ranking counts: "+str({k:dict(v) for k,v in leg_counts.items()})+".","",
        "The maximum of the squared norm of an affine residual vector over a box is attained at a vertex by convexity. "
        "A minimum among vertices alone is insufficient. When all component residual lower limits are positive "
        "and their minimizing-vertex sets intersect, monotonicity in the positive orthant supplies a global minimum. "
        "The checker verifies those conditions independently.","",
        "Leg1: a missing minimum proof does not defeat a strictly negative upper MSE difference; that upper bound "
        "alone can certify the model is better throughout the declared box. Leg2: the positive component criterion "
        "and a strictly positive minimum MSE difference certify the opposite ranking. Actual pair results are in JSON.",
        "", "Exact input/checker hashes, all 18 pair margins, errors/tolerances and any failures are retained in JSON and the comparison CSV. "
        "No B frozen output, manuscript, state or original source was changed. No bootstrap was repeated.",""]
 if exception: md += ["Execution/structure exception:",exception]
 mdpath.write_text("\n".join(md))
 print(json.dumps(dict(passed=report["passed"],raw_primary_counts=result.get("raw_primary_counts"),
        n_legacy=result.get("n_legacy_comparisons"),mismatches=len(failures),
        paths=[str(path),str(mdpath),str(csvpath)]),indent=2))
 return 0 if report["passed"] else 2

if __name__=="__main__":
 sys.exit(main())
