#!/usr/bin/env python3
"""Render source-bound reviewer figures and supplementary tables; no new fit.

Run in Python3.12 research .venv. The task root is inferred from this file.
Numerical diagnostics are read only from the frozen v2 run and R8 outputs.
"""
from pathlib import Path
import csv,hashlib,json,shutil,statistics,sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import ListedColormap,BoundaryNorm
import numpy as np

HERE=Path(__file__).resolve().parent
REV=HERE.parents[1];TASK=REV.parent
DIAG=REV/'analysis/results-v2';OLD=TASK/'manuscript/figures'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def writecsv(name,data):
    with (HERE/name).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

# Freeze-v2 diagnostics are accepted only with the root-run manifest's exact hashes.
run=json.loads((DIAG/'run_manifest.json').read_text())
for item in run['outputs']:assert sha(DIAG/item['path'])==item['sha256'],item['path']
source_paths=[DIAG/'station_layer_support.csv',DIAG/'station_layer_support_long.csv',DIAG/'station_geometry.json',
 DIAG/'mse_decomposition.csv',DIAG/'common_station_summary.json',DIAG/'training_common_multiplier.csv',DIAG/'summary.json',
 REV/'baseline/manuscript/supplementary-tables.en.md',OLD/'fig01_source_values.csv',OLD/'fig02_source_values.csv',OLD/'fig03_source_values.csv',
 OLD/'fig02_fixed_sensitivity_comparison.png',OLD/'fig02_fixed_sensitivity_comparison.svg',
 TASK/'results/ree_ph_fixed_transfer_v1/aggregate_summary.json',TASK/'results/ree_ph_fixed_transfer_v1/layer_summary.csv',
 TASK/'results/ree_ph_fixed_transfer_v1/leave_one_station_out.json',TASK/'ree/round8/protocol-frozen-v1.json',
 TASK/'ree/round8/raw/Lin2024-REE_pH_proxy.py',TASK/'ree/round8/raw/Lin2024-REE_pH_proxy.ipynb']
source_records=[{'path':str(p.relative_to(TASK)),'sha256':sha(p)} for p in source_paths]
support=rows(DIAG/'station_layer_support.csv');long=rows(DIAG/'station_layer_support_long.csv')
geo=json.loads((DIAG/'station_geometry.json').read_text());geometry={(x['leg'],x['station']):x for x in geo}
means=rows(OLD/'fig01_source_values.csv');mse=rows(DIAG/'mse_decomposition.csv')
summary=json.loads((DIAG/'summary.json').read_text());common=json.loads((DIAG/'common_station_summary.json').read_text())
training=rows(DIAG/'training_common_multiplier.csv')
COLORS={'leg1':'#007F78','leg2':'#C75B18'};OBS='#233F55';MODEL='#B34635';BASE='#777777'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':10,'axes.titlesize':11,
 'axes.titleweight':'bold','axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.8,
 'legend.frameon':False,'legend.fontsize':9,'xtick.direction':'out','ytick.direction':'out',
 'pdf.fonttype':42,'ps.fonttype':42,'pdf.compression':9,'svg.fonttype':'none','svg.hashsalt':'gp15-reviewer-revision-20261027-v3','figure.facecolor':'white','savefig.facecolor':'white'})
outputs=[]
def save(fig,name):
    for ext in ('png','svg','pdf'):
        metadata={'Date':None} if ext=='svg' else {'CreationDate':None,'ModDate':None,'Creator':'Reproducible Python plotting code developed with Codex assistance'} if ext=='pdf' else {'Software':'Matplotlib; source-bound post-review diagnostics'}
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
fig.text(.12,.865,'Leg 2 REE model: mean error +0.211 pH; 93.3% of MSE is the squared mean error.',fontsize=9)
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
handles=[Line2D([],[],marker='D',color='#202020',ls='none',label='Original training reference means',ms=4),Line2D([],[],marker='o',color=COLORS['leg1'],ls='none',label='Measured GP15 Leg 1',ms=5),Line2D([],[],marker='s',color=COLORS['leg2'],ls='none',label='Measured GP15 Leg 2',ms=5),Line2D([],[],color='#333333',label='Original fixed mapping')]
fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.55,.99),ncol=2,fontsize=8)
sub.text(.69,.97,'Dashed line: extrapolation',transform=sub.transAxes,fontsize=8,color='#666666',va='top');sub.text((lo+hi)/2,8.005,'Training α range',fontsize=8,color='#555555',ha='center',va='top')
fig.text(.02,.075,'Original training pH: nonmissing GLODAP product values; direct-measurement restriction not established.',fontsize=7.8)
fig.text(.02,.036,'All primary layers shown. Shading is feature support, not a confidence band or a joint validity domain.',fontsize=8)
save(fig,'figS02_calibration_domain');shutil.copyfile(OLD/'fig03_source_values.csv',HERE/'figS02_source_values.csv')

