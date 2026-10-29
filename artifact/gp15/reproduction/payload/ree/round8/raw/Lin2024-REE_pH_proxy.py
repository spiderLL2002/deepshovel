"""
This code produces the REE-pH proxy with linear regression from REE slopes
using modern seawater from GLODAPv2 (2021) for pH data and GEOTRACES (2021)
for REE measurements in seawater.
"""
import math
import pandas as pd
import matplotlib.pyplot as plt
import scipy.stats as sp
import numpy as np
import geopandas as gpd
pd.set_option('display.max_rows', None)

# import data
df = pd.read_csv('pH_data/GLODAPv2.2022_Merged_Master_File.csv')
# parameters
max_depth = 800
min_depth = 200
layer_size = 100
coord = [-180, 180, -90, 90]  # long_min long_max lat_min lat_max

long_min = coord[0]
long_max = coord[1]
lat_min = coord[2]
lat_max = coord[3]
shallow_depth = (df['G2depth'] >= min_depth) & (df['G2depth'] <= max_depth)
valid_pH = df['G2phts25p0'] != -9999
long_range = (df['G2longitude'] >= long_min) & (df['G2longitude'] <= long_max)
lat_range = (df['G2latitude'] >= lat_min) & (df['G2latitude'] <= lat_max)
location = long_range & lat_range
df = df.fillna(-9999)
pH_in_range = df[shallow_depth & valid_pH & location][['G2phts25p0',
                                                       'G2depth',
                                                       'G2longitude',
                                                       'G2latitude',
                                                       'G2cruise']]
layer_pH_avgs = []
layer_pH_sme = []
for layer in range(min_depth, max_depth, layer_size):
    layer_range = ((pH_in_range['G2depth'] >= layer) &
                   (pH_in_range['G2depth'] <= (layer + layer_size)))
    layer_df = pH_in_range[layer_range]
    print("average pH of ", layer, "-", layer+layer_size, "m: ",
          layer_df['G2phts25p0'].mean())
    layer_pH_avgs.append(layer_df['G2phts25p0'].mean())
    layer_pH_sme.append(2 * layer_df['G2phts25p0'].std() /
                        math.sqrt(layer_df['G2phts25p0'].count()))
# Plot
plt.plot(layer_pH_avgs, range(min_depth+int(layer_size/2),
         max_depth+int(layer_size/2), layer_size), 'o')
plt.errorbar(layer_pH_avgs, range(min_depth+int(layer_size/2),
             max_depth+int(layer_size/2), layer_size),
             xerr=layer_pH_sme, yerr=layer_size/2,
             fmt="o", capsize=5, color="C0")
plt.xlabel('pH')
plt.ylabel('Depth (m)')
plt.gca().invert_yaxis()
plt.show()

# pH measurement locations
fig, ax = plt.subplots(figsize=(10, 5))
countries = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"))
countries.plot(color="lightgrey", ax=ax)
plt.plot(pH_in_range['G2longitude'].tolist(),
         pH_in_range['G2latitude'].tolist(), '.', color="C0")
plt.show()

# REE slope vs depth & REE conc vs depth
# REE conc. in seawater [mol/g]
m_REE = pd.read_csv('REE_data/REE_seawater_all_pmol.csv')

# Filters
shallow_depth = (m_REE['depth'] >= min_depth) & (m_REE['depth'] <= max_depth)
long_range = ((m_REE['longitude'] >= long_min) &
              (m_REE['longitude'] <= long_max))
lat_range = (m_REE['latitude'] >= lat_min) & (m_REE['latitude'] <= lat_max)
location = long_range & lat_range

m_REE = m_REE[shallow_depth & location]
m_REE_labels = m_REE


REE_list = ['La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu', 'Gd',
            'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu']
m_REE = m_REE[REE_list]
m_REE = m_REE*10**-15  # mREE to mol/g

# REE atomic number [amu]
atomic_num = pd.read_csv('coeff/atomic_number.csv')

# REE molar mass [g/mol]
molar_mass = pd.read_csv('coeff/molar_mass_mgmol.csv')
molar_mass = molar_mass*10**-3  # mg to g

