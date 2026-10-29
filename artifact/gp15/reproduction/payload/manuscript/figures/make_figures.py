#!/usr/bin/env python3
"""Publication figures from immutable R8 outputs; no fitting or sample selection.
Run from repo root: .venv/bin/python -B tasks/2026-10-25-deepshovel/manuscript/figures/make_figures.py
"""
from pathlib import Path
import csv, hashlib, json, re, sys, statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

HERE=Path(__file__).resolve().parent
TASK=HERE.parents[1]
RUN=TASK/'results/ree_ph_fixed_transfer_v1'
WORK=TASK/'ree/round8'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def readcsv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def writecsv(name,rows):
    with (HERE/name).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

expected={
 'plans/ree-ph-fixed-transfer-v1.json':'6f5defd55abc658c961b7a8396fbe490aa2f38dcda84e8ded1bd9287e4c22820',
 'ree/round8/protocol-frozen-v1.json':'08483ad527d609ab123e0a8949af23e47f9bf921b5e1dc48b5721706c99acc79',
 'ree/round8/transfer-source-input.json':'9fd91d413e03e069afe0fc65d4b622449308a38c81d3d458285e42a1514e2455',
 'results/ree_ph_fixed_transfer_v1/layer_summary.csv':'52a3a879451f88c9dc3746763de6f383d6323019b7bef99f91d7c803a7b01321',
 'results/ree_ph_fixed_transfer_v1/aggregate_summary.json':'13d6831201d631d6ce39ef99ba5ef35be057a20f58795214956b02157a9b1b18',
 'results/ree_ph_fixed_transfer_v1/leave_one_station_out.json':'2ca019a6363c308bfee33509315375cb760ff959c04ba1e43ecb6ffc6caeaeff',
 'results/ree_ph_fixed_transfer_v1/sample_predictions.csv':'75e79395b1db84626911efccefc922005dddc0c896fe86621dcdd9fb5fab963e',
 'ree/round8/raw/Lin2024-REE_pH_proxy.ipynb':'0c6b1aa80b7ac9a527ea8f835b19adb4a675a7d53402d13f903daaaa8c79fea9'
}
for p,s in expected.items():assert sha(TASK/p)==s,p
cfg=json.loads((WORK/'protocol-frozen-v1.json').read_text())
sourceinput=json.loads((WORK/'transfer-source-input.json').read_text())
for item in sourceinput['sources']:assert sha(WORK/item['path'])==item['sha256'],item['role']
rows=readcsv(RUN/'layer_summary.csv')
primary={leg:sorted([r for r in rows if r['leg']==leg and r['variant']=='primary'],key=lambda r:int(r['bin_index'])) for leg in ('leg1','leg2')}
samples={(r['leg'],r['sample_id']):r for r in readcsv(RUN/'sample_predictions.csv')}
summary=json.loads((RUN/'aggregate_summary.json').read_text())['summaries']
loso=json.loads((RUN/'leave_one_station_out.json').read_text())
nb=json.loads((WORK/'raw/Lin2024-REE_pH_proxy.ipynb').read_text());texts=[]
for cell in nb['cells']:
    for out in cell.get('outputs',[]):
        t=out.get('text',out.get('data',{}).get('text/plain',''))
        texts.append(''.join(t) if isinstance(t,list) else t)
t='\n'.join(texts)
trainx=[float(x) for x in re.findall(r'average REE_slope of\s+\d+\s+-\s+\d+\s+m:\s+([0-9.eE+-]+)',t)]
trainy=[float(x) for x in re.findall(r'Average pH of depth \d+-\d+ m:\s+([0-9.eE+-]+)',t)]
assert len(trainx)==len(trainy)==6 and trainy==cfg['model']['training_depth_mean_ph']
beta=cfg['model']['beta'];intercept=cfg['model']['intercept']

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':10,'axes.titlesize':11,
 'axes.titleweight':'bold','axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.8,
 'xtick.direction':'out','ytick.direction':'out','xtick.major.size':3.5,'ytick.major.size':3.5,
 'legend.fontsize':8.5,'legend.frameon':False,'svg.fonttype':'none','svg.hashsalt':'gp15-fixed-transfer-manuscript-v1',
 'figure.facecolor':'white','savefig.facecolor':'white','lines.linewidth':1.5})
C1='#007f78';C2='#c75b18';MODEL='#b34635';OBS='#233f55';BASE='#777777';TRAIN='#202020'
outputs=[]
def save(fig,name):
    for ext in ('png','svg'):
        p=HERE/(name+'.'+ext)
        fig.savefig(p,dpi=450 if ext=='png' else None,bbox_inches='tight',pad_inches=.06,metadata={'Date':None} if ext=='svg' else {'Software':'Matplotlib; immutable R8 outputs'})
        outputs.append(p)
    plt.close(fig)

