"""
This script estimates pH of marine carbonates using linear regression
with an input spreadsheet of REE concentrations.
"""
from math import sqrt
import pandas as pd
import matplotlib.pyplot as plt
import scipy.stats as sp
import numpy as np
from sklearn.metrics import r2_score
pd.set_option('display.max_rows', None)


def load_REE(file_path):
    """
    This function reads the input REE concentration data from rock records.
    """
    # REE concentration starts as ug REE /g ALL(ppm) format [g/g]
    REE_in_rocks = pd.read_csv(file_path)
    REE_in_rocks = REE_in_rocks[['La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu', 'Gd',
                                'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu']]
    REE_in_rocks = REE_in_rocks*10**-6  # REE_mf to g/g
    return REE_in_rocks


def load_coeff(DREE_path, atomic_num_path, molar_mass_path, paas_path):
    """
    This function reads the input coeffcients for REEs from spreadsheets.
    """
    # DREE (partitioning constants) [mol/mol, no unit]
    D_REE = pd.read_csv(DREE_path)  # (mol/mol)
    D_REE = D_REE[['La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu', 'Gd',
                   'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu']]
    # REE atomic number [amu]
    atomic_num = pd.read_csv(atomic_num_path)
    # REE molar mass [g/mol]
    molar_mass = pd.read_csv(molar_mass_path)
    molar_mass = molar_mass*10**-3  # mg to g
    # PAAS (ug REE /g ALL(ppm)) [mol/g] from Pourmand et al., 2012
    paas = pd.read_csv(paas_path)  # ug/g
    paas = paas*10**-6  # paas to g/g
    paas_mol = paas/molar_mass.loc[0]  # paas to mol
    paas_mol = paas_mol[['La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu',
                        'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu']]
    # Variables
    M_Ca = 0.01*10**-3  # mol/g
    X_Ca = 1
    n_Ca = M_Ca/X_Ca
    coeffs = [D_REE, atomic_num, molar_mass, paas_mol, n_Ca]
    return coeffs


def preprocess_data(REE_input, coeffs):
    """
    This function reads the coeffcients from csvs and creates a PAAS normalized
    REE dataset for regression analysis.
    """
    # molar fraction of REE
    D_REE, atomic_num, molar_mass, paas_mol, n_Ca = coeffs
    n_REE = REE_input/molar_mass.loc[0]  # mols
    n_REE = n_REE[['La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu', 'Gd', 'Tb',
                   'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu']]
    molar_mass_caco3 = 100.0869  # g/mol
    n_total = 1/molar_mass_caco3  # 1/100.0869 mol of caco3

    X_REE = n_REE/n_total
    D_REE = D_REE.to_numpy()
    paas_mol = paas_mol.to_numpy()
    m_REE = (X_REE/D_REE)*n_Ca  # mol/g
    m_REE = m_REE/paas_mol

    selected = ['Sm', 'Gd', 'Dy', 'Er']
    mREE_selected = m_REE[selected]

    return mREE_selected


# REE-pH proxy from REE_pH_proxy.py
def fixed_proxy():
    """
    This function fixes the beta parameter in the REE-proxy equation
    """
    beta_coeff = [-3.9879452461785675e-08, 3.339192211653703e-07]
    return beta_coeff


