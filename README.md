# IN-14: BESS placement and sizing for frequency regulation (PSS®E)

Scripts, network data and simulation results for the paper

> **Frequency Regulation of 14-Bus 220 kV System Using Optimally Placed and Sized Battery Energy Storage**
> Ateeq Ur Rahman, Dayananth, Harish V, Sri Harikrishnan, Dr. Kavitha — Department of Electrical and Electronics Engineering, Amrita Vishwa Vidyapeetham, Coimbatore, India.

IN-14 is a 14-bus, single-voltage 220 kV, 50 Hz test system built to Indian grid practice (IEEE 14-bus topology,
ACSR Zebra lines, 210 MW coal units). A particle swarm optimisation (PSO) calls PSS®E dynamic simulation for every
candidate BESS design and optimises bus, MW rating and droop against fault-initiated unit trips and the loss of the
BESS's own substation. The BESS uses the WECC generic models REGCA1–REECC1–REPCA1 with state-of-charge tracking.

## Contents

| Folder | What it holds |
|---|---|
| `scripts/in14_psse_bess_final.py` | Main script: builds the RAW/DYR cases, runs the contingencies, SOC sizing and the PSO (single or split BESS) |
| `scripts/in14_psse_bess_extra.py` | Adds the conventional case, ride-through study and contingency-set stages; used by the review batch |
| `scripts/in14_review2.py` | Batch of all studies in the revised paper (10 seeds per configuration, weight sensitivity, droop sweep, ride-through bands, penetration levels, conventional system) |
| `scripts/in14_psse_bess_parallel.py` | Runs several PSO seeds at the same time |
| `cases/` | PSS®E RAW (rev. 33) and DYR files: renewable case without BESS, with the final split BESS, and with the single BESS (seed 1) |
| `results/final_runs/` | Results of the first final PSO runs (summary.json, PSO log, time series) |
| `results/review_runs/` | Results of every study of the revised paper, one folder per job |

Each result folder contains `summary.json` (design, energy, worst-case metrics, per-contingency results), `pso_log.csv`
(every evaluated design) and CSV time series (`BESS.csv`, `noBESS.csv`, `BESS_SOC.csv`) where applicable.

## Requirements

* **PSS®E 36** (developed with 36.7 Xplore). PSS®E is commercial software from Siemens and is **not** included.
* The Python version that matches your PSS®E `PSSPY3x` folder (e.g. Python 3.14 for `PSSPY314`).
* `numpy` and `matplotlib` (optional, for plots): `pip install -r requirements.txt`

## How to run

1. Put all files of `scripts/` in one folder.
2. If needed, set `PSSE_PSSPY` at the top of the script to your PSS®E `PSSPY3x` folder (the review batch searches for it).
3. Either drag the script into the PSS®E window, or from a Command Prompt:
   ```
   py -3.14 in14_review2.py
   ```
   `in14_review2.py` runs 4 jobs at a time from a Command Prompt (about 1 h) or one after another inside PSS®E.
   Set `RUN_ONLY = ['droop_sweep']` (for example) to run selected studies only.
4. Results are written to `IN14_REVIEW2/` and zipped into `IN14_REVIEW2_ALL.zip`.

Main settings: time step 2.5 ms, 10 s per run, 3-phase fault at 0.5 s cleared at 0.6 s, loads converted to constant
current (P) / constant admittance (Q); PSO with 12 particles × 20 iterations, seeds 1–10.

## Citation

If you use this code or data, please cite the paper (citation details will be added after publication).

## License

Code and data: MIT License (see `LICENSE`).