# Fig1: identical axes, no estimated confidence intervals or line interpolation inference.
fig,axs=plt.subplots(1,2,figsize=(7.1,4.25),sharex=True,sharey=True)
fig.subplots_adjust(left=.11,right=.98,bottom=.16,top=.78,wspace=.14)
legend=[];data1=[]
for j,(leg,ax) in enumerate(zip(('leg1','leg2'),axs)):
    ll=primary[leg];depth=[(float(r['low_m'])+float(r['high_m']))/2 for r in ll]
    ids={sid for r in ll for sid in json.loads(r['sample_ids'])};stations={s for r in ll for s in json.loads(r['stations'])}
    for field,label,col,marker,style in [('observed_ph','Observed pH',OBS,'o','-'),('predicted_ph','Fixed REE model',MODEL,'s','-'),('baseline_ph','Fixed depth reference',BASE,'^','--')]:
        h,=ax.plot([float(r[field]) for r in ll],depth,color=col,marker=marker,ms=4.8,linestyle=style,label=label)
        if j==0:legend.append(h)
    ax.set_title(('A  Leg 1' if j==0 else 'B  Leg 2'),loc='left',pad=19)
    ax.text(0,1.025,f'{len(ids)} unique bottles · {len(stations)} stations',transform=ax.transAxes,fontsize=8.5,va='bottom')
    ax.set_xlim(7.30,8.05);ax.set_ylim(800,200);ax.set_xticks([7.3,7.5,7.7,7.9,8.0]);ax.set_yticks(range(200,801,100))
    ax.grid(axis='y',lw=.5,alpha=.18);ax.set_xlabel('pH (total scale, 25 °C)')
    for r in ll:data1.append({k:r[k] for k in ['leg','bin_index','low_m','high_m','n_records','n_stations','observed_ph','predicted_ph','baseline_ph','sample_ids','stations']})
axs[0].set_ylabel('Layer midpoint depth (m)')
fig.legend(handles=legend,loc='upper center',bbox_to_anchor=(.55,.99),ncol=3,handlelength=1.8,columnspacing=1.5)
fig.text(.11,.025,'Matched parent bottles; fixed inclusive 100 m layers. Lines connect layer means.',fontsize=8,color='#444444')
save(fig,'fig01_matched_depth_profiles');writecsv('fig01_source_values.csv',data1)

# Fig2: all aggregation choices; LOSO range only on the declared primary comparison.
fig,ax=plt.subplots(figsize=(7.1,3.8));fig.subplots_adjust(left=.28,right=.98,bottom=.21,top=.81)
variants=['primary','half_open','ree_reported_depth','station_equal'];names=['Primary: pH depth','Half-open depth bins','REE-reported depth','Equal station weight']
data2=[]
for leg,color,marker,off in [('leg1',C1,'o',-.11),('leg2',C2,'s',.11)]:
    case=[x for x in summary if x['leg']==leg]
    for i,v in enumerate(variants):
        r=next(x for x in case if x['variant']==v);val=r['delta_rmse_model_minus_baseline'];lo=hi=None
        if v=='primary':
            dd=[x['aggregate_on_same_layer_support']['delta_rmse_model_minus_baseline'] for x in loso[leg]['cases'] if x['aggregate_on_same_layer_support'] is not None]
            assert len(dd)==(6 if leg=='leg1' else 8);lo=min(dd);hi=max(dd)
            ax.hlines(i+off,lo,hi,color=color,lw=1.8,zorder=2);ax.plot([lo,hi],[i+off,i+off],'|',color=color,ms=7)
        ax.plot(val,i+off,marker=marker,color=color,ms=5.6,zorder=3,linestyle='none')
        data2.append(dict(leg=leg,variant=v,model_rmse=r['model_rmse'],baseline_rmse=r['baseline_rmse'],delta_rmse=val,n_supported_layers=r['n_supported_layers'],loso_min=lo,loso_max=hi))