captions='''# Revised figure captions

**Figure 1. Recorded station positions and changing sampling support.** (A) Positions of the 14 stations with eligible primary bottles, using the original REE files' recorded start coordinates. The map is a coordinate plot, without a coastline, area weights or an inferred water-mass boundary. Markers use the median recorded coordinate of eligible bottles at each station; every recorded coordinate and its source line remains in station_geometry.json. (B–C) Eligible bottle memberships in all six primary inclusive 100 m layers, including explicit zeros. An asterisk identifies stations represented in every layer. Leg 1 uses24unique bottles/28memberships; Leg2 uses55/63. Boundary records can occur in adjacent layers and are not additional independent samples. Stations16and18 are present in every Leg1layer;31and33 are present in every Leg2layer. Missing stations are not recovered by within-layer station weighting. The plotted layer means therefore describe actual eligible samples rather than an area-weighted or geographically complete regional profile. Source: frozen review diagnostic station_layer_support.csv, station_layer_support_long.csv and station_geometry.json; plotting coordinates and original CSV row locators are in fig01_source_values.csv.

**Figure 2. Fixed predictions compared on actual sampled layer means.** Measured GP15 total-scale pH at25°C, unchanged REE predictions and the original fixed global-depth reference are compared for bothlegs on identical bottle support. Axes are identical. Neighboring layers can contain different station groups, as shown in Figure1; joining points is a visual guide and does not establish a vertical profile at fixed spatial support. The reference is a comparator, not locally measured truth. The comparison does not apply area weighting or infer representative regional means. Source: the original frozen primary layer_summary.csv; full-precision plotting values and sample/station memberships are reproduced in fig02_source_values.csv.

**Figure 3. Descriptive mean-offset and centered-error contributions.** Each bar shows the exact decomposition of six-layer MSE into squared mean error and centered MSE, for both fixed predictions and bothlegs. The two pieces sum to the original MSE; labels give original RMSE in pHunits. Leg2REE meanerror is+0.211pH and its square contributes93.3%of totalMSE. Centered error retains discrepancies after subtracting the target-observed mean error for diagnosis only. It is neither a validated bias correction nor a new model performance; no refit, interval or additional independent test is represented. Source: mse_decomposition.csv, independently reconstructed from the original matched observations; full-precision values in fig03_source_values.csv.

**Figure S1. Original fixed aggregation and station-deletion checks.** All four prespecified aggregation variants and bothlegs are retained from the original R8 analysis. Points are REE-minus-fixed-depth-reference RMSE on matched support; negative values favor the REE model. Bars on the primary rows span every individual station deletion, and are sensitivity ranges rather than confidence intervals. The Leg1half-open variant retains a weak layer, with only five layers meeting the three-station criterion. This supplementary panel redraws the original coordinates and deletion ranges without numerical changes; neither the number of scenarios nor the number of deletion cases is an independent sample size. Source: original aggregate_summary.json and leave_one_station_out.json; exact plotted values in figS01_source_values.csv.

**Figure S2. Original calibration reference and all sampled primary means.** Blackdiamonds are the original six training reference means; coloredpoints are measured GP15pH means and their corresponding REEfeatures. The original training code accepts nonmissing G2phts25p0 values from the merged GLODAPproduct and does not impose a direct-pH-measurement-only restriction; the calculated-value fraction in the training subset is not established. The y-axis therefore identifies meanpH without calling every training value a direct observation. The solid fixed line spans the original alpha feature range and dashed segments extrapolate. Greyshading represents feature support, not joint alpha–pH validity or a confidence region. All12GP15primarylayer means remain, including target-pH departures among points with in-range alpha. This descriptive domain annotation does not change selection, scoring or the original calibration. Source: saved original training means and original frozen GP15 outputs; coordinate values unchanged in figS02_source_values.csv.

## Reproduction and interpretation

All new figures are presentation artifacts, not new experiments or independent validation. Figures1and3 use the root-frozen post-review diagnosticv2; Figure2 uses unchanged R8primary outputs. FigureS1 redraws the existing manuscript panel from its unchanged original coordinate and range table. FigureS2 redraws those same saved coordinates with corrected training-pH provenance wording. The script writes only inside this revision figure directory and a new generated supplementary v3 table file. It does not write original raw data, frozen programs, protocols, results or central state. PNGfiles are450dpi and SVGtext remains editable. Source hashes, field mappings and output hashes are in figure-source-manifest.json.
'''
# Editorial spaces are deliberate prose formatting only; numerical fields stay exact in CSV.
replacements={'Leg1':'Leg 1','Leg2':'Leg 2','bothlegs':'both legs','25°C':'25 °C','24unique':'24 unique','28memberships':'28 memberships','55/63':'55 bottles/63 memberships','Stations16and18':'Stations 16 and 18','31and33':'31 and 33','Figure1':'Figure 1','Figure2':'Figure 2','FigureS1':'Figure S1','FigureS2':'Figure S2','MSE':'MSE','pHunits':'pH units','meanerror':'mean error','+0.211pH':'+0.211 pH','93.3%of totalMSE':'93.3% of total MSE','Blackdiamonds':'Black diamonds','coloredpoints':'colored points','GP15pH':'GP15 pH','REEfeatures':'REE features','GLODAPproduct':'GLODAP product','meanpH':'mean pH','Greyshading':'Grey shading','All12GP15primarylayer':'All 12 GP15 primary layer','Figures1and3':'Figures 1 and 3','diagnosticv2':'diagnostic v2','R8primary':'R8 primary','PNGfiles are450dpi':'PNG files are 450 dpi','SVGtext':'SVG text','Leg 1half-open':'Leg 1 half-open','criterionin':'criterion in'}
for a,b in replacements.items():captions=captions.replace(a,b)
for a,b in {'uses24':'uses 24','uses55':'uses 55','Leg 1layer;31':'Leg 1 layer; 31','Leg 2layer':'Leg 2 layer','at25':'at 25','Leg 2REE':'Leg 2 REE','is+0.211':'is +0.211','contributes93.3':'contributes 93.3'}.items():captions=captions.replace(a,b)
disclosure='Data plots were produced by reproducible Python code developed with Codex assistance.'
captionparts=captions.split('\n\n');captions='\n\n'.join(part+' '+disclosure if part.startswith('**Figure ') else part for part in captionparts)
(HERE/'CAPTIONS.md').write_text(captions)

