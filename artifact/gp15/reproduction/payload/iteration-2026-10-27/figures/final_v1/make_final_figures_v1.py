#!/usr/bin/env python3
"""Render five final GP15 figures from existing frozen values and captions.

Root-freeze gated. No manuscript/SI writes, new selection, fit or diagnostics.
The unchanged plotting geometry is adapted from revision figure v3.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import shutil
import sys

HERE = Path(__file__).resolve().parent
ITER = HERE.parents[1]
TASK = ITER.parent
SCRIPT = 'iteration-2026-10-27/figures/final_v1/make_final_figures_v1.py'
DIAG = TASK/'revision-2026-10-27/analysis/results-v2'
OLD = TASK/'manuscript/figures'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))
def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def writecsv(name,data):
    with (HERE/name).open('x',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--plan',type=Path,required=True)
arguments=parser.parse_args()
plan_path=arguments.plan.resolve()
plan=json.loads(plan_path.read_text())
if not (plan.get('schema')=='gp15-final-figures-plan/v1' and plan.get('frozen') is True
        and plan.get('frozen_by')=='root' and plan.get('frozen_at_utc')):
    raise RuntimeError('ROOT_FREEZE_REQUIRED: plotting draft is for review only')
if sys.version_info[:2]!=(3,12):
    raise RuntimeError('Approved Python 3.12 environment required')
if Path(__file__).resolve()!=TASK/SCRIPT or plan.get('script')!=SCRIPT or sha(TASK/SCRIPT)!=plan.get('script_sha256'):
    raise RuntimeError('Script differs from the root binding')
if plan.get('task_root_relative')!='../../..' or (plan_path.parent/'../../..').resolve()!=TASK:
    raise RuntimeError('Unexpected task-root contract')
expected_roles={
 'diagnostic_manifest':'revision-2026-10-27/analysis/results-v2/run_manifest.json',
 'support':'revision-2026-10-27/analysis/results-v2/station_layer_support.csv',
 'support_long':'revision-2026-10-27/analysis/results-v2/station_layer_support_long.csv',
 'geometry':'revision-2026-10-27/analysis/results-v2/station_geometry.json',
 'mse':'revision-2026-10-27/analysis/results-v2/mse_decomposition.csv',
 'primary_coordinates':'manuscript/figures/fig01_source_values.csv',
 'sensitivity_coordinates':'manuscript/figures/fig02_source_values.csv',
 'domain_coordinates':'manuscript/figures/fig03_source_values.csv',
 'original_figure_manifest':'manuscript/figures/figure-source-manifest.json',
 'protocol':'ree/round8/protocol-frozen-v1.json',
 'captions':'iteration-2026-10-27/figures/final_v1/CAPTIONS.md',
 'caption_bindings':'iteration-2026-10-27/figures/final_v1/caption-source-bindings.json'}
entries=plan['inputs']
if len(entries)!=len(expected_roles) or {x['role'] for x in entries}!=set(expected_roles):
    raise RuntimeError('Unexpected figure input roles')
for item in entries:
    path=TASK/item['path']
    if item['path']!=expected_roles[item['role']] or path.resolve()!=path or sha(path)!=item['sha256']:
        raise RuntimeError('Figure input binding failed: '+item['role'])
source_records=[dict(x) for x in entries]
coordinate_references=plan.get('coordinate_identity_reference',[])
if len(coordinate_references)!=5:
    raise RuntimeError('Exactly five frozen coordinate identity references required')
for item in coordinate_references:
    if not item['path'].startswith('revision-2026-10-27/figures/v3/') or sha(TASK/item['path'])!=item['sha256']:
        raise RuntimeError('Prior coordinate reference binding failed')
    source_records.append({'role':'prior_coordinate_identity','path':item['path'],'sha256':item['sha256']})
# Validate all pre-existing reviewer outputs against their frozen manifest.
run=json.loads((DIAG/'run_manifest.json').read_text())
for item in run['outputs']:
    if sha(DIAG/item['path'])!=item['sha256']:
        raise RuntimeError('Frozen reviewer output SHA changed: '+item['path'])
expected_generated=[stem+'.'+ext for stem in [
 'fig01_sampling_support','fig02_sampled_layer_means','fig03_error_decomposition',
 'figS01_fixed_sensitivity','figS02_calibration_domain'] for ext in ('png','svg','pdf')]
expected_generated += ['fig01_source_values.csv','fig02_source_values.csv','fig03_source_values.csv',
 'figS01_source_values.csv','figS02_source_values.csv','figure-source-manifest.json']
if plan.get('generated_outputs')!=expected_generated:
    raise RuntimeError('Output names differ from the declared five-figure contract')
if any((HERE/name).exists() for name in expected_generated):
    raise FileExistsError('Never overwrite a generated final figure; use a new version or a disposable reproduction copy')
caption_bytes=(HERE/'CAPTIONS.md').read_bytes()
if caption_bytes.decode().count('Data plots were produced by reproducible Python code developed with Codex assistance.')!=5:
    raise RuntimeError('Each of five captions must retain the plotting-code disclosure')
support=rows(DIAG/'station_layer_support.csv');long=rows(DIAG/'station_layer_support_long.csv')
geo=json.loads((DIAG/'station_geometry.json').read_text());geometry={(x['leg'],x['station']):x for x in geo}
means=rows(OLD/'fig01_source_values.csv');mse=rows(DIAG/'mse_decomposition.csv')
if (len(geo),len(support),len(means),len(mse))!=(14,14,12,4):
    raise RuntimeError('Unexpected stored support; no new selection is permitted')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import ListedColormap,BoundaryNorm
import numpy as np
COLORS={'leg1':'#007F78','leg2':'#C75B18'};OBS='#233F55';MODEL='#B34635';BASE='#777777'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':10,'axes.titlesize':11,
 'axes.titleweight':'bold','axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.8,
 'legend.frameon':False,'legend.fontsize':9,'xtick.direction':'out','ytick.direction':'out',
 'pdf.fonttype':42,'ps.fonttype':42,'pdf.compression':9,'svg.fonttype':'none','svg.hashsalt':'gp15-final-iteration-20261027-v1','figure.facecolor':'white','savefig.facecolor':'white'})
outputs=[]
def save(fig,name):
    for ext in ('png','svg','pdf'):
        metadata={'Date':None} if ext=='svg' else {'CreationDate':None,'ModDate':None,'Creator':'Reproducible Python plotting code developed with Codex assistance'} if ext=='pdf' else {'Software':'Matplotlib; existing source-bound public-data results'}
        p=HERE/(name+'.'+ext);fig.savefig(p,dpi=450 if ext=='png' else None,bbox_inches='tight',pad_inches=.07,metadata=metadata)
        outputs.append(p)
    plt.close(fig)

# Figure1 explicitly shows changing spatial membership alongside all station zeros.
fig=plt.figure(figsize=(7.6,6.65));gs=fig.add_gridspec(2,2,width_ratios=[.88,1.38],height_ratios=[6,8],
 left=.08,right=.985,top=.96,bottom=.18,wspace=.43,hspace=.38)
ax=fig.add_subplot(gs[:,0]);ax.set_facecolor('#F6FAFC')
offsets={'5':(5,1),'10':(5,1),'12':(5,1),'16':(5,1),'18':(5,3),'18.3':(-34,5),
 '18.6':(-40,-8),'19':(5,-9),'21':(5,1),'25':(5,1),'31':(5,3),'33':(5,-6),'37':(5,1),'39':(5,-3)}
for leg in ('leg1','leg2'):
    for g in [x for x in geo if x['leg']==leg]:
        x=g['representative_longitude_for_map'];y=g['representative_latitude_for_map'];marker='o' if leg=='leg1' else 's'
        ax.scatter(x,y,c=COLORS[leg],marker=marker,s=28,edgecolors='white',linewidth=.4,zorder=3)
        ax.annotate(g['station'],(x,y),xytext=offsets[g['station']],textcoords='offset points',fontsize=8.5,color=COLORS[leg],
            arrowprops={'arrowstyle':'-','lw':.45,'color':COLORS[leg]} if g['station'] in ('18.3','18.6') else None)
ax.set_title('A  Eligible GP15 stations',loc='left',pad=9)
ax.set_xlim(-156.9,-150.6);ax.set_ylim(-24,58)
ax.set_xticks([-156,-154,-152],['156°W','154°W','152°W']);ax.set_yticks([-20,0,20,40],['20°S','0°','20°N','40°N'])
ax.set_xlabel('Longitude');ax.set_ylabel('Latitude');ax.grid(lw=.4,alpha=.22)
ax.text(.06,.30,'Central\nPacific',transform=ax.transAxes,color='#6C7B83',fontsize=9)
ax.legend(handles=[Line2D([],[],color=COLORS[l],marker='o' if l=='leg1' else 's',ls='none',label='Leg 1' if l=='leg1' else 'Leg 2') for l in ('leg1','leg2')],loc='upper left',fontsize=8)
fig1values=[]
for k,leg in enumerate(('leg1','leg2')):
    sub=fig.add_subplot(gs[k,1]);ss=[r for r in support if r['leg']==leg]
    matrix=np.asarray([[int(r[f'n_{lo}_{lo+100}']) for lo in range(200,800,100)] for r in ss])
    # Discrete counts0–4, consistent scale in both blocks; no response color coding.
    cmap=ListedColormap(['#FFFFFF','#E4EEF1','#BED3DB','#89ABB9','#4E7A8D']);norm=BoundaryNorm([-.5,.5,1.5,2.5,3.5,4.5],cmap.N)
    sub.pcolormesh(np.arange(7)-.5,np.arange(len(ss)+1)-.5,matrix,cmap=cmap,norm=norm,shading='flat',rasterized=False)
    sub.set_xlim(-.5,5.5);sub.set_ylim(len(ss)-.5,-.5)
    sub.set_xticks(range(6),[f'{v}–\n{v+100}' for v in range(200,800,100)],fontsize=8.2)
    sub.set_yticks(range(len(ss)),[r['station']+(' *' if r['all_six_layers']=='True' else '') for r in ss],fontsize=8.8)
    sub.set_xticks(np.arange(-.5,6,1),minor=True);sub.set_yticks(np.arange(-.5,len(ss),1),minor=True)
    sub.grid(which='minor',color='#CED5D8',lw=.5);sub.tick_params(which='minor',length=0)
    sub.set_ylabel('Station');sub.set_xlabel('Inclusive depth layer (m)',fontsize=9)
    sub.set_title(('B  Leg 1' if leg=='leg1' else 'C  Leg 2')+' bottle memberships',loc='left',pad=8)
    for i,r in enumerate(ss):
        g=geometry[leg,r['station']]
        for j,value in enumerate(matrix[i]):
            sub.text(j,i,str(value),ha='center',va='center',fontsize=9,color='white' if value==4 else '#1B252B')
            rec=next(x for x in long if x['leg']==leg and x['station']==r['station'] and int(x['bin_index'])==j)
            fig1values.append({'leg':leg,'station':r['station'],'bin_index':j,'low_m':200+100*j,'high_m':300+100*j,
             'n_records':int(value),'latitude_for_map':g['representative_latitude_for_map'],'longitude_for_map':g['representative_longitude_for_map'],
             'latitude_min':g['latitude_range'][0],'latitude_max':g['latitude_range'][1],'longitude_min':g['longitude_range'][0],'longitude_max':g['longitude_range'][1],
             'all_six_layers':g['all_six_layers'],'sample_ids':rec['sample_ids'],'ree_csv_lines':rec['ree_csv_lines'],'ph_csv_lines':rec['ph_csv_lines']})
fig.text(.08,.037,'Numbers are bottle memberships; 0 means no eligible bottle. * marks stations represented in all six layers.',fontsize=8.2)
fig.text(.08,.012,'Recorded station positions only; no area weights, water-mass boundaries or complete regional profile implied.',fontsize=8.2)
save(fig,'fig01_sampling_support');writecsv('fig01_source_values.csv',fig1values)

# Figure2 unchanged primary scores; no visual promise of a complete fixed-station profile.
fig,axs=plt.subplots(1,2,figsize=(7.1,4.3),sharex=True,sharey=True)
fig.subplots_adjust(left=.11,right=.98,bottom=.25,top=.80,wspace=.14)
handles=[]
for k,(leg,sub) in enumerate(zip(('leg1','leg2'),axs)):
    ll=[r for r in means if r['leg']==leg];depth=[(float(r['low_m'])+float(r['high_m']))/2 for r in ll]
    for field,label,col,marker in [('observed_ph','Measured GP15',OBS,'o'),('predicted_ph','Fixed REE model',MODEL,'s'),('baseline_ph','Fixed depth reference',BASE,'^')]:
        h,=sub.plot([float(r[field]) for r in ll],depth,color=col,marker=marker,ms=4.8,ls=':' if field!='baseline_ph' else '--',lw=1.1)
        if k==0:handles.append(h)
    sub.set_title(('A  Leg 1' if k==0 else 'B  Leg 2')+' sampled means',loc='left',pad=18)
    sub.text(0,1.02,'24 bottles · 6 stations' if k==0 else '55 bottles · 8 stations',transform=sub.transAxes,fontsize=8.5,va='bottom')
    sub.set_xlim(7.30,8.05);sub.set_ylim(800,200);sub.set_xticks([7.3,7.5,7.7,7.9,8.0]);sub.set_yticks(range(200,801,100))
    sub.set_xlabel('pH (total scale, 25 °C)');sub.grid(axis='y',alpha=.18,lw=.5)
axs[0].set_ylabel('Layer midpoint depth (m)')
fig.legend(handles=handles,labels=['Measured GP15','Fixed REE model','Fixed depth reference'],loc='upper center',bbox_to_anchor=(.55,.99),ncol=3,fontsize=8.3)
fig.text(.04,.07,'Each point averages actual eligible samples; neighboring layers can contain different stations (Fig. 1).',fontsize=8)
fig.text(.04,.025,'Lines are visual guides. These are sampled layer means, without area weighting or fixed spatial support.',fontsize=8)
save(fig,'fig02_sampled_layer_means');shutil.copyfile(OLD/'fig01_source_values.csv',HERE/'fig02_source_values.csv')

# Figure3 descriptive identity, not a de-biased model evaluation.
fig,sub=plt.subplots(figsize=(7.1,4.1));fig.subplots_adjust(left=.12,right=.98,top=.78,bottom=.24)
x=np.array([0,1,2.5,3.5]);bias2=np.array([float(r['squared_mean_error']) for r in mse]);center=np.array([float(r['centered_mse']) for r in mse])
sub.bar(x,bias2,width=.64,color=OBS,label='Squared mean error')
sub.bar(x,center,bottom=bias2,width=.64,color='#BED3DB',edgecolor='white',lw=.5,label='Centered MSE')
sub.set_xticks(x,['Leg 1\nREE model','Leg 1\nDepth reference','Leg 2\nREE model','Leg 2\nDepth reference'],fontsize=8.6)
sub.set_ylabel('Mean squared error (pH²)');sub.set_ylim(0,.055);sub.set_yticks([0,.01,.02,.03,.04,.05]);sub.grid(axis='y',alpha=.18,lw=.5);sub.set_axisbelow(True)
for xx,row in zip(x,mse):
    sub.text(xx,float(row['mse'])+.0012,f"RMSE {float(row['rmse']):.3f}",ha='center',fontsize=8.3)
fig.legend(handles=[Patch(facecolor=OBS,label='Squared mean error'),Patch(facecolor='#BED3DB',label='Centered MSE')],loc='upper center',bbox_to_anchor=(.55,.99),ncol=2)
leg2_mse=next(r for r in mse if r['leg']=='leg2' and r['predictor']=='REE_model')
fig.text(.12,.865,f"Leg 2 REE model: mean error {float(leg2_mse['mean_error']):+.3f} pH; {100*float(leg2_mse['squared_mean_error_fraction_of_mse']):.1f}% of MSE is the squared mean error.",fontsize=9)
fig.text(.03,.09,'MSE = (mean error)² + mean[(error − mean error)²], across the six equally weighted layer means.',fontsize=8)
fig.text(.03,.04,'Centering diagnoses error structure; it is not a fitted correction or a new validation performance.',fontsize=8)
save(fig,'fig03_error_decomposition');shutil.copyfile(DIAG/'mse_decomposition.csv',HERE/'fig03_source_values.csv')

# FigureS1 redraws all original finite points/ranges for consistent embedded-fontPDF.
sensitivity=rows(OLD/'fig02_source_values.csv')
fig,sub=plt.subplots(figsize=(7.1,3.8));fig.subplots_adjust(left=.28,right=.98,bottom=.21,top=.81)
variants=['primary','half_open','ree_reported_depth','station_equal'];labels=['Primary: pH depth','Half-open depth bins','REE-reported depth','Equal station weight']
for leg,col,marker,offset in [('leg1',COLORS['leg1'],'o',-.11),('leg2',COLORS['leg2'],'s',.11)]:
    for i,variant in enumerate(variants):
        row=next(x for x in sensitivity if x['leg']==leg and x['variant']==variant)
        if variant=='primary':
            left,right=float(row['loso_min']),float(row['loso_max']);sub.hlines(i+offset,left,right,color=col,lw=1.8);sub.plot([left,right],[i+offset,i+offset],'|',color=col,ms=7)
        sub.plot(float(row['delta_rmse']),i+offset,marker=marker,color=col,ms=5.6,ls='none')
sub.axvline(0,color='#777777',lw=.9,ls='--');sub.set_xlim(-.16,.16);sub.set_ylim(3.45,-.5);sub.set_yticks(range(4),labels)
sub.set_xticks([-.15,-.1,-.05,0,.05,.1,.15]);sub.set_xticklabels(['−0.15','−0.10','−0.05','0','0.05','0.10','0.15'])
sub.grid(axis='x',alpha=.15,lw=.5);sub.set_xlabel('RMSE(fixed REE model) − RMSE(fixed depth reference), pH units')
fig.legend(handles=[Line2D([],[],marker='o',color=COLORS['leg1'],ls='none',label='Leg 1'),Line2D([],[],marker='s',color=COLORS['leg2'],ls='none',label='Leg 2'),Line2D([],[],color='#555555',marker='|',ms=7,label='Primary: range after deleting one station')],loc='upper center',bbox_to_anchor=(.52,.99),ncol=3,columnspacing=1.2,fontsize=8)
sub.text(.02,1.02,'REE model lower error',transform=sub.transAxes,fontsize=8,color=COLORS['leg1'],va='bottom');sub.text(.98,1.02,'Depth reference lower error',transform=sub.transAxes,fontsize=8,color=COLORS['leg2'],va='bottom',ha='right')
fig.text(.02,.025,'All six layers retained. Deletion ranges are sensitivity ranges, not confidence intervals.',fontsize=8,color='#444444')
save(fig,'figS01_fixed_sensitivity');shutil.copyfile(OLD/'fig02_source_values.csv',HERE/'figS01_source_values.csv')

# FigureS2 redraws the fixed domain coordinates with corrected reference-pH provenance.
domain=rows(OLD/'fig03_source_values.csv');train=[r for r in domain if r['series']=='Original calibration']
lo=min(float(r['mean_alpha']) for r in train)*1e8;hi=max(float(r['mean_alpha']) for r in train)*1e8
fig,sub=plt.subplots(figsize=(7.1,5.15));fig.subplots_adjust(left=.12,right=.98,bottom=.18,top=.80)
sub.axvspan(lo,hi,color='#ECEEEF',zorder=0);xx=np.linspace(1.38,4.1,220);fixed=json.loads((TASK/'ree/round8/protocol-frozen-v1.json').read_text())['model'];beta=fixed['beta'];b=fixed['intercept']
sub.plot(xx,(xx*1e-8-b)/beta,color='#777777',ls=(0,(4,3)),lw=1.2);inside=np.linspace(lo,hi,40);sub.plot(inside,(inside*1e-8-b)/beta,color='#333333',lw=1.7)
sub.scatter([float(r['mean_alpha'])*1e8 for r in train],[float(r['mean_observed_ph']) for r in train],s=23,c='#202020',marker='D',zorder=5)
offset={'leg1':[(-50,8),(7,-14),(8,8),(7,9),(-12,-17),(-59,-19)],'leg2':[(8,-2),(8,-3),(-58,-10),(8,9),(-57,-14),(8,-10)]}
for leg in ('leg1','leg2'):
    for i,r in enumerate([x for x in domain if x['series']==leg]):
        a=float(r['mean_alpha'])*1e8;p=float(r['mean_observed_ph']);sub.scatter(a,p,c=COLORS[leg],marker='o' if leg=='leg1' else 's',s=34,zorder=6,edgecolors='white',linewidth=.4)
        sub.annotate(f"{r['low_m']}–{r['high_m']}",(a,p),xytext=offset[leg][i],textcoords='offset points',fontsize=7.5,color=COLORS[leg],
            arrowprops={'arrowstyle':'-','color':COLORS[leg],'lw':.5} if (leg=='leg1' and i>=4) or (leg=='leg2' and i==5) else None)
sub.set_xlim(1.38,4.10);sub.set_ylim(7.29,8.035);sub.set_xticks([1.5,2,2.5,3,3.5,4]);sub.set_yticks([7.3,7.4,7.5,7.6,7.7,7.8,7.9,8.0])
sub.set_xlabel('Mean PAAS-normalized REE slope, α (×10⁻⁸ per atomic-number unit)');sub.set_ylabel('Mean pH (total scale, 25 °C)');sub.grid(axis='y',alpha=.15,lw=.5)
handles=[Line2D([],[],marker='D',color='#202020',ls='none',label='Six global calibration means',ms=4),Line2D([],[],marker='o',color=COLORS['leg1'],ls='none',label='Measured GP15 Leg 1',ms=5),Line2D([],[],marker='s',color=COLORS['leg2'],ls='none',label='Measured GP15 Leg 2',ms=5),Line2D([],[],color='#333333',label='Original fixed mapping')]
fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.55,.99),ncol=2,fontsize=8)
sub.text(.69,.97,'Dashed line: extrapolation',transform=sub.transAxes,fontsize=8,color='#666666',va='top');sub.text((lo+hi)/2,8.005,'Six global means: α range',fontsize=8,color='#555555',ha='center',va='top')
fig.text(.02,.075,'Calibration pH: retained GLODAP product values; measured/calculated fraction unknown.',fontsize=7.8)
fig.text(.02,.036,'Shading spans six global calibration-mean features; not the full raw-source envelope or a joint validity region.',fontsize=8)
save(fig,'figS02_calibration_domain');shutil.copyfile(OLD/'fig03_source_values.csv',HERE/'figS02_source_values.csv')

# Captions are immutable inputs; do not write a main-text or SI document.
for item in entries:
    if sha(TASK/item['path'])!=item['sha256']:
        raise RuntimeError('Figure source changed during rendering: '+item['role'])
if (HERE/'CAPTIONS.md').read_bytes()!=caption_bytes:
    raise RuntimeError('Caption input changed during rendering')
coordinate_outputs=['fig01_source_values.csv','fig02_source_values.csv','fig03_source_values.csv',
 'figS01_source_values.csv','figS02_source_values.csv']
for final_name,prior_name in [('fig01_source_values.csv','fig01_source_values.csv'),
 ('fig02_source_values.csv','fig02_source_values.csv'),('fig03_source_values.csv','fig03_source_values.csv'),
 ('figS01_source_values.csv','figS01_source_values.csv'),('figS02_source_values.csv','figS02_source_values.csv')]:
    prior=TASK/'revision-2026-10-27/figures/v3'/prior_name
    if sha(HERE/final_name)!=sha(prior):
        raise RuntimeError('Presentation-only coordinate identity failed: '+final_name)
manifest={
 'schema':'gp15-final-figures/v1',
 'scientific_calculation_scope':'Render existing frozen values only; no new sample selection, fit, uncertainty analysis or SI generation',
 'source_sha256':source_records,
 'plan':{'path':str(plan_path.relative_to(TASK)),'sha256':sha(plan_path)},
 'script':{'path':SCRIPT,'sha256':sha(Path(__file__))},
 'caption_input':{'path':str((HERE/'CAPTIONS.md').relative_to(TASK)),'sha256':sha(HERE/'CAPTIONS.md'),'unchanged':True},
 'caption_source_bindings':{'path':str((HERE/'caption-source-bindings.json').relative_to(TASK)),'sha256':sha(HERE/'caption-source-bindings.json')},
 'field_mappings':[
  {'figure':'Figure 1','coordinates':'station_geometry representative longitude/latitude; all 14 stations','counts':'station_layer_support n_200_300 through n_700_800, including zero','row_locators':'station_layer_support_long sample_ids/REE and pH CSV lines'},
  {'figure':'Figure 2','x':['observed_ph','predicted_ph','baseline_ph'],'y':'midpoint(low_m,high_m)','selection':'all 12 unchanged primary sampled means'},
  {'figure':'Figure 3','bottom':'squared_mean_error','stack':'centered_mse','annotation':['rmse','mean_error','squared_mean_error_fraction_of_mse'],'interpretation':'Descriptive identity, not correction or pure vertical-shape error'},
  {'figure':'Figure S1','source':'all 8 original aggregation points and primary station-deletion ranges','selection_changed':False},
  {'figure':'Figure S2','source':'all 18 original calibration/GP15 coordinate rows','calibration_definition':'six global calibration means, not the full raw-source envelope','selection_changed':False}],
 'environment':{'python':sys.version,'matplotlib':matplotlib.__version__,'numpy':np.__version__},
 'outputs':[{'path':str(p.relative_to(TASK)),'sha256':sha(p),'bytes':p.stat().st_size}
            for p in outputs+[HERE/name for name in coordinate_outputs]],
 'presentation_changes_from_v3':[
  'Final captions are exact bound input paragraphs from p17, reported unchanged in p18; no caption or SI regeneration.',
  'Figure S2 labels explicitly identify six global calibration means and their feature range.',
  'Figure 3 mean-offset annotation is formatted from the same frozen MSE row instead of duplicated numeric text.',
  'Fixed SVG salt identifies this final presentation version; PDF creation/modification dates remain absent and fonts use Type42.',
  'All five coordinate CSVs must remain byte-identical to v3.'],
 'main_figures':3,'supplementary_figures':2,'figure_coordinate_csvs':5,
 'generated_artifact_count_including_this_manifest':21,
 'immutable_caption_file_is_an_input_not_a_deleted_generated_output':True,
 'not_written':['main manuscript','scientific SI','central state','any original frozen source/program/protocol/result'],
 'uncertainty':'No new confidence interval or error bar; centering and station deletions retain their stated descriptive scope.'}
dump(HERE/'figure-source-manifest.json',manifest)
print(json.dumps({'main_figures':3,'supplementary_figures':2,'generated_files':21,
 'caption_input_sha256':sha(HERE/'CAPTIONS.md'),'figure_manifest_sha256':sha(HERE/'figure-source-manifest.json')},indent=2))