ax.axvline(0,color='#777777',lw=.9,ls='--');ax.set_xlim(-.16,.16);ax.set_ylim(3.45,-.5)
ax.set_yticks(range(4),names);ax.set_xticks([-.15,-.1,-.05,0,.05,.1,.15]);ax.set_xticklabels(['−0.15','−0.10','−0.05','0','0.05','0.10','0.15'])
ax.grid(axis='x',alpha=.15,lw=.5);ax.set_xlabel('RMSE(fixed REE model) − RMSE(fixed depth reference), pH units')
legends=[Line2D([],[],marker='o',color=C1,linestyle='none',label='Leg 1'),Line2D([],[],marker='s',color=C2,linestyle='none',label='Leg 2'),Line2D([],[],color='#555555',marker='|',ms=7,label='Primary: range after deleting one station')]
fig.legend(handles=legends,loc='upper center',bbox_to_anchor=(.52,.99),ncol=3,columnspacing=1.2,fontsize=8)
ax.text(.02,1.02,'REE model lower error',transform=ax.transAxes,fontsize=8,color=C1,va='bottom')
ax.text(.98,1.02,'Depth reference lower error',transform=ax.transAxes,fontsize=8,color=C2,va='bottom',ha='right')
fig.text(.02,.025,'All six layers retained. Deletion ranges are sensitivity ranges, not confidence intervals.',fontsize=8,color='#444444')
save(fig,'fig02_fixed_sensitivity_comparison');writecsv('fig02_source_values.csv',data2)

# Fig3: geometry of the original fixed mapping and all current primary layer means.
fig,ax=plt.subplots(figsize=(7.1,5.1));fig.subplots_adjust(left=.12,right=.98,bottom=.17,top=.79)
xlo,xhi=min(trainx)*1e8,max(trainx)*1e8
ax.axvspan(xlo,xhi,color='#eceeef',zorder=0)
xx=np.linspace(1.38,4.10,220)
yy=(xx*1e-8-intercept)/beta
ax.plot(xx,yy,color='#777777',lw=1.2,ls=(0,(4,3)),zorder=1)
inside=np.linspace(xlo,xhi,40);ax.plot(inside,(inside*1e-8-intercept)/beta,color='#333333',lw=1.7,zorder=2)
ax.scatter(np.asarray(trainx)*1e8,trainy,s=23,color=TRAIN,marker='D',zorder=5)
data3=[dict(series='Original calibration',bin_index=i,low_m=200+100*i,high_m=300+100*i,mean_alpha=a,mean_observed_ph=p,mean_fixed_prediction_ph=(a-intercept)/beta) for i,(a,p) in enumerate(zip(trainx,trainy))]
# Fixed annotation offsets for leg / six ordered bins, chosen solely for legibility.
offsets={'leg1':[(-50,8),(7,-14),(8,8),(7,9),(-12,-17),(-59,-19)],
         'leg2':[(8,-2),(8,-3),(-58,-10),(8,9),(-57,-14),(8,-10)]}
for leg,col,marker in [('leg1',C1,'o'),('leg2',C2,'s')]:
    for i,r in enumerate(primary[leg]):
        ids=json.loads(r['sample_ids']);a=statistics.mean(float(samples[leg,sid]['alpha']) for sid in ids);p=float(r['observed_ph'])
        derived=(a-intercept)/beta;assert abs(derived-float(r['predicted_ph']))<1e-12
        ax.scatter(a*1e8,p,color=col,marker=marker,s=34,zorder=6,edgecolors='white',linewidths=.4)
        label=f"{int(r['low_m'])}–{int(r['high_m'])}"
        ax.annotate(label,(a*1e8,p),xytext=offsets[leg][i],textcoords='offset points',fontsize=7.5,color=col,arrowprops={'arrowstyle':'-','color':col,'lw':.5} if (leg=='leg1' and i>=4) or (leg=='leg2' and i==5) else None)
        data3.append(dict(series=leg,bin_index=i,low_m=int(r['low_m']),high_m=int(r['high_m']),mean_alpha=a,mean_observed_ph=p,mean_fixed_prediction_ph=derived))
ax.set_xlim(1.38,4.10);ax.set_ylim(7.29,8.035);ax.set_xticks([1.5,2,2.5,3,3.5,4]);ax.set_yticks([7.3,7.4,7.5,7.6,7.7,7.8,7.9,8.0])
ax.set_xlabel('Mean PAAS-normalized REE slope, α (×10⁻⁸ per atomic-number unit)');ax.set_ylabel('Observed mean pH (total scale, 25 °C)')
ax.grid(axis='y',alpha=.15,lw=.5)
legend=[Line2D([],[],marker='D',color=TRAIN,linestyle='none',label='Original six calibration means',ms=4),Line2D([],[],marker='o',color=C1,linestyle='none',label='Leg 1',ms=5),Line2D([],[],marker='s',color=C2,linestyle='none',label='Leg 2',ms=5),Line2D([],[],color='#333333',label='Original fixed mapping')]
fig.legend(handles=legend,loc='upper center',bbox_to_anchor=(.56,.98),ncol=2,columnspacing=1.9,fontsize=8.5)
ax.text(.69,.97,'Dashed line: extrapolation',transform=ax.transAxes,fontsize=8,color='#666666',va='top')
ax.text((xlo+xhi)/2,8.005,'Training α range',fontsize=8,color='#555555',ha='center',va='top')
fig.text(.02,.06,'Labels: depth intervals (m). Shading: original training-feature range, not a confidence band.',fontsize=8,color='#444444')
fig.text(.02,.025,'Post-result domain annotation only; every primary layer is shown and no subset is rescored.',fontsize=8,color='#444444')
save(fig,'fig03_calibration_feature_domain');writecsv('fig03_source_values.csv',data3)

