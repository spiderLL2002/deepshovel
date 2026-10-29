# Primary layer scoring values

Existing already-exposed data: deterministic presentation and reviewer QC only; post-exposure descriptive work, not new validation, inference, calibration, fitting, or changed eligibility/weighting.

The score is RMSE of six equally weighted layer mean errors. It is not RMSE of individual bottle errors. Each primary within-layer mean uses equal bottle weights. Inclusive-boundary memberships can repeat bottles. All pH values are total scale at 25 °C; signed errors are prediction minus measured pH.

## leg1

|Layer m|Bottles/stations|Measured pH|REE pH|Reference pH|REE error|Reference error|
|---|---:|---:|---:|---:|---:|---:|
|200–300|5/4|7.770460|7.827262|7.730879|0.056802|-0.039581|
|300–400|6/5|7.657633|7.718414|7.689871|0.060781|0.032237|
|400–500|6/5|7.530350|7.550065|7.658636|0.019715|0.128286|
|500–600|3/3|7.414267|7.543944|7.631553|0.129677|0.217286|
|600–700|5/4|7.365420|7.405490|7.606188|0.040070|0.240768|
|700–800|3/3|7.362233|7.424269|7.584326|0.062036|0.222092|

## leg2

|Layer m|Bottles/stations|Measured pH|REE pH|Reference pH|REE error|Reference error|
|---|---:|---:|---:|---:|---:|---:|
|200–300|16/7|7.696456|7.968543|7.730879|0.272086|0.034422|
|300–400|14/7|7.639750|7.876788|7.689871|0.237038|0.050121|
|400–500|17/7|7.514182|7.786329|7.658636|0.272146|0.144454|
|500–600|7/5|7.520929|7.702594|7.631553|0.181665|0.110624|
|600–700|5/4|7.436140|7.625485|7.606188|0.189345|0.170048|
|700–800|4/3|7.499050|7.611826|7.584326|0.112776|0.085276|

CSV values retain original stored numeric tokens; Markdown is rounded to six decimals. Source: results/ree_ph_fixed_transfer_v1/layer_summary.csv, variant=primary.