# PAAS (ug REE /g ALL(ppm)) [mol/g]
paas = pd.read_csv('coeff/paas.csv')  # ug/g
paas = paas*10**-6  # paas to g/g
paas_mol = paas/molar_mass.loc[0]  # paas to mol
paas_mol = paas_mol[REE_list]

# REE conc. in seawater / PAAS [mol/g]/[mol/g] = unitless
m_REE = m_REE.div(paas_mol.loc[0])

# REE slope calc. by lingress slope
selected = ['Sm', 'Gd', 'Dy', 'Er']
labels = ['location']
depth = ['depth']

# REE slope values by linear regression
all_slopes = []
lst = []
for n in range(0, len(m_REE[selected])):
    slope, intercept, r, p, se = sp.linregress([62, 64, 66, 68],
                                               m_REE[selected].iloc[n])
    x_err_bar = se * 2  # uncertainty is 2 sigma, 98% confidence level
    lst.append([m_REE_labels['location'].iloc[n],
                m_REE_labels['depth'].iloc[n], slope,
                x_err_bar, m_REE_labels['latitude'],
                m_REE_labels['longitude'],
                m_REE['Sm'].iloc[n], m_REE['Gd'].iloc[n],
                m_REE['Dy'].iloc[n], m_REE['Er'].iloc[n]])
    all_slopes.append(slope)

slope_df = pd.DataFrame(lst, columns=['location',
                                      'depth', 'REE_slope', 'slope_error',
                                      'latitude', 'longitude',
                                      'Sm', 'Gd', 'Dy', 'Er'])


location_lst = slope_df['location'].unique().tolist()
loc_i = 0  # start of subset stations
loc_f = len(location_lst)  # end of subset stations

# Finding average REE slope for each layer (parameters in 1st kernel)
layer_slope_avgs = []
layer_slope_error_avgs = []
layer_Sm_avgs = []
layer_Gd_avgs = []
layer_Dy_avgs = []
layer_Er_avgs = []
layer_depth_avgs = []
layer_sme = []
layer_sample_size = []
layer_missing = []
for layer in range(min_depth, max_depth, layer_size):
    layer_range = ((slope_df['depth'] >= layer) &
                   (slope_df['depth'] <= (layer + layer_size)))
    in_loc_lst = (slope_df['location'].isin(location_lst[loc_i:loc_f]))
    layer_df = slope_df[layer_range & in_loc_lst]
    print("average REE_slope of ", layer, "-",
          layer + layer_size, "m: ", layer_df['REE_slope'].mean())
    layer_slope_avgs.append(layer_df['REE_slope'].mean())
    layer_slope_error_avgs.append(layer_df['slope_error'].mean())
    layer_Sm_avgs.append(layer_df['Sm'].mean())
    layer_Gd_avgs.append(layer_df['Gd'].mean())
    layer_Dy_avgs.append(layer_df['Dy'].mean())
    layer_Er_avgs.append(layer_df['Er'].mean())
    layer_depth_avgs.append(str(int(layer)) + 'm - ' +
                            str(int(layer + layer_size)) + 'm')
    if layer_df['REE_slope'].count() != 0:
        layer_sme.append(2*layer_df['REE_slope'].std() /
                         math.sqrt(layer_df['REE_slope'].count()))
    else:
        layer_sme.append(2*layer_df['slope_error'].mean())
    layer_sample_size.append(layer_df['REE_slope'].count())
    layer_missing.append(layer_df['REE_slope'].count() == 0)

plt.plot(layer_slope_avgs,
         range(min_depth+int(layer_size/2), max_depth+int(layer_size/2),
               layer_size), 'o', color="C0")
plt.errorbar(layer_slope_avgs,
             range(min_depth+int(layer_size/2), max_depth+int(layer_size/2),
                   layer_size),
             xerr=layer_sme, yerr=layer_size/2,
             fmt="o", capsize=5, color="C0")