captions='''# Figure captions and source notes

All figures are new presentation artifacts from frozen plan `ree-ph-fixed-transfer-v1`; they do not replace or modify the original run outputs. No new model, exclusion or score is introduced. PNG files are 450 dpi; SVG files retain editable text. The original measurements are previously published public data, reanalysed here.

**Figure 1. Matched-layer predictions and observations on a common pH axis.** Observed total-scale pH at 25 °C, predictions from the unchanged Lin–Catling calibration, and the six fixed original global depth-layer pH means are shown for (A) GP15 Leg 1 and (B) Leg 2. Every panel has identical pH and depth limits. Points are means for the original inclusive 100 m layers from 200 to 800 m; connecting lines are visual guides. No prediction is clipped or refitted. The primary comparisons use 24 unique parent bottles from six station labels in Leg 1 and 55 bottles from eight stations in Leg 2. Because inclusive boundaries repeat four and eight bottles, respectively, there are 28 and 63 layer memberships; memberships are not independent samples. From shallowest to deepest, Leg 1 has 5/6/6/3/5/3 bottle records and 4/5/5/3/4/3 station labels per layer; Leg 2 has 16/14/17/7/5/4 records and 7/7/7/5/4/3 station labels. REE and pH measurements are distinct aliquots linked by Sample_ID, Station_ID and Event_ID. Leg 1 was exploration-exposed; Leg 2 joint responses were first examined after protocol freeze. The legs share cruise/laboratory methods and are not statistically independent laboratory replicates. The depth reference is a comparator, not measured truth. Source: frozen `layer_summary.csv`, primary rows, with unique counts from their sample/station lists. Exact values are in `fig01_source_values.csv`.

**Figure 2. Prespecified aggregation and station-deletion checks.** Points show across-layer RMSE of the unchanged REE calibration minus RMSE of the fixed original depth reference on identical matched observations. Negative values favour the REE mapping; positive values favour the depth reference. All six layers are retained in each aggregate. The four rows show the primary inclusive pH-reported-depth calculation, half-open depth bins (last bin includes 800 m), inclusive REE-reported depth, and equal weighting of station means within each layer. Horizontal bars on the primary row are the complete ranges from deleting one station at a time: six Leg 1 and eight Leg 2 cases, all retaining the original nonempty-layer support. These are sensitivity ranges, not confidence intervals or independent replicates. No bootstrap interval is plotted. Primary layers meet the prespecified three-station criterion in both legs; Leg 1 half-open has only five supported layers and its displayed six-layer comparison is descriptive. All variants and both legs are shown, including the Leg 1 improvement. Source: `aggregate_summary.json:summaries` and `leave_one_station_out.json:<leg>.cases[*].aggregate_on_same_layer_support.delta_rmse_model_minus_baseline`. Exact points and deletion endpoints are in `fig02_source_values.csv`.

**Figure 3. Depth correspondence does not establish calibration-feature correspondence.** Original six saved calibration means (black diamonds), the unchanged mapping, and all six primary matched-layer means from each GP15 leg are shown in mean REE slope α versus observed pH space. α is the OLS slope of molar PAAS-normalized Sm, Gd, Dy and Er concentrations against atomic number. GP15 α values are means of the frozen bottle-level α values on exactly the layer membership used in Figure 1. The solid part of the fixed line spans the original six-feature range; dashed continuations are extrapolations. The grey strip spans α=2.564942791181893×10⁻⁸ to 3.2035971215737773×10⁻⁸; it is neither a joint α–pH validity domain nor a confidence interval. The original observed calibration pH range is 7.5843256070–7.7308787125, slightly different from the fitted line's endpoint values. Depth labels identify all fixed intervals. Leg 2's upper three means are outside the training feature range; its lower three have α within that range but observed pH below the training pH range. Thus the complete comparison mixes feature extrapolation and target-distribution shifts; it is not an entirely within-calibration-domain test. This post-result annotation changes no selection, result, threshold or subset score. Source: original author notebook's saved six α/pH outputs, the frozen model coefficients, `sample_predictions.csv`, and primary `layer_summary.csv` memberships. Exact coordinates are in `fig03_source_values.csv`.

## Source identity and interpretation

- Lin & Catling (2024), DOI [10.2475/001c.118215](https://doi.org/10.2475/001c.118215): fixed modern calibration and author notebook at Git commit `99ebf142d6787361c7720b7a3e7c213fd213eddb`. The paper already recognizes water-mass controls and extrapolation uncertainty (§3.2 and §4.2), contains regional pattern comparisons (Figures 4 and 10), and targets coarse Precambrian estimates of about 1–2 pH units (§4.6). These figures do not claim to refute that paleo objective or establish a new mechanism.
- Raw GP15 REE files: BCO-DMO datasets 932161 v1 and 932559 v1; pH: 838157 v2 and 838173 v2. Exact original file URLs, hashes and field locators are in the frozen `ree/round8/transfer-source-input.json`, also retained by `figure-source-manifest.json`.
- Figure source paths, SHA-256 values, record filters and field-level mappings are in `figure-source-manifest.json`. The source-value CSV files are plotting coordinates, not additional experiments.
- Independent numerical review: `reviews/round8-ree-transfer-independent-results-review.md` and its reproducible raw-CSV script/receipt. The figure-generation script does not refit the model or rerun hypothesis selection.

## Reproduction

From the repository root, use the existing Python 3.12 research environment:

```sh
.venv/bin/python -B tasks/2026-10-25-deepshovel/manuscript/figures/make_figures.py
```

The script rejects changed frozen source versions. Outputs are confined to this figure directory; it writes no frozen result, protocol, source or central task state.
'''
(HERE/'CAPTIONS.md').write_text(captions)
source_records=[{'path':p,'sha256':s} for p,s in expected.items()]
fieldmaps=[
 {'figure':'fig01','source':'results/ree_ph_fixed_transfer_v1/layer_summary.csv','filter':'variant == primary; each leg; ascending bin_index','x_fields':['observed_ph','predicted_ph','baseline_ph'],'y':'(low_m+high_m)/2','counts':'unique JSON sample_ids / stations; membership n_records'},
 {'figure':'fig02','source':'results/ree_ph_fixed_transfer_v1/aggregate_summary.json','filter':'all summaries, both legs, all 4 prespecified variants','x_field':'summaries[*].delta_rmse_model_minus_baseline','sensitivity_range_source':'results/ree_ph_fixed_transfer_v1/leave_one_station_out.json','sensitivity_range':'min/max of each leg cases[*].aggregate_on_same_layer_support.delta_rmse_model_minus_baseline, primary only; all cases retained'},
 {'figure':'fig03','original_calibration_source':'ree/round8/raw/Lin2024-REE_pH_proxy.ipynb','locator':'saved output text: average REE_slope of each 200–800m layer and Average pH of depth lines; notebook never executed','gp15_sources':['results/ree_ph_fixed_transfer_v1/sample_predictions.csv','results/ree_ph_fixed_transfer_v1/layer_summary.csv'],'gp15_x':'mean alpha for exactly each primary layer sample_ids','gp15_y':'primary observed_ph','line':'pH=(alpha-intercept)/beta from frozen protocol','status':'post-result descriptive domain annotation; no new subgroup score or selection'}]
manifest={'schema':'geoscience-research.manuscript-figures/v1','render_only':True,'new_model_fit':False,'source_sha256':source_records,'field_mappings':fieldmaps,'original_measurement_sources':sourceinput['sources'],
          'script':{'path':str(Path(__file__).resolve().relative_to(TASK)),'sha256':sha(Path(__file__))},
          'environment':{'python':sys.version,'matplotlib':matplotlib.__version__,'numpy':np.__version__},
          'outputs':[{'path':str(p.relative_to(TASK)),'sha256':sha(p)} for p in outputs]+[{'path':str(p.relative_to(TASK)),'sha256':sha(p)} for p in [HERE/'fig01_source_values.csv',HERE/'fig02_source_values.csv',HERE/'fig03_source_values.csv',HERE/'CAPTIONS.md']],
          'uncertainty_display':'No confidence interval is displayed. Figure2 ranges are fixed leave-one-station-out sensitivity ranges; Figure3 shading is training alpha support.'}
(HERE/'figure-source-manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
print(json.dumps({'figures':[p.name for p in outputs],'manifest_sha256':sha(HERE/'figure-source-manifest.json'),'script_sha256':sha(Path(__file__))},indent=2))