# Supplementary tables retain the original results and add only reviewer diagnostics.
def table(headers,rr):return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']+['|'+'|'.join(map(str,row))+'|' for row in rr])+'\n\n'
old_layers=rows(TASK/'results/ree_ph_fixed_transfer_v1/layer_summary.csv')
agg=json.loads((TASK/'results/ree_ph_fixed_transfer_v1/aggregate_summary.json').read_text())['summaries']
si='# Supplementary tables\n\n'
si+='**Table S1. Original dataset versions and primary sample support.** pH is total scale at25°C. Layer memberships can repeat boundary bottles and are not independent samples.\n\n'
si+=table(['Leg','REE dataset/version','pH dataset/version','Unique bottles','Stations','Memberships'],[['1','BCO-DMO932161v1','BCO-DMO838157v2',24,6,28],['2','BCO-DMO932559v1','BCO-DMO838173v2',55,8,63]])
si+='**Table S2. All original fixed aggregation comparisons.** Errors are in pH units and rounded for presentation; full precision remains in the original result CSV/JSON. ΔRMSE=model minus fixed-depth baseline. Leg1half-open has only five layers meeting the three-station criterion, although all six remain in the descriptive aggregate.\n\n'
names={'primary':'Primary','half_open':'Half-open bins','ree_reported_depth':'REE-reported depth','station_equal':'Station equal'}
si+=table(['Leg','Convention','REE RMSE','Baseline RMSE','ΔRMSE'],[[r['leg'][-1],names[r['variant']],f"{r['model_rmse']:.4f}",f"{r['baseline_rmse']:.4f}",f"{r['delta_rmse_model_minus_baseline']:+.4f}"] for r in agg])
si+='**Table S3. Original feature-domain description of primary means.** Alpha is in10⁻⁸per atomic-number unit; original training range2.5649–3.2036. Domain labels describe alpha only, not joint validity. No subset is rescored. Reference pH is the original GLODAP product average, without a direct-measurement-only restriction.\n\n'
by={(r['series'],int(r['bin_index'])):r for r in domain}
rr=[]
for i in range(6):
    r1,r2=by['leg1',i],by['leg2',i];a1,a2=float(r1['mean_alpha'])*1e8,float(r2['mean_alpha'])*1e8
    pos=lambda a:'Below' if a<lo else 'Above' if a>hi else 'Within'
    rr.append([f'{200+i*100}–{300+i*100}',f'{a1:.4f}',pos(a1),f"{float(r1['mean_observed_ph']):.3f}",f'{a2:.4f}',pos(a2),f"{float(r2['mean_observed_ph']):.3f}"])