def perform_linear_regression(REE_proxy):
    """
    This function performs regression (Equation 5) and produces
    pH estimates for all the samples from the input data.
    The pH estimates are plotted as two figures and compared to
    the REE-pH proxy.
    """
    # REE slope values by linear regression
    beta_coeff = fixed_proxy()
    all_pH_esimtates = []
    all_slopes = []
    all_pH_err = []
    all_RS_err = []
    for n in range(0, len(REE_proxy)):
        slope, intercept, r, p, se = sp.linregress([62, 64, 66, 68],
                                                   REE_proxy.iloc[n])
        pH = (slope-beta_coeff[1])/beta_coeff[0]
        x_err_bar = se * 2
        y_err_bar = x_err_bar/abs(beta_coeff[0])
        all_pH_esimtates.append(pH)
        all_slopes.append(slope)
        all_pH_err.append(y_err_bar)
        all_RS_err.append(x_err_bar)
        print(round(pH, 2), slope, x_err_bar, y_err_bar)

    # average REE pH estimate
    avg_slope, avg_int, avg_r, avg_p, avg_se = sp.linregress([62, 64, 66, 68],
                                                             REE_proxy.mean())
    avg_pH = (avg_slope-beta_coeff[1])/beta_coeff[0]
    avg_x_err_bar = avg_se * 2
    avg_y_err_bar = avg_x_err_bar/abs(beta_coeff[0])

    print("Average:", round(avg_pH, 2))
    print("Average pH err bar 2 sig:", avg_y_err_bar)

    # statisics fo the box plot of the data
    pH_estimates = np.array(all_pH_esimtates)
    pH_estimates = pH_estimates[~np.isnan(pH_estimates)]
    q1, q2, q3 = np.percentile(pH_estimates, [25, 50, 75])

    # the Avg_pH data
    plt.scatter(1, avg_pH, marker='x', label="avg_pH")
    plt.errorbar(1, avg_pH, yerr=avg_y_err_bar, fmt="x", capsize=10)

    plt.boxplot(pH_estimates)
    plt.legend()
    plt.show()

    print("[Q1 Q2 Q3]:", [q1, q2, q3])
    iqr = sp.iqr(pH_estimates)
    print("IQR:", iqr)
    avg = np.mean(pH_estimates)
    print("Mean:", avg)
    sd = sp.tstd(pH_estimates)
    print("2 SD: ", 2*sd)
    print("2 SME:", 2*sd/sqrt(np.count_nonzero(pH_estimates)))

    # Tukey's fences outlier filter
    pH_estimates_filtered = []
    for sample in pH_estimates:
        if (sample < (q3+1.5*iqr)) & (sample > (q1-1.5*iqr)):
            pH_estimates_filtered.append(sample)
    pH_estimates_filtered = np.array(pH_estimates_filtered)
    print("Filtered mean: (w/o outliers) ", np.mean(pH_estimates_filtered))
    print("Filtered 2*SD (w/o outliers):", 2*sp.tstd(pH_estimates_filtered))
    print("Filtered 2*ME (w/o outliers):",
          2 * sp.tstd(pH_estimates_filtered) /
          sqrt(np.count_nonzero(pH_estimates)))
    print("Filtered median (w/o outliers) :", np.median(pH_estimates_filtered))

    # pH prediction plots
    # REE slope values by linear regression

    line_x = np.linspace(6.6, 9, 200)

    r_slope = np.array(all_slopes)
    slope_se = np.array(all_RS_err)
    r_pH = np.array(all_pH_esimtates)
    pH_se = np.array(all_pH_err)

    y = r_slope
    x = r_pH
    print((sp.pearsonr(x, y)[0])**2)  # pearson R coeff
    print("R^2:", r2_score(x, y))

    fig, ax = plt.subplots()
    ax.plot(line_x, beta_coeff[0] * line_x + beta_coeff[1],
            color='gray', linestyle='--')
    ax.scatter(x, y)
    ax.errorbar(x, y, xerr=pH_se, yerr=slope_se, fmt="o")
    fig.tight_layout()
    plt.xlabel('pH', size=14)
    plt.ylabel('REE slope', size=14)
    plt.show()


def main():
    REE_in_rocks = load_REE('REE_data/REE_limestone_toyama_noFJ1YK1_ugg.csv')
    coeffs = load_coeff('coeff/DREE_toyama.csv',
                        'coeff/atomic_number.csv',
                        'coeff/molar_mass_mgmol.csv',
                        'coeff/paas.csv')
    perform_linear_regression(preprocess_data(REE_in_rocks, coeffs))


if __name__ == "__main__":
    main()