plt.xlabel('Avg. REE_slope')
plt.ylabel('Depth (m)')
plt.gca().invert_yaxis()
plt.show()
layer_selected_avgs = pd.DataFrame(np.transpose(np.array([layer_Sm_avgs,
                                                          layer_Gd_avgs,
                                                          layer_Dy_avgs,
                                                          layer_Er_avgs])),
                                   columns=['Sm', 'Gd', 'Dy', 'Er'])

plt.figure(figsize=(5, 6), dpi=300)
plt.plot(np.transpose(layer_selected_avgs), 'o-')
plt.legend(layer_depth_avgs, fontsize=16)
plt.ylabel('REE conc./PAAS', size=16)
plt.xlabel('Atomic number (amu)', size=16)
plt.yticks(size=16)
plt.xticks([0, 1, 2, 3], ['62\n[Sm]', '64\n[Gd]', '66\n[Dy]', '68\n[Er]'],
           size=16)
plt.show()

# REE sample locations
fig, ax = plt.subplots(figsize=(10, 5))
countries = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"))
countries.plot(color="lightgrey", ax=ax)
plt.plot(slope_df['longitude'].tolist(),
         slope_df['latitude'].tolist(), '.', color='C0')
plt.show()


# Get Reg line model pH vs REE slope
# filter out nan
layer_pH_avgs = np.array(layer_pH_avgs)
layer_slope_avgs = np.array(layer_slope_avgs)
layer_sme = np.array(layer_sme)
mask = np.isfinite(layer_pH_avgs) & np.isfinite(layer_slope_avgs)
layer_pH_avgs = layer_pH_avgs[mask]
layer_slope_avgs = layer_slope_avgs[mask]
layer_sme = layer_sme[np.isfinite(layer_sme)]

# Filter out missing data (pH REE mismatch, morelikely REE data missing)
layer_pH_sme_valid = []
for index in range(0, len(layer_missing)):
    if layer_missing[index] == False:
        layer_pH_sme_valid.append(layer_pH_sme[index])


# linear regression prediction
def plot_ci_manual(t, s_err, n, x, x2, y2, ax=None):

    if ax is None:
        ax = plt.gca()
    ci = t * s_err * np.sqrt(1/n + (x2 - np.mean(x))**2 /
                             np.sum((x - np.mean(x))**2))
    ax.fill_between(x2, y2 + ci, y2 - ci, color="#b9cfe7", edgecolor="none")
    return ax


# Computations ----------------------------------------------------------------
x = layer_pH_avgs
y = layer_slope_avgs


# Modeling with Numpy
def equation(a, b):
    """Return a 1D polynomial."""
    return np.polyval(a, b)


p, cov = np.polyfit(x, y, 1, cov=True)
y_model = equation(p, x)

# Statistics
n = y.size
m = p.size
dof = n - m
t = sp.t.ppf(0.975, n - m)

# Estimates of Error in Data/Model
resid = y - y_model
chi2 = np.sum((resid / y_model)**2)
chi2_red = chi2 / dof
s_err = np.sqrt(np.sum(resid**2) / dof)

# Plotting --------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(5, 5), dpi=300)

# Data
ax.plot(
    x, y, "o", color="#b9cfe7", markersize=8,
    markeredgewidth=0, markeredgecolor="midnightblue",
    markerfacecolor="midnightblue"
)
# make sure REE length match pH length
plt.errorbar(layer_pH_avgs, layer_slope_avgs,
             xerr=layer_pH_sme_valid, yerr=layer_sme,
             fmt="o", capsize=5, color="midnightblue")

plt.xlabel('pH', size=16)
plt.ylabel('REE slope $\mathregular{α}$ ($\mathregular{amu^{-1}pH^{-1}}$)',
           size=16)
plt.xticks(size=14)
plt.yticks(size=16)

# Fit
ax.plot(x, y_model, "-", color="0.1", linewidth=2, alpha=0.5, label="Fit")

x2 = np.linspace(np.min(x), np.max(x), 100)
y2 = equation(p, x2)

# Confidence Interval
plot_ci_manual(t, s_err, n, x, x2, y2, ax=ax)
plt.show()