si+=table(['Depth m','Leg1alpha','Position','Leg1pH','Leg2alpha','Position','Leg2pH'],rr)
si+='**Table S4. Station × primary-depth-layer support.** Entries are eligible bottle memberships, including explicit zeros. Asterisks mark stations represented in every layer. Inclusive boundaries allow one bottle in two neighboring columns. Totals therefore sum memberships, whereas the final column counts unique bottles. No area weighting is implied.\n\n'
for leg in ('leg1','leg2'):
    ss=[r for r in support if r['leg']==leg];rr=[[r['station']+('*' if r['all_six_layers']=='True' else '')]+[r[f'n_{v}_{v+100}'] for v in range(200,800,100)]+[r['n_unique_bottles']] for r in ss]
    rr.append(['Totals']+[sum(int(r[f'n_{v}_{v+100}']) for r in ss) for v in range(200,800,100)]+[sum(int(r['n_unique_bottles']) for r in ss)])
    si+='Leg '+leg[-1]+'\n\n'+table(['Station']+[f'{v}–{v+100}' for v in range(200,800,100)]+['Unique bottles'],rr)
si+='**Table S4 continued. Recorded coordinate ranges for eligible primary bottles.** Coordinates come from the REE source CSV; negative latitude is south and negative longitude west. Ranges retain source precision and are positions of this sample support, not area boundaries. Rounded pH-file coordinates and row-level provenance remain in station_geometry.json.\n\n'
rr=[]
for g in geo:
    fmt=lambda pair:f'{pair[0]:.3f}' if pair[0]==pair[1] else f'{pair[0]:.3f} to {pair[1]:.3f}'
    rr.append([g['leg'][-1],g['station'],fmt(g['latitude_range']),fmt(g['longitude_range'])])
si+=table(['Leg','Station','Latitude °N','Longitude °E'],rr)
si+='**Table S5. Descriptive decomposition of six-layer error.** Mean error=prediction minus measured GP15pH. MSE=(mean error)²+centeredMSE. CenteredRMSE is an after-observation diagnostic; it does not represent a validated correction or revised performance. Allsix original means have equal weight.\n\n'
si+=table(['Leg','Prediction','RMSE','Mean error','Centered RMSE','Mean-error²/MSE %'],[[r['leg'][-1],'REE' if r['predictor']=='REE_model' else 'Depth baseline',f"{float(r['rmse']):.4f}",f"{float(r['mean_error']):+.4f}",f"{float(r['centered_rmse']):.4f}",f"{100*float(r['squared_mean_error_fraction_of_mse']):.1f}"] for r in mse])
si+='**Table S6. Post-review common-station diagnostic.** Intersecting station labels across allsix primary inclusive layers leaves onlytwo perleg. Within each layer, station means receive equal weight; six layers then receive equal weight. This exploratory diagnostic cannot replace the primary estimand, recover missing stations, demonstrate regional representativeness, or create independent validation. No interval or pvalue is assigned.\n\n'
si+=table(['Leg','Stations','Unique bottles','Memberships','REE RMSE','Baseline RMSE','ΔRMSE'],[[r['leg'][-1],', '.join(r['stations']),r['n_unique_bottles'],r['layer_memberships'],f"{r['model']['rmse']:.4f}",f"{r['baseline']['rmse']:.4f}",f"{r['delta_rmse_model_minus_baseline']:+.4f}"] for r in common])
case=summary['sample14984'][0]
si+='**Table S7. Unresolved reported-depth conflict for sample14984.** The same sample ID, station, event and normalized starttime agree across records. All five requiredflags are2. REEdepth360m versuspHdepth261m changes the assigned100mlayer; identity agreement does not determine the true depth. The primary uses the predeclared pH-depth convention for consistent pairing to the pH target, without claiming it is physically superior; the original REE-depth sensitivity covers an alternative recorded assignment and preserves aggregate ordering.\n\n'
si+=table(['Field','REE record','pH record'],[['Dataset/version','932559v1','838173v2'],['Physical CSV line',case['ree_csv_line'],case['ph_csv_line']],['Sample ID','14984','14984'],['Station / event','31 / 6912','31 / 6912'],['Reported depth m','360','261'],['Start UTC',case['ree_time'],case['ph_time']],['Required flags','Sm/Gd/Dy/Er=2','pH=2'],['Assigned inclusive layer m','REE-depth300–400m','pH-depth200–300m']])
si+='There are53nonzero depth conflicts among79unique primary bottles (58among84eligibleunion pairs); the maximum is99m in both scopes and also among all173identity-matched nonzero conflicts. All84eligibleunion timestamps agree afterISO normalization (Leg1:26, Leg2:58), including the79primarypairs (24,55). No timestamp exclusion was added. Full conflicts and source hashes remain in depth_conflicts.csv and summary.json.\n\n'
si+='**Table S8. Exact common-concentration illustration for the original six training alpha values.** LetA=mean of four normalized concentrations ands=their OLSslope after dividing each byA. Thenalpha=A×s. Multiplying all four concentrations by1.10 leavesrelative shapeunchanged and changes the fixed prediction by0.10alpha/beta. This is an algebraic sensitivity, not evidence for the cause of GP15 residuals.\n\n'
si+=table(['Training depth m','Training alpha ×10⁸','Δpredicted pH at+10%'],[[f"{r['low_m']}–{r['high_m']}",f"{float(r['original_training_alpha'])*1e8:.4f}",f"{float(r['delta_predicted_ph']):.4f}"] for r in training])
si+='Bottle-levelA,sandalpha and both record-weighted/station-equal layer descriptions are retained in abundance_shape_bottles.csv and abundance_shape_layers.csv. For weighted layer means, mean(alpha)=mean(A)×s_of_mean_composition; in general mean(alpha)=mean(A)×mean(s)+covariance(A,s), so multiplying separately averaged Aands is insufficient. These algebraic components do not independently identify concentration supply, mixing, complexation or particle exchange.\n\n'
si+='## Source and analysis status\n\nTablesS1–S3 retain original R8 results with presentation rounding. TablesS4–S8 derive from the root-frozen post-review diagnosticv2 and are explicitly exploratory/descriptive. Raw data, original frozen outputs and fixed model coefficients were not changed. Exact SHA, source-file locators and derived field mappings are in the adjacent figure-source-manifest.json. Full precision remains in the sourceCSV/JSON files.\n'
repl={'25°C':'25 °C','BCO-DMO932161v1':'BCO-DMO 932161 v1','BCO-DMO838157v2':'BCO-DMO 838157 v2','BCO-DMO932559v1':'BCO-DMO 932559 v1','BCO-DMO838173v2':'BCO-DMO 838173 v2','Leg1':'Leg 1','Leg2':'Leg 2','Leg 1half-open':'Leg 1 half-open','in10⁻⁸per':'in 10⁻⁸ per','range2.':'range 2.','GP15pH':'GP15 pH','centeredMSE':'centered MSE','CenteredRMSE':'Centered RMSE','Allsix':'All six','allsix':'all six','onlytwo perleg':'only two per leg','pvalue':'p value','sample14984':'sample 14984','starttime':'start time','requiredflags are2':'required flags are 2','REEdepth360m versuspHdepth261m':'REE depth 360 m versus pH depth 261 m','assigned100mlayer':'assigned 100 m layer','REE-depth300–400m':'REE depth 300–400 m','pH-depth200–300m':'pH depth 200–300 m','53nonzero':'53 nonzero','among79unique':'among 79 unique','58among84eligibleunion':'58 among 84 eligible union','maximum is99m':'maximum is 99 m','all173identity-matched':'all 173 identity-matched','All84eligibleunion':'All 84 eligible union','afterISO':'after ISO','the79primarypairs':'the 79 primary pairs','LetA':'Let A','ands=':'and s =','OLSslope':'OLS slope','byA':'by A','Thenalpha':'Then alpha','by1.10':'by 1.10','leavesrelative shapeunchanged':'leaves relative shape unchanged','by0.10alpha/beta':'by 0.10 alpha/beta','Bottle-levelA,sandalpha':'Bottle-level A, s and alpha','Aands':'A and s','TablesS1–S3':'Tables S1–S3','TablesS4–S8':'Tables S4–S8','diagnosticv2':'diagnostic v2','sourceCSV/JSON':'source CSV/JSON','Leg 1alpha':'Leg 1 alpha','Leg 2alpha':'Leg 2 alpha','Leg 1pH':'Leg 1 pH','Leg 2pH':'Leg 2 pH','932559v1':'932559 v1','838173v2':'838173 v2'}
for a,b in repl.items():si=si.replace(a,b)
for a,b in {'at25':'at 25','There are53':'There are 53','Leg 1:26':'Leg 1: 26','Leg 2:58':'Leg 2: 58','(24,55)':'(24, 55)','A=mean':'A = mean','s =their':'s = their','Then alpha=A×s':'Then alpha = A × s','at+10%':'at +10%'}.items():si=si.replace(a,b)
supp=HERE/'generated-supplementary.v3.en.md';supp.write_text(si)
manifest={'schema':'gp15-revision-figures/v3','scientific_calculation_scope':'Rendering and tabulating already frozen results only; no new model or sample selection',
 'source_sha256':source_records,'frozen_diagnostic_manifest':{'path':str((DIAG/'run_manifest.json').relative_to(TASK)),'sha256':sha(DIAG/'run_manifest.json')},
 'field_mappings':[
 {'figure':'Figure1','coordinate_source':'station_geometry.json','fields':['representative_longitude_for_map','representative_latitude_for_map'],'support_source':'station_layer_support.csv','counts':'n_200_300 ... n_700_800; all14stations×6bins including0','row_locators':'station_layer_support_long.csv sample_ids and originalCSVlines'},
 {'figure':'Figure2','source':'original fig01_source_values.csv','x_fields':['observed_ph','predicted_ph','baseline_ph'],'y':'midpoint(low_m,high_m)','selection':'all12primarymeans unchanged'},
 {'figure':'Figure3','source':'mse_decomposition.csv','base_height':'squared_mean_error','stack_height':'centered_mse','annotation':'rmse; Leg2REE mean_error and squared_mean_error_fraction_of_mse'},
 {'figure':'FigureS1','reuse':'all8fixedpointsandprimarydeletionranges redrawnfrombyte-identicalsourceCSV; no numeric changes'},
 {'figure':'FigureS2','source':'original fig03_source_values.csv','coordinates':'all18originaltraining/GP15rows unchanged','label_change':'trainingreferencepH distinguished from measuredGP15'}],
 'script':{'path':str(Path(__file__).resolve().relative_to(TASK)),'sha256':sha(Path(__file__))},
 'environment':{'python':sys.version,'matplotlib':matplotlib.__version__,'numpy':np.__version__},
 'outputs':[{'path':str(p.relative_to(TASK)),'sha256':sha(p),'bytes':p.stat().st_size} for p in outputs+[HERE/f for f in ['fig01_source_values.csv','fig02_source_values.csv','fig03_source_values.csv','figS01_source_values.csv','figS02_source_values.csv','CAPTIONS.md']]+[supp]],
 'presentation_changes_from_v2':['RemovedFigure1supertitle; retainedA/B/Cpanelheaders','Tightenedlongitudeaxisfrom159–148.8Wto156.9–150.6Waroundrecordedpoints','Added5vectorPDFswithTrueType42embeddedfontsanddeterministicmetadata; heatmaps usevectorcellpaths ratherthanrasterimages','AddedCodexplottingcodedisclosuretoeachof5captions','RedrewFigureS1fromunchanged8rowcoordinate/rangetableforconsistentPDFoutput','Preservedallv2filesandoriginalscientificdata'],'precision':'CSV source values preserve full computational precision; manuscript tables3–4decimalpH andoriginalunits are presentation rounding',
 'uncertainty':'No new CI or errorbar. Figure3centered residual is descriptive, not bias-correctedvalidation.'}
dump(HERE/'figure-source-manifest.json',manifest)
print(json.dumps({'main_figures':3,'supplementary_figures':2,'supplementary_tables':8,'figure_manifest_sha256':sha(HERE/'figure-source-manifest.json'),'script_sha256':sha(Path(__file__))},indent=2))
