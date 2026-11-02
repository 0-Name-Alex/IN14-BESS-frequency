"""
IN-14 / 220 kV + renewables + BESS  -  PSS(R)E 36: N-1-robust, SOC-aware BESS placement and sizing by PSO
=========================================================================================================
PSS(R)E does all power-flow and dynamic simulation. This script writes the RAW + DYR case for each
candidate design, runs the contingencies in PSS(R)E, scores the result and runs a PSO.

N_BESS = 1  -> single BESS: PSO chooses bus, MW rating and droop
N_BESS = 2  -> split BESS : PSO chooses two buses, two MW ratings and a shared droop

CONTINGENCIES (every design is tested against all of them; its worst result counts)
  1. Fault at bus 2, coal unit 2 trips          2. Fault at bus 12, solar park trips
  3. Fault at bus 1, coal unit 1 trips
  4. (+5 for a split BESS) busbar fault at EACH BESS's own 220 kV bus: busbar protection clears
     the whole substation - its lines, load, generator AND the BESS behind it (common-mode loss).
     Any bus or machine left islanded by the outage is tripped too.
  Losing a whole substation is conservative: Indian 220 kV substations usually have a double main
  bus with a bus coupler, so a busbar fault normally clears only one bus section.

STATE OF CHARGE (SOC)  - new in this version
  Every BESS starts at SOC_INI and must stay inside [SOC_MIN, SOC_MAX] for the whole simulation AND
  until AGC takes over (T_SECONDARY, assuming the BESS holds its end-of-run power until then).
  SOC(t) = SOC_INI - (1/E) * integral( P/ETA_DIS if discharging, P*ETA_CH if charging ) dt
  * Automatic sizing (E_FIXED_H = None): E is the smallest energy that keeps SOC inside the window
      E = max( discharge need / (SOC_INI - SOC_MIN),  charge need / (SOC_MAX - SOC_INI),  MIN_DURATION_H * P )
    With the defaults (SOC_INI 0.9, SOC_MIN 0.1, efficiencies 1.0) this reproduces the paper's sizing exactly
    (usable window 0.8 = the old 80 % depth of discharge).
  * Fixed energy (E_FIXED_H = e.g. 0.25): E = E_FIXED_H * P and leaving the SOC window becomes a PSO
    constraint violation, like the frequency limits.
  * BESS_EC_MODEL selects the BESS electrical controller:
      'REECA1'  : as in the paper. SOC is tracked in Python only. REECA1 cannot absorb power (Ipmin = 0
                  inside the model), so the BESS never charges - the negative PMIN written below has no effect.
      'REECC1'  : WECC storage controller (PSS(R)E library). Has its own SOC state with SOCmin/SOCmax limits
                  and allows charging (Ipmin = -Ipmax), so the BESS also helps in over-frequency events.
      'REECCU1' : same model as a user model (USRMDL), for PSS(R)E versions without REECC1 in the library.
    The REECC1/REECCU1 parameter order must be checked against your PSS(R)E Model Library before use -
    see REECC_ORDER_CHECKED.
  In a 10 s run the SOC moves by about 1 %, so the SOC limits can only bind through the AGC hand-over
  projection (or in a longer run: set T_END to 300 s or more in 'check' mode).

FINAL SETTINGS (this file) - result of the ripple diagnosis (runs diag 1-3)
  * Plant-level reactive-power PI of REPCA1 lowered from Kp = 18, Ki = 5 to Kp = 1, Ki = 0.5 for the BESS AND for
    the solar and wind plants. The high gains (WECC voltage-control example values) caused a ~6 Hz reactive-power /
    voltage oscillation in every inverter plant.
  * Time step 2.5 ms instead of 5 ms. At 5 ms a 12 Hz numerical ripple of ~50 MW appeared on the BESS output;
    it shrinks to ~11 MW at 2.5 ms and stays the same at 1 ms, so 2.5 ms is converged.
  * BESS active-power ripple is now checked: detrended peak-to-peak of the total BESS output in the last 3 s must stay
    below P_RIPPLE_MAX x total rating, otherwise the design is penalised like any other limit violation.
  MODE = 'diag' re-runs contingency R1 for the REECC1 split optimum to confirm the ripple is gone.

REECC1 VERSION
  The BESS uses the WECC storage controller REECC1, so it can both discharge (under-frequency) and
  CHARGE (over-frequency, e.g. after a busbar outage that removes load). SOC_INI is 0.5 so the battery has
  room in both directions; the energy is still set by the 15-min minimum (26 + 16.25 MWh for the split design).
  The REECC1 data line (5 ICONs + 45 CONs) follows a published PSS(R)E REECCU1 example:
    ICONs: BUSR PFFLAG VFLAG QFLAG PQFLAG
    CONs : Vdip Vup Trv dbd1 dbd2 Kqv Iqh1 Iql1 Vref0 Tp | Qmax Qmin Vmax Vmin Kqp Kqi Kvp Kvi |
           Tiq dPmax dPmin Pmax Pmin Imax Tpord | VDL1 (8) | VDL2 (8) | T SOCini SOCmax SOCmin
  CHECK ONCE in the PSS(R)E GUI after a 'check' run: open the dynamics data of machine 'B' at bus 15 and
  confirm the REECC1 values are under the right names (especially T = 818, SOCini = 0.5, SOCmax = 0.9,
  SOCmin = 0.1, PMIN = -0.909). If your PSS(R)E has no library model REECC1, the script retries
  with the user model REECCU1 automatically.

HOW TO RUN: inside PSS(R)E  File > Run Program Automation File...  -> choose this file.
  MODE = 'all'   -> runs everything in RUN_PLAN one after another (diagnosis, split PSO seeds 1-3, single PSO)
                    and zips the results into IN14_FINAL_ALL.zip next to this file (about 30-45 min in total)
  MODE = 'check' -> simulate the design in BESS_CHECK against all contingencies (about 1 minute)
  MODE = 'pso'   -> full PSO. Progress lines 'PSO iter ...' appear in the Output window.
Results: folder named in WORK_NAME next to this file (summary.json, pso_log.csv, CSVs incl. SOC, PNGs).

DYNAMIC MODELS: coal GENROU+SEXS+TGOV1, hydro GENSAL+SEXS+HYGOV,
  solar/wind REGCA1+REECA1+REPCA1 (no frequency response),
  BESS REGCA1+REECA1/REECC1(P priority)+REPCA1 with frequency droop; ride-through band 0.5-1.3 pu
  (the standard 0.9-1.1 band froze the BESS power command after faults and caused a limit cycle).
"""
import os, sys, json, math, random, time

# ============================================================================ USER SETTINGS
N_BESS = 2                            # 1 = single BESS (option 1), 2 = split BESS (option 2)
MODE = 'parallel'                     # 'parallel' (PARALLEL_PLAN, N_WORKERS at once), 'all' (RUN_PLAN one after another),
                                      # or a single 'diag', 'pso' or 'check' run
RUN_PLAN = [('diag', 2, 1),           # (mode, N_BESS, SEED) - executed in order when MODE = 'all'
            ('pso', 2, 1), ('pso', 2, 2), ('pso', 2, 3),
            ('pso', 1, 1)]
SEED = 1                              # PSO random seed - change it to test robustness of the optimum
# ---- parallel batch (MODE = 'parallel'): several PSS(R)E runs at the same time, one process each
PARALLEL_PLAN = [('pso', 2, s) for s in range(1, 11)] + [('pso', 1, s) for s in range(1, 11)]   # 10 seeds each
N_WORKERS = 4                         # runs at the same time; <= number of CPU cores (try 2 first)
N_PART, N_ITER = 12, 20
PSSE_PSSPY = r"C:\Program Files\PTI\PSSE36\36.2\PSSPY39"   # only used outside the PSS(R)E GUI

if N_BESS == 1:
    BESS_CHECK = dict(units=[dict(bus=9, P=180.0)], R=0.15)                          # paper: single optimum
    BOUNDS = [(1, 14.999), (10.0, 300.0), (0.15, 1.0)]                  # bus, P, droop
    WORK_NAME = 'IN14_FINAL_single_seed%d' % SEED
else:
    BESS_CHECK = dict(units=[dict(bus=5, P=104.0), dict(bus=13, P=65.0)], R=0.15)    # paper: split optimum
    BOUNDS = [(1, 14.999), (1, 14.999), (10.0, 200.0), (10.0, 200.0), (0.15, 1.0)]   # busA, busB, PA, PB, droop
    WORK_NAME = 'IN14_FINAL_split_seed%d' % SEED

T_FAULT, T_CLEAR, T_END, DELT = 0.5, 0.6, 10.0, 0.0025   # 2.5 ms: converged (5 ms gave a numerical ripple)
P_RIPPLE_MAX = 0.05                   # max detrended peak-to-peak BESS output ripple in the last 3 s, fraction of total rating
RE_QKP, RE_QKI = 1.0, 0.5             # REPCA1 plant Q-loop gains of the solar and wind plants (18/5 oscillated)
F_MIN = 49.5                          # Hz, worst COI or bus frequency (0.1 Hz above UFLS stage 1, 49.4 Hz)
ROCOF_MAX = 1.5                       # Hz/s, COI, 500 ms window
F_OVER_MAX = 50.10                    # Hz, no overshoot above this after an UNDER-frequency event (generation lost)
F_OVER_LOADLOSS = 51.5                # Hz, peak limit after an OVER-frequency event (load lost, e.g. busbar outage of
                                      # a load bus): generators must ride through up to 51.5 Hz (grid-code upper limit)
F_SWING_MAX = 0.05                    # Hz, max peak-to-peak COI oscillation in the last 3 s (after removing the
                                      # slow linear drift of governor recovery - drift is not an oscillation)
COST_PER_MW, COST_PER_MWH = 0.12, 0.18     # M$ (assumption - change to your data)
MIN_DURATION_H, T_SECONDARY = 0.25, 300.0  # h at rated P (shortest battery, 4C); s until AGC takes over
BESS_VDIP, BESS_VUP = 0.5, 1.3        # BESS ride-through band (REECA1 / REECC1)

# ---- state of charge
SOC_INI = 0.50                        # initial SOC: 0.5 = room to charge AND discharge (REECC1 does both)
SOC_MIN, SOC_MAX = 0.10, 0.90         # allowed SOC window (0.4 to discharge, 0.4 to charge)
ETA_DIS, ETA_CH = 1.00, 1.00          # one-way efficiencies; 0.95 / 0.95 gives about 90 % round trip
E_FIXED_H = None                      # None = size E from the SOC window; a number (h at rated P) fixes E and
                                      # makes the SOC window a PSO constraint
SOC_PENALTY_STEP = 0.05               # SOC excursion that counts as one unit of limit violation
BESS_EC_MODEL = 'REECC1'              # 'REECA1' (paper), 'REECC1' (library) or 'REECCU1' (user model)
REECC_ORDER_CHECKED = True            # order follows a published PSS(R)E REECCU1 line - still confirm once in the GUI
# ---- BESS control settings varied by MODE = 'diag'
REECC_KQV = 2.0                       # REECC1 reactive-current injection gain during voltage dips
REECC_QPI = (0.0, 0.1, 0.0, 40.0)     # REECC1 Kqp, Kqi, Kvp, Kvi (reactive-power / voltage PI gains)
BESS_KPG, BESS_TLAG = 1.0, 0.02       # REPCA1 active-power PI gain and output lag of the BESS
BESS_QKP, BESS_QKI = 1.0, 0.5         # REPCA1 plant reactive-power (Q) PI gains of the BESS (18/5 oscillated)
BESS_TG, BESS_ACCEL = 0.02, 0.7       # REGCA1 current-loop lag (s) and network-interface acceleration factor of the BESS
BESS_IMAX = 1.1                       # BESS converter current limit (pu on MBASE)
DIAG_DESIGN = dict(units=[dict(bus=5, P=111.6), dict(bus=6, P=74.5)], R=0.15)   # REECC1 split optimum (seed 1)

FIXED_CONT = [('Fault at bus 2, coal unit 2 trips', 2, 'machine', 2, '1'),
              ('Fault at bus 12, solar park trips', 12, 'machine', 12, '1'),
              ('Fault at bus 1, coal unit 1 trips', 1, 'machine', 1, '1')]

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, WORK_NAME)
F0, SBASE, KV = 50.0, 100.0, 220.0
BESS_BUS0 = 15                        # BESS units get buses 15, 16, ... (33 kV, own transformer)

# ============================================================================ NETWORK DATA
NAMES = {1: 'TPS-A', 2: 'TPS-B', 3: 'GAS-C', 4: 'SS-D', 5: 'SS-E', 6: 'HEP-F', 7: 'SS-G', 8: 'HEP-H',
         9: 'SS-I', 10: 'SS-J', 11: 'SS-K', 12: 'SOLAR-L', 13: 'SS-M', 14: 'WIND-N'}
LOADS = [(2, 54, 18), (3, 150, 49), (4, 120, 39), (5, 30, 10), (6, 28, 9),
         (9, 74, 24), (10, 22, 7), (11, 9, 3), (12, 15, 5), (13, 34, 11), (14, 37, 12)]
SHUNTS = [(3, 40.0), (9, 20.0)]
COND = {'Z': (0.0750, 0.400, 2.85e-6, (230, 260, 285)), 'TZ': (0.0375, 0.310, 3.70e-6, (460, 520, 570))}
LINES = [(1, 2, 'TZ', 60), (1, 5, 'TZ', 120), (2, 3, 'Z', 110), (2, 4, 'Z', 100), (2, 5, 'Z', 90),
         (3, 4, 'Z', 80), (4, 5, 'Z', 40), (4, 7, 'Z', 60), (4, 9, 'Z', 90), (5, 6, 'Z', 70),
         (6, 11, 'Z', 50), (6, 12, 'Z', 60), (6, 13, 'Z', 40), (7, 8, 'Z', 50), (7, 9, 'Z', 30),
         (9, 10, 'Z', 25), (9, 14, 'Z', 70), (10, 11, 'Z', 45), (12, 13, 'Z', 55), (13, 14, 'Z', 80)]
SYNC = [(1, 0.0, 130, -60, 1.04, 247, 210, 116, 'coal', 3.2),      # bus, PG, QT, QB, VS, MBASE, PT, PB, kind, H
        (2, 125.0, 130, -60, 1.03, 247, 210, 116, 'coal', 3.2),
        (6, 50.0, 70, -40, 1.02, 118, 100, 0, 'hydro', 3.5),
        (8, 30.0, 45, -25, 1.02, 71, 60, 0, 'hydro', 3.5)]
RE = [(12, 150.0, 50, -50, 1.02, 160), (14, 100.0, 33, -33, 1.02, 105)]   # bus, P, QT, QB, VS, MBASE

# ============================================================================ CASE WRITERS
OWN = '   1,1.0000,   0,1.0000,   0,1.0000,   0,1.0000'

def write_raw(fn, units):
    vmax, vmin = 245 / 220, 198 / 220
    gb = {g[0] for g in SYNC} | {r[0] for r in RE}
    txt = ', '.join('BESS %.0f MW @%d' % (u['P'], u['bus']) for u in units) or 'NO BESS'
    L = ['0,   100.00, 33, 0, 1, 50.00     / PSS(R)E-33 RAW  IN-14 220 kV RE + BESS',
         'IN-14/220 RE CASE: SOLAR 150 MW @12, WIND 100 MW @14, ' + txt, 'TPS-A 1 UNIT + GAS-C DECOMMITTED']
    for b in range(1, 15):
        ide = 3 if b == 1 else (2 if b in gb else 1)
        L.append("%6d,'%-12s',%10.4f,%2d,   1,   1,   1,1.00000,   0.0000,%7.5f,%7.5f,%7.5f,%7.5f" % (b, NAMES[b], KV, ide, vmax, vmin, vmax, vmin))
    for k, u in enumerate(units):
        L.append("%6d,'%-12s',%10.4f, 2,   1,   1,   1,1.00000,   0.0000,1.10000,0.90000,1.10000,0.90000" % (BESS_BUS0 + k, 'BESS%d-33KV' % (k + 1), 33.0))
    L.append('0 / END OF BUS DATA, BEGIN LOAD DATA')
    for b, p, q in LOADS:
        L.append("%6d,'1 ',1,   1,   1,%10.3f,%10.3f,     0.000,     0.000,     0.000,     0.000,   1,1,0" % (b, p, q))
    L.append('0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA')
    for b, q in SHUNTS:
        L.append("%6d,'1 ',     1,     0.000,%10.3f" % (b, q))
    L.append('0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA')
    for b, pg, qt, qb, vs, mb, pt, pb, kind, H in SYNC:
        L.append("%6d,'1 ',%10.3f,     0.000,%10.3f,%10.3f,%7.5f,     0,%10.3f, 0.00000E+0, 2.50000E-1, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,%10.3f,%10.3f,%s,0, 1.0000" % (b, pg, qt, qb, vs, mb, pt, pb, OWN))
    for b, p, qt, qb, vs, mb in RE:
        L.append("%6d,'1 ',%10.3f,     0.000,%10.3f,%10.3f,%7.5f,     0,%10.3f, 0.00000E+0, 9.99990E+3, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,%10.3f,     0.000,%s,1, 1.0000" % (b, p, qt, qb, vs, mb, p, OWN))
    for k, u in enumerate(units):
        mb = 1.1 * u['P']
        L.append("%6d,'B ',     0.000,     0.000,     0.000,     0.000,1.00000,     0,%10.3f, 0.00000E+0, 9.99990E+3, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,%10.3f,%10.3f,%s,1, 1.0000" % (BESS_BUS0 + k, mb, u['P'], -u['P'], OWN))
    L.append('0 / END OF GENERATOR DATA, BEGIN BRANCH DATA')
    for f, t, c, km in LINES:
        r, x, bb, rate = COND[c]; zb = KV ** 2 / SBASE
        L.append("%6d,%6d,'1 ', %.5E, %.5E,%10.5f,%8.2f,%8.2f,%8.2f,  0.00000,  0.00000,  0.00000,  0.00000,1,1,%7.2f,%s" % (
            f, t, r * km / zb, x * km / zb, bb * km * zb, rate[0], rate[1], rate[2], km, OWN))
    L.append('0 / END OF BRANCH DATA, BEGIN TRANSFORMER DATA')
    for k, u in enumerate(units):      # 220/33 kV BESS transformer, 12 % on its own MVA base
        mb = 1.1 * u['P']
        L.append("%6d,%6d,     0,'1 ',1,2,1,     0.00000,     0.00000,2,'BESS%d TR 220/33',1,%s,'            '" % (u['bus'], BESS_BUS0 + k, k + 1, OWN))
        L.append(" 3.00000E-3, 1.20000E-1,%10.2f" % mb)
        L.append("1.00000,   0.000,   0.000,%8.2f,%8.2f,%8.2f, 0,      0, 1.10000, 0.90000, 1.10000, 0.90000,  33, 0, 0.00000, 0.00000,  0.00000" % (mb, mb, mb))
        L.append("1.00000,   0.000")
    L += ['0 / END OF TRANSFORMER DATA, BEGIN AREA DATA', "     1,     1,     0.000,    10.000,'IN14-220KV'",
          '0 / END OF AREA DATA, BEGIN TWO-TERMINAL DC DATA', '0 / END OF TWO-TERMINAL DC DATA, BEGIN VSC DC LINE DATA',
          '0 / END OF VSC DC LINE DATA, BEGIN IMPEDANCE CORRECTION DATA',
          '0 / END OF IMPEDANCE CORRECTION DATA, BEGIN MULTI-TERMINAL DC DATA',
          '0 / END OF MULTI-TERMINAL DC DATA, BEGIN MULTI-SECTION LINE DATA',
          '0 / END OF MULTI-SECTION LINE DATA, BEGIN ZONE DATA', "     1,'IN14-220KV  '",
          '0 / END OF ZONE DATA, BEGIN INTER-AREA TRANSFER DATA', '0 / END OF INTER-AREA TRANSFER DATA, BEGIN OWNER DATA',
          "     1,'IN14-OWNER  '", '0 / END OF OWNER DATA, BEGIN FACTS DEVICE DATA',
          '0 / END OF FACTS DEVICE DATA, BEGIN SWITCHED SHUNT DATA', '0 / END OF SWITCHED SHUNT DATA, BEGIN GNE DATA',
          '0 / END OF GNE DATA, BEGIN INDUCTION MACHINE DATA', '0 / END OF INDUCTION MACHINE DATA', 'Q']
    with open(fn, 'w') as fh:
        fh.write('\n'.join(L) + '\n')


VDL = "0.2 1.1 0.5 1.1 0.75 1.1 1.0 1.1 0.2 1.1 0.5 1.1 0.75 1.1 1.0 1.1"   # VDL1 / VDL2 (V, I) pairs

def _regca1(b, mid, tg=0.02, accel=0.7):
    #        Lvplsw Tg   Rrpwr Brkpt Zerox Lvpl1 Volim Lvpnt1 Lvpnt0 Iolim Tfltr Khv Iqrmax Iqrmin Accel
    return "%d 'REGCA1' '%s' 1  %.3f 10.0 0.9 0.4 1.22 1.2 0.8 0.4 -1.3 0.02 0.7 99.0 -99.0 %.2f /" % (b, mid, tg, accel)

def _reeca1(b, mid, pqflag, pmax, pmin, imax, vdip=0.9, vup=1.1):
    ic = "0 0 0 0 0 %d" % pqflag                       # BUSR PFFLAG VFLAG QFLAG PFLAG PQFLAG
    c1 = "%.2f %.2f 0.02 -0.1 0.1 2.0 1.05 -1.05 0.0 0.0 0.0 0.0" % (vdip, vup)   # Vdip Vup Trv dbd1 dbd2 Kqv Iqh1 Iql1 Vref0 Iqfrz Thld Thld2
    c2 = "0.05 0.436 -0.436 1.1 -1.1 0.0 0.1 0.0 40.0 0.0 0.02"    # Tp QMax QMin VMAX VMIN Kqp Kqi Kvp Kvi Vbias Tiq
    c3 = "99.0 -99.0 %.4f %.4f %.4f 0.02" % (pmax, pmin, imax)       # dPmax dPmin PMAX PMIN Imax Tpord
    return "%d 'REECA1' '%s' %s %s %s %s %s /" % (b, mid, ic, c1, c2, c3, VDL)

def _reecc1(b, mid, pmax, pmin, imax, vdip, vup, T, socini, socmax, socmin):
    """WECC REEC_C storage controller: 5 ICONs + 45 CONs (same electrical settings as the BESS REECA1).
    ASSUMED ORDER (from a published REECCU1 data line) - CHECK IT against the REECC1 / REECCU1 page of the
    PSS(R)E 36 Model Library, especially the position of T, SOCini, SOCmax and SOCmin at the end:
      ICONs: BUSR PFFLAG VFLAG QFLAG PQFLAG
      CONs : Vdip Vup Trv dbd1 dbd2 Kqv Iqh1 Iql1 Vref0 Tp | QMax QMin VMAX VMIN Kqp Kqi Kvp Kvi |
             Tiq dPmax dPmin PMAX PMIN Imax Tpord | VDL1 (8) VDL2 (8) | T SOCini SOCmax SOCmin"""
    ic = "0 0 0 0 1"                                    # P priority
    c1 = "%.2f %.2f 0.02 -0.1 0.1 %.3f 1.05 -1.05 0.0 0.05" % (vdip, vup, REECC_KQV)
    c2 = "0.436 -0.436 1.1 -1.1 %.3f %.3f %.3f %.3f" % REECC_QPI
    c3 = "0.02 99.0 -99.0 %.4f %.4f %.4f 0.02" % (pmax, pmin, imax)
    soc = "%.2f %.4f %.4f %.4f" % (T, socini, socmax, socmin)
    data = "%s %s %s %s %s %s" % (ic, c1, c2, c3, VDL, soc)
    if BESS_EC_MODEL == 'REECCU1':                     # user model: IT NI? NI NC NS NV as in the published line
        return "%d 'USRMDL' '%s' 'REECCU1' 102 0 5 45 7 6 %s /" % (b, mid, data)
    return "%d 'REECC1' '%s' %s /" % (b, mid, data)

def _repca1(b, mid, fflag, pmax, pmin, droop, kpg, kig, tg, qkp=18.0, qki=5.0):
    ic = "0 0 0 '1 ' 0 0 %d" % fflag                   # BUSR BUS1 BUS2 CKT VCFlag RefFlag Fflag
    c = ("0.02 %.3f %.3f 0.0 0.05 0.0 0.0 0.0 0.0 0.1 -0.1 0.0 0.0 0.436 -0.436 "
         "%.4f %.4f 0.02 -0.0004 0.0004 99.0 -99.0 %.4f %.4f %.4f %.3f %.3f" % (qkp, qki, kpg, kig, pmax, pmin, tg, droop, droop))
    return "%d 'REPCA1' '%s' %s %s /" % (b, mid, ic, c)

def e_dynamic(u):
    """Energy (MWh, SOC 0->1) given to the REECC1 SOC model. It is the smallest energy the sizing can return,
    so the dynamic SOC limits are conservative (a larger final E would move the SOC less)."""
    return (E_FIXED_H if E_FIXED_H is not None else MIN_DURATION_H) * u['P']

def write_dyr(fn, units, R):
    L = []
    for b, pg, qt, qb, vs, mb, pt, pb, kind, H in SYNC:
        if kind == 'coal':
            L.append("%d 'GENROU' 1  6.0 0.05 0.90 0.07 %.2f 0.0 1.80 1.75 0.30 0.55 0.25 0.15 0.10 0.40 /" % (b, H))
            L.append("%d 'SEXS' 1  0.1 10.0 100.0 0.05 0.0 4.0 /" % b)
            L.append("%d 'TGOV1' 1  0.05 0.50 %.3f 0.30 2.1 7.0 0.0 /" % (b, pt / mb))
        else:
            L.append("%d 'GENSAL' 1  6.0 0.05 0.08 %.2f 0.0 1.00 0.65 0.35 0.25 0.15 0.10 0.30 /" % (b, H))
            L.append("%d 'SEXS' 1  0.1 10.0 100.0 0.05 0.0 4.0 /" % b)
            L.append("%d 'HYGOV' 1  0.05 0.38 5.0 0.05 0.5 0.2 0.80 0.0 1.2 1.2 0.5 0.08 /" % b)
    for b, p, qt, qb, vs, mb in RE:
        pm = p / mb
        L += [_regca1(b, '1'), _reeca1(b, '1', 0, pm, 0.0, 1.1), _repca1(b, '1', 0, pm, 0.0, 0.0, 0.0, 0.0, 0.1, RE_QKP, RE_QKI)]
    for k, u in enumerate(units):
        b = BESS_BUS0 + k; mb = 1.1 * u['P']; pm = u['P'] / mb
        droop = pm / (R / F0)                   # pu power (MBASE) per pu frequency: full output at R Hz
        if BESS_EC_MODEL == 'REECA1':
            ec = _reeca1(b, 'B', 1, pm, -pm, 1.1, BESS_VDIP, BESS_VUP)     # Ipmin = 0 inside REECA1: no charging
        else:
            T = e_dynamic(u) * 3600.0 / mb      # s: time to move SOC from 1 to 0 at 1 pu power on MBASE
            ec = _reecc1(b, 'B', pm, -pm, BESS_IMAX, BESS_VDIP, BESS_VUP, T, SOC_INI, SOC_MAX, SOC_MIN)
        L += [_regca1(b, 'B', BESS_TG, BESS_ACCEL), ec, _repca1(b, 'B', 1, pm, -pm, droop, BESS_KPG, 0.0, BESS_TLAG, BESS_QKP, BESS_QKI)]
    with open(fn, 'w') as fh:
        fh.write('\n'.join(L) + '\n')

# ============================================================================ CONTINGENCIES / TOPOLOGY
def contingencies(units):
    """Fixed contingencies + a busbar outage at every distinct BESS host bus."""
    cons = list(FIXED_CONT)
    for b in sorted({u['bus'] for u in units}):
        cons.append(('Busbar outage at bus %d (BESS bus)' % b, b, 'bus', b, None))
    return cons

def out_of_service_buses(units, bus_out):
    """Buses disconnected when bus_out is cleared: the bus, BESS buses behind it, and anything islanded."""
    nodes = set(range(1, 15)) | {BESS_BUS0 + k for k in range(len(units))}
    edges = [(f, t) for f, t, c, km in LINES] + [(u['bus'], BESS_BUS0 + k) for k, u in enumerate(units)]
    out = {bus_out} | {BESS_BUS0 + k for k, u in enumerate(units) if u['bus'] == bus_out}
    alive = nodes - out
    adj = {n: set() for n in alive}
    for f, t in edges:
        if f in alive and t in alive:
            adj[f].add(t); adj[t].add(f)
    comps, seen = [], set()
    for n in sorted(alive):
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            m = stack.pop()
            if m in comp:
                continue
            comp.add(m); stack.extend(adj[m] - comp)
        seen |= comp; comps.append(comp)
    # keep the island with the most synchronous generation (then most buses); the rest is lost
    syn = {g[0]: g[5] for g in SYNC}
    main = max(comps, key=lambda c: (sum(syn.get(n, 0) for n in c), len(c)))
    return out | (alive - main)

# ============================================================================ PSS(R)E RUN
psspy = None

def check_settings():
    if not (0.0 <= SOC_MIN < SOC_INI < SOC_MAX <= 1.0):
        raise ValueError('SOC settings must satisfy 0 <= SOC_MIN < SOC_INI < SOC_MAX <= 1')
    if not (0.0 < ETA_DIS <= 1.0 and 0.0 < ETA_CH <= 1.0):
        raise ValueError('efficiencies must be in (0, 1]')
    if BESS_EC_MODEL not in ('REECA1', 'REECC1', 'REECCU1'):
        raise ValueError("BESS_EC_MODEL must be 'REECA1', 'REECC1' or 'REECCU1'")
    if BESS_EC_MODEL != 'REECA1' and not REECC_ORDER_CHECKED:
        raise RuntimeError('BESS_EC_MODEL = %s: compare the parameter order in _reecc1() with the %s page of your '
                           'PSS(R)E Model Library (5 ICONs, 45 CONs, SOC parameters last), correct it if needed, '
                           'then set REECC_ORDER_CHECKED = True.' % (BESS_EC_MODEL, BESS_EC_MODEL))

def psse_start():
    global psspy
    if psspy is not None:
        return
    if os.environ.get('IN14_WORKER') == '1':        # started by the parallel launcher: own PSS(R)E instance
        sys.path.insert(0, _find_psspy())
        os.environ['PATH'] = os.path.dirname(_find_psspy()) + os.pathsep + os.environ.get('PATH', '')
        try:
            import psse36                             # sets the PSS(R)E paths (name differs between versions)
        except ImportError:
            pass
        import psspy as p
        p.psseinit(50)
        p.progress_output(2, os.path.join(WORK, 'psse_progress.txt'), [0, 0])
        p.report_output(2, os.path.join(WORK, 'psse_report.txt'), [0, 0])
        psspy = p
        return
    try:
        import psspy as p
    except ImportError:
        sys.path.append(_find_psspy())
        import psse36
        import psspy as p
        p.psseinit(50)
    psspy = p
    if MODE == 'pso':
        psspy.progress_output(6, '', [0, 0])      # silence per-run progress; report output stays on


def run_case(units, R, cont, tag):
    global BESS_EC_MODEL
    raw = os.path.join(WORK, tag + '.raw'); dyr = os.path.join(WORK, tag + '.dyr'); out = os.path.join(WORK, tag + '.outx')
    write_raw(raw, units); write_dyr(dyr, units, R)
    if psspy.read(0, raw) != 0:
        raise RuntimeError('read RAW failed: ' + raw)
    psspy.fnsl([0, 0, 0, 1, 1, 0, 99, 0])
    if psspy.solved() != 0:
        raise RuntimeError('load flow did not converge (%s)' % tag)
    psspy.cong(0)
    for opt in (1, 2, 3):
        psspy.conl(0, 1, opt, [0, 0], [100.0, 0.0, 0.0, 100.0])
    psspy.ordr(0); psspy.fact(); psspy.tysl(0)
    if psspy.dyre_new([1, 1, 1, 1], dyr, "", "", "") != 0:
        if BESS_EC_MODEL == 'REECC1' and units:       # library model missing -> try the user model REECCU1
            print('REECC1 not accepted by dyre_new - retrying with user model REECCU1')
            BESS_EC_MODEL = 'REECCU1'
            write_dyr(dyr, units, R)
            if psspy.dyre_new([1, 1, 1, 1], dyr, "", "", "") == 0:
                return _finish_run(units, R, cont, out)
        raise RuntimeError('dyre_new failed - check ' + dyr + ' (BESS controller %s)' % BESS_EC_MODEL)
    return _finish_run(units, R, cont, out)


def _finish_run(units, R, cont, out):
    import dyntools
    psspy.dynamics_solution_param_2(realar3=DELT)
    psspy.set_netfrq(1)
    try:
        psspy.delete_all_plot_channels()
    except Exception:
        pass
    for g in SYNC:
        psspy.machine_array_channel([-1, 7, g[0]], '1', 'SPD %d' % g[0])
    for k in range(len(units)):
        psspy.machine_array_channel([-1, 2, BESS_BUS0 + k], 'B', 'PB%d' % (k + 1))
        psspy.machine_array_channel([-1, 3, BESS_BUS0 + k], 'B', 'QB%d' % (k + 1))
        psspy.voltage_channel([-1, -1, -1, BESS_BUS0 + k], 'VB%d' % (k + 1))
    for b in range(1, 15):
        psspy.bus_frequency_channel([-1, b], 'FRQ %d' % b)
        psspy.voltage_channel([-1, -1, -1, b], 'V %d' % b)
    ierr = psspy.strt_2([0, 0], out)
    if ierr != 0:
        raise RuntimeError('strt_2 ierr=%d (initial conditions) - see Progress window. With REECC1/REECCU1, '
                           'check the parameter order in _reecc1() against the Model Library.' % ierr)

    name, fbus, kind, tb, tid = cont
    psspy.run(0, T_FAULT, 1000, 1, 0)
    psspy.dist_bus_fault(fbus, 1, KV, [0.0, -2.0E11])
    psspy.run(0, T_CLEAR, 1000, 1, 0)
    psspy.dist_clear_fault(1)
    if kind == 'machine':
        psspy.dist_machine_trip(tb, tid)
        lost_buses = set(); tripped = {tb} if tb in [g[0] for g in SYNC] else set()
    else:
        lost_buses = out_of_service_buses(units, tb)
        tripped = {g[0] for g in SYNC if g[0] in lost_buses}
        for b in sorted(lost_buses):          # trip machines first, then disconnect the buses
            for g in SYNC:
                if g[0] == b:
                    psspy.dist_machine_trip(b, '1')
            for r in RE:
                if r[0] == b:
                    psspy.dist_machine_trip(b, '1')
            if b >= BESS_BUS0:
                psspy.dist_machine_trip(b, 'B')
        for b in sorted(lost_buses, reverse=True):
            if psspy.dist_bus_trip(b) != 0:
                raise RuntimeError('dist_bus_trip(%d) failed' % b)
    psspy.run(0, T_END, 1000, 1, 0)

    _, chanid, data = dyntools.CHNF(out).get_data()
    t = data['time']; idx = {v.strip(): k for k, v in chanid.items() if k != 'time'}
    ic = next(i for i, x in enumerate(t) if x >= T_CLEAR)
    fcoi = []
    for i in range(len(t)):
        num = den = 0.0
        for g in SYNC:
            if g[0] in tripped and i >= ic:
                continue
            w = g[9] * g[5]; num += w * F0 * (1 + data[idx['SPD %d' % g[0]]][i]); den += w
        fcoi.append(num / den)
    nadir = min(fcoi[ic:])
    w = int(round(0.5 / (t[1] - t[0])))
    rocof = max(abs(fcoi[i + w] - fcoi[i]) for i in range(ic, len(t) - w)) / 0.5
    i0 = next(i for i, x in enumerate(t) if x >= T_CLEAR + 0.15)
    fvals = [F0 * (1 + data[idx['FRQ %d' % b]][i]) for b in range(1, 15) if b not in lost_buses
             for i in range(i0, len(t)) if data[idx['V %d' % b]][i] > 0.8]
    fbus = min(fvals) if fvals else 45.0      # no bus above 0.8 pu after clearing: voltage collapse -> infeasible
    pb = [[v * SBASE for v in data[idx['PB%d' % (k + 1)]]] for k in range(len(units))]   # MW, + = discharging
    qb = [[v * SBASE for v in data[idx['QB%d' % (k + 1)]]] for k in range(len(units))]   # Mvar
    vb = [list(data[idx['VB%d' % (k + 1)]]) for k in range(len(units))]                   # pu, 33 kV BESS bus
    i1 = next(i for i, x in enumerate(t) if x >= T_CLEAR + 0.5)
    f_over = max(fcoi[i1:])
    tt = [x for x in t if x >= T_END - 3.0]
    tail = [f for x, f in zip(t, fcoi) if x >= T_END - 3.0]
    under_event = sum(tail) / len(tail) < F0          # frequency settles below 50 Hz -> generation was lost
    # oscillation check: remove the slow linear drift (governor recovery) before measuring the swing
    mt, mf = sum(tt) / len(tt), sum(tail) / len(tail)
    slope = sum((a - mt) * (b - mf) for a, b in zip(tt, tail)) / max(sum((a - mt) ** 2 for a in tt), 1e-12)
    resid = [b - (mf + slope * (a - mt)) for a, b in zip(tt, tail)]
    ptot = [sum(z) for z in zip(*pb)] if pb else [0.0] * len(t)
    ptail = [p for x, p in zip(t, ptot) if x >= T_END - 3.0]
    mp = sum(ptail) / len(ptail)
    sp = sum((a - mt) * (b - mp) for a, b in zip(tt, ptail)) / max(sum((a - mt) ** 2 for a in tt), 1e-12)
    presid = [b - (mp + sp * (a - mt)) for a, b in zip(tt, ptail)]
    over_lim = F_OVER_MAX if under_event else F_OVER_LOADLOSS
    return dict(name=name, nadir=nadir, rocof=rocof, fbus=fbus, f_end=fcoi[-1], f_over=f_over, over_lim=over_lim,
                event='under-frequency' if under_event else 'over-frequency',
                f_swing=max(resid) - min(resid), p_ripple=max(presid) - min(presid), t=t, fcoi=fcoi, pb_units=pb, qb_units=qb, vb_units=vb,
                pb=[sum(v) for v in zip(*pb)] if pb else [0.0] * len(t), lost=sorted(lost_buses))

# ============================================================================ STATE OF CHARGE
def _energy_flow(p_mw):
    """Energy taken from (+) or put into (-) the cells per MW of terminal power, after losses."""
    return p_mw / ETA_DIS if p_mw > 0 else p_mw * ETA_CH

def soc_analysis(units, res):
    """Size the energy of every BESS from its SOC window, then compute each SOC trajectory.
    Adds r['soc'] (list per unit) and r['soc_agc'] (SOC projected to the AGC hand-over) to every result.
    The BESS is assumed to hold its end-of-run power until T_SECONDARY (as in the paper's sizing rule)."""
    win_d, win_c = SOC_INI - SOC_MIN, SOC_MAX - SOC_INI
    E, need_d, need_c = [], [], []
    for k, u in enumerate(units):
        nd = nc = 0.0
        for r in res:
            s = r['pb_units'][k]; dt = r['t'][1] - r['t'][0]
            run = sum(_energy_flow(v) for v in s) * dt / 3600.0 if s else 0.0
            dis = sum(_energy_flow(v) for v in s if v > 0) * dt / 3600.0
            chg = -sum(_energy_flow(v) for v in s if v < 0) * dt / 3600.0
            tail = _energy_flow(s[-1]) * T_SECONDARY / 3600.0
            # worst cumulative discharge / charge, including the hold until AGC
            nd = max(nd, dis + max(tail, 0.0), run + max(tail, 0.0))
            nc = max(nc, chg + max(-tail, 0.0), -run + max(-tail, 0.0))
        need_d.append(nd); need_c.append(nc)
        if E_FIXED_H is not None:
            E.append(E_FIXED_H * u['P'])
        else:
            E.append(max(nd / win_d, nc / win_c, MIN_DURATION_H * u['P']))
    lo, hi = SOC_INI, SOC_INI
    for r in res:
        r['soc'], r['soc_agc'] = [], []
        dt = r['t'][1] - r['t'][0]
        for k in range(len(units)):
            cum, tr = 0.0, []
            for v in r['pb_units'][k]:
                tr.append(SOC_INI - cum / E[k])
                cum += _energy_flow(v) * dt / 3600.0
            agc = tr[-1] - _energy_flow(r['pb_units'][k][-1]) * T_SECONDARY / 3600.0 / E[k]
            r['soc'].append(tr); r['soc_agc'].append(agc)
            lo = min(lo, min(tr), agc); hi = max(hi, max(tr), agc)
    tol = 1e-6
    viol = (max(0.0, SOC_MIN - lo - tol) + max(0.0, hi - SOC_MAX - tol)) / SOC_PENALTY_STEP
    return E, dict(lo=lo, hi=hi, viol=viol, need_dis_MWh=need_d, need_chg_MWh=need_c)

# ============================================================================ OBJECTIVE
def _failed_result(units, cont):
    """Result used when PSS(R)E cannot simulate a contingency for a design: heavily infeasible, no BESS output."""
    t = [0.0, DELT, T_END]
    return dict(name=cont[0], nadir=45.0, rocof=5.0, fbus=45.0, f_end=45.0, f_over=F0, over_lim=F_OVER_MAX,
                event='failed', f_swing=1.0, p_ripple=0.0, t=t, fcoi=[F0, 45.0, 45.0],
                pb_units=[[0.0] * 3 for _ in units], qb_units=[[0.0] * 3 for _ in units],
                vb_units=[[0.0] * 3 for _ in units], pb=[0.0] * 3, lost=[])

def decode(x):
    if N_BESS == 1:
        return [dict(bus=int(math.floor(x[0])), P=x[1])], x[2]
    return [dict(bus=int(math.floor(x[0])), P=x[2]), dict(bus=int(math.floor(x[1])), P=x[3])], x[4]

def evaluate(units, R):
    res = []
    for c in contingencies(units):
        try:
            res.append(run_case(units, R, c, 'eval'))
        except Exception as e:                     # simulation failed for this design: score it as a collapse
            print('  design %s, %s: simulation failed (%s) - treated as collapse' % (fmt(units, R), c[0], e))
            res.append(_failed_result(units, c))
    fmin = min(min(r['fbus'], r['nadir']) for r in res)
    rc = max(r['rocof'] for r in res)
    over_ex = max(r['f_over'] - r['over_lim'] for r in res)     # overshoot beyond each event's own limit
    swing = max(r['f_swing'] for r in res)
    ripple = max(r['p_ripple'] for r in res) / max(sum(u['P'] for u in units), 1e-6)   # fraction of rating
    E, soc = soc_analysis(units, res)
    C = sum(COST_PER_MW * u['P'] + COST_PER_MWH * e for u, e in zip(units, E))
    viol = (max(0.0, F_MIN - fmin) / 0.1 + max(0.0, rc - ROCOF_MAX) / 0.1
            + max(0.0, over_ex) / 0.1 + max(0.0, swing - F_SWING_MAX) / 0.05 + soc['viol']
            + max(0.0, ripple - P_RIPPLE_MAX) / 0.05)
    Cref = COST_PER_MW * 300.0 + COST_PER_MWH * MIN_DURATION_H * 300.0
    J = C / Cref + 10.0 * viol + 0.05 * (F0 - min(r['nadir'] for r in res))
    return J, dict(units=units, R=R, E=E, cost=C, fmin=fmin, rocof=rc, viol=viol, soc=soc,
                   ripple=ripple,
                   stable=(over_ex <= 0 and swing <= F_SWING_MAX and ripple <= P_RIPPLE_MAX), res=res)

def fmt(units, R, E=None):
    s = ' + '.join('%.0f MW%s @bus %d' % (u['P'], (' / %.0f MWh' % E[k]) if E else '', u['bus']) for k, u in enumerate(units))
    return '%s, droop %.2f Hz' % (s, R)

# ============================================================================ PSO
def pso():
    random.seed(SEED)
    lo = [b[0] for b in BOUNDS]; hi = [b[1] for b in BOUNDS]; D = len(BOUNDS)
    X = [[lo[d] + random.random() * (hi[d] - lo[d]) for d in range(D)] for _ in range(N_PART)]
    for i in range(N_PART):                            # spread the first bus over all 14 buses
        X[i][0] = 1 + (i % 14) + random.random() * 0.999
    V = [[0.0] * D for _ in range(N_PART)]
    P = [x[:] for x in X]; PJ = [float('inf')] * N_PART
    G, GJ, GI = None, float('inf'), None
    hist = []
    vmax = [0.2 * (hi[d] - lo[d]) for d in range(D)]
    log = open(os.path.join(WORK, 'pso_log.csv'), 'w')
    hdr = 'iter,particle,' + ','.join('bus%d,P%d_MW' % (k + 1, k + 1) for k in range(N_BESS)) + \
          ',R_Hz,J,f_min,rocof,E_total_MWh,cost,soc_min,soc_max,stable\n'
    log.write(hdr)
    for it in range(N_ITER):
        for i in range(N_PART):
            units, R = decode(X[i])
            J, info = evaluate(units, R)
            log.write('%d,%d,' % (it + 1, i) + ','.join('%d,%.2f' % (u['bus'], u['P']) for u in units) +
                      ',%.3f,%.5f,%.4f,%.4f,%.2f,%.3f,%.4f,%.4f,%s\n' % (
                          R, J, info['fmin'], info['rocof'], sum(info['E']), info['cost'],
                          info['soc']['lo'], info['soc']['hi'], info['stable']))
            log.flush()
            if J < PJ[i]:
                PJ[i], P[i] = J, X[i][:]
            if J < GJ:
                GJ, G, GI = J, X[i][:], info
        hist.append(GJ)
        w = 0.9 - 0.5 * it / max(N_ITER - 1, 1)
        for i in range(N_PART):
            for d in range(D):
                V[i][d] = w * V[i][d] + 1.5 * random.random() * (P[i][d] - X[i][d]) + 1.5 * random.random() * (G[d] - X[i][d])
                V[i][d] = max(-vmax[d], min(vmax[d], V[i][d]))
                X[i][d] = max(lo[d], min(hi[d], X[i][d] + V[i][d]))
        print('PSO iter %2d/%d  J=%.4f  %s  cost %.1f M$  f_min=%.3f  RoCoF=%.2f  SOC %.3f-%.3f' % (
            it + 1, N_ITER, GJ, fmt(GI['units'], GI['R'], GI['E']), GI['cost'], GI['fmin'], GI['rocof'],
            GI['soc']['lo'], GI['soc']['hi']))
    log.close()
    return GI, hist

# ============================================================================ OUTPUT
def save_series(tag, runs):
    with open(os.path.join(WORK, tag + '.csv'), 'w') as fh:
        fh.write('time,' + ','.join('fCOI_%d,PBESS_total_MW_%d' % (c, c) for c in range(len(runs))) + '\n')
        n = min(len(r['t']) for r in runs)
        for i in range(n):
            fh.write('%.4f,' % runs[0]['t'][i] + ','.join('%.5f,%.3f' % (r['fcoi'][i], r['pb'][i]) for r in runs) + '\n')

def save_soc(runs, units):
    with open(os.path.join(WORK, 'BESS_SOC.csv'), 'w') as fh:
        cols = ['SOC_c%d_bus%d' % (c, u['bus']) for c in range(len(runs)) for u in units]
        fh.write('time,' + ','.join(cols) + '\n')
        n = min(len(r['t']) for r in runs)
        for i in range(n):
            fh.write('%.4f,' % runs[0]['t'][i] +
                     ','.join('%.6f' % r['soc'][k][i] for r in runs for k in range(len(units))) + '\n')

def plot(nob, wb, title):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib not available - plot the CSV files in Excel'); return
    n = len(wb)
    fig, ax = plt.subplots(1, n, figsize=(4.6 * n, 4.6), sharey=True, squeeze=False)
    ax = ax[0]
    for c in range(n):
        ref = [r for r in nob if r['name'] == wb[c]['name']]
        if ref:
            ax[c].plot(ref[0]['t'], ref[0]['fcoi'], color='#eb6834', lw=1.8, label='RE, no BESS')
        ax[c].plot(wb[c]['t'], wb[c]['fcoi'], color='#2a78d6', lw=2.2, label='RE + BESS')
        ax[c].axhline(49.4, color='#b33', lw=0.8, ls=':')
        ax[c].set_title(wb[c]['name'], fontsize=9); ax[c].set_xlabel('time (s)'); ax[c].grid(alpha=0.25)
    ax[0].set_ylabel('COI frequency (Hz)'); ax[0].legend(frameon=False, fontsize=8)
    fig.suptitle(title); fig.tight_layout(); fig.savefig(os.path.join(WORK, 'frequency_PSSE.png'), dpi=130)

def plot_soc(wb, units, E):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return
    n = len(wb)
    fig, ax = plt.subplots(1, n, figsize=(4.6 * n, 4.2), sharey=True, squeeze=False)
    ax = ax[0]
    styles = ['-', '--', ':']
    for c in range(n):
        for k, u in enumerate(units):
            ax[c].plot(wb[c]['t'], [100 * s for s in wb[c]['soc'][k]], ls=styles[k % 3], lw=2, color='#2a78d6',
                       label='BESS bus %d (%.0f MWh)' % (u['bus'], E[k]))
        ax[c].axhline(100 * SOC_MIN, color='#b33', lw=0.8, ls=':')
        ax[c].axhline(100 * SOC_MAX, color='#b33', lw=0.8, ls=':')
        txt = ', '.join('%.1f %%' % (100 * a) for a in wb[c]['soc_agc'])
        ax[c].set_title('%s\nSOC at AGC hand-over: %s' % (wb[c]['name'], txt), fontsize=8)
        ax[c].set_xlabel('time (s)'); ax[c].grid(alpha=0.25)
    ax[0].set_ylabel('state of charge (%)'); ax[0].legend(frameon=False, fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(WORK, 'soc_PSSE.png'), dpi=130)

# ============================================================================ RIPPLE DIAGNOSIS
def _ripple(t, x, t0=5.0):
    """Peak-to-peak (after removing a straight-line trend) and dominant frequency of x for t >= t0."""
    import numpy as np
    t = np.array(t); x = np.array(x); m = t >= t0
    if m.sum() < 20:
        return 0.0, 0.0
    y = x[m] - np.polyval(np.polyfit(t[m], x[m], 1), t[m])
    F = np.fft.rfftfreq(len(y), t[1] - t[0]); A = np.abs(np.fft.rfft(y)); A[0] = 0
    return float(y.max() - y.min()), float(F[A.argmax()])

def diag():
    """Runs contingency R1 for the REECC1 split optimum with one control setting changed at a time and reports
    the BESS active-power, reactive-power and voltage ripple. The variant whose ripple disappears points to the
    loop that causes it. About 5-10 s per variant."""
    global BESS_EC_MODEL, REECC_KQV, REECC_QPI, BESS_KPG, BESS_TLAG, BESS_QKP, BESS_QKI, BESS_IMAX, DELT, BESS_TG, BESS_ACCEL
    global RE_QKP, RE_QKI
    base = (BESS_EC_MODEL, REECC_KQV, REECC_QPI, BESS_KPG, BESS_TLAG, BESS_QKP, BESS_QKI, BESS_IMAX, DELT, BESS_TG, BESS_ACCEL)
    variants = [
        ('F0 final settings (2.5 ms)',                 dict()),
        ('F1 final settings, 1 ms check',              dict(delt=0.001)),
        ('F2 final settings, 5 ms',                    dict(delt=0.005)),
        ('F3 final but solar/wind Q loop 18/5',        dict(req=(18.0, 5.0))),
    ]
    units, R = DIAG_DESIGN['units'], DIAG_DESIGN['R']
    cont = FIXED_CONT[0]
    rows, traces = [], []
    for name, v in variants:
        BESS_EC_MODEL = v.get('ec', base[0]); REECC_KQV = v.get('kqv', base[1]); REECC_QPI = v.get('qpi', base[2])
        BESS_KPG = v.get('kpg', base[3]); BESS_TLAG = v.get('tlag', base[4])
        BESS_QKP = v.get('qkp', base[5]); BESS_QKI = v.get('qki', base[6]); BESS_IMAX = v.get('imax', base[7])
        DELT = v.get('delt', base[8]); BESS_TG = v.get('tg', base[9]); BESS_ACCEL = v.get('accel', base[10])
        units = v.get('units', DIAG_DESIGN['units'])
        RE_QKP, RE_QKI = v.get('req', (1.0, 0.5))
        try:
            r = run_case(units, R, cont, 'diag')
        except Exception as e:
            print('%-40s FAILED: %s' % (name, e)); continue
        P = [sum(z) for z in zip(*r['pb_units'])]; Q = [sum(z) for z in zip(*r['qb_units'])]
        pp, pf = _ripple(r['t'], P); qp, qf = _ripple(r['t'], Q); vp, vf = _ripple(r['t'], r['vb_units'][0])
        rows.append((name, min(r['nadir'], r['fbus']), pp, pf, qp, qf, vp, vf))
        traces.append((name, r['t'], P, Q))
        print('%-40s nadir %.3f Hz | P ripple %6.1f MW @ %5.2f Hz | Q ripple %6.1f Mvar @ %5.2f Hz | V ripple %.4f pu'
              % (name, rows[-1][1], pp, pf, qp, qf, vp))
    BESS_EC_MODEL, REECC_KQV, REECC_QPI, BESS_KPG, BESS_TLAG, BESS_QKP, BESS_QKI, BESS_IMAX, DELT, BESS_TG, BESS_ACCEL = base
    RE_QKP, RE_QKI = 1.0, 0.5
    with open(os.path.join(WORK, 'diag_ripple.csv'), 'w') as fh:
        fh.write('variant,worst_f_Hz,P_ripple_MW,P_ripple_Hz,Q_ripple_Mvar,Q_ripple_Hz,V_ripple_pu,V_ripple_Hz\n')
        for x in rows:
            fh.write('%s,%.4f,%.2f,%.2f,%.2f,%.2f,%.5f,%.2f\n' % x)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
        for name, t, P, Q in traces:
            ax[0].plot(t, P, lw=1, label=name); ax[1].plot(t, Q, lw=1)
        ax[0].set_ylabel('BESS P total (MW)'); ax[1].set_ylabel('BESS Q total (Mvar)'); ax[1].set_xlabel('time (s)')
        ax[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(os.path.join(WORK, 'diag_ripple.png'), dpi=130)
    except ImportError:
        pass
    print('Diagnosis written to', os.path.join(WORK, 'diag_ripple.csv'))

def _find_psspy():
    """Folder that contains psspy: PSSE_PSSPY if it exists, otherwise searched under C:\\Program Files (x86)\\PTI."""
    import glob
    if os.path.isdir(PSSE_PSSPY):
        return PSSE_PSSPY
    for root in (r"C:\Program Files\PTI", r"C:\Program Files (x86)\PTI"):
        hits = sorted(glob.glob(os.path.join(root, '**', 'PSSPY3*', 'psspy.py*'), recursive=True))
        if hits:
            return os.path.dirname(hits[-1])
    raise RuntimeError('psspy not found - set PSSE_PSSPY to the PSSPY3x folder of your PSS(R)E installation')

def _summarise_and_zip(done, t_all):
    """Write the overview of a batch and zip all result folders (used by 'all' and 'parallel')."""
    import zipfile
    _summarise_and_zip(done, t_all)

def run_parallel():
    """Start every PARALLEL_PLAN entry as its own Python + PSS(R)E process, N_WORKERS at a time.
    Must be started from a Command Prompt (python in14_psse_bess_parallel.py), not from inside the PSS(R)E GUI."""
    import subprocess
    check_settings()
    _find_psspy()                                     # fail early if PSS(R)E cannot be found
    queue = list(PARALLEL_PLAN); running = []; done = []; t_all = time.time()
    env = dict(os.environ, IN14_WORKER='1')
    print('Parallel batch: %d runs, %d at a time' % (len(queue), N_WORKERS))
    while queue or running:
        while queue and len(running) < N_WORKERS:
            mode, n_bess, seed = queue.pop(0)
            configure(mode, n_bess, seed)             # creates the result folder and gives WORK_NAME
            log = open(os.path.join(WORK, 'console_log.txt'), 'w')
            pr = subprocess.Popen([sys.executable, os.path.abspath(__file__), mode, str(n_bess), str(seed)],
                                  cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
            running.append((pr, WORK_NAME, time.time(), log))
            print('  started  %-28s (%d waiting)' % (WORK_NAME, len(queue)))
        time.sleep(5)
        for item in running[:]:
            pr, name, t0, log = item
            if pr.poll() is not None:
                log.close(); running.remove(item)
                ok = pr.returncode == 0 and os.path.exists(os.path.join(HERE, name, 'summary.json'))
                done.append((name, 'ok' if ok else 'FAILED (see console_log.txt)', time.time() - t0))
                print('  finished %-28s %s  %.1f min' % (name, done[-1][1], (time.time() - t0) / 60.0))
    _summarise_and_zip(sorted(done), t_all)

def configure(mode, n_bess, seed):
    """Set the globals that depend on the run (mode, single/split, seed, bounds, results folder)."""
    global MODE, N_BESS, SEED, BESS_CHECK, BOUNDS, WORK_NAME, WORK
    MODE, N_BESS, SEED = mode, n_bess, seed
    if n_bess == 1:
        BESS_CHECK = dict(units=[dict(bus=9, P=180.0)], R=0.15)
        BOUNDS = [(1, 14.999), (10.0, 300.0), (0.15, 1.0)]
        WORK_NAME = 'IN14_FINAL_single_seed%d' % seed
    else:
        BESS_CHECK = dict(units=[dict(bus=5, P=104.0), dict(bus=13, P=65.0)], R=0.15)
        BOUNDS = [(1, 14.999), (1, 14.999), (10.0, 200.0), (10.0, 200.0), (0.15, 1.0)]
        WORK_NAME = 'IN14_FINAL_split_seed%d' % seed
    if mode == 'diag':
        WORK_NAME = 'IN14_FINAL_diag'
    WORK = os.path.join(HERE, WORK_NAME)
    os.makedirs(WORK, exist_ok=True)

def run_all():
    """Run every entry of RUN_PLAN, keep going if one fails, then write an overview and zip the results."""
    import zipfile, traceback
    check_settings()
    psse_start()
    psspy.progress_output(6, '', [0, 0])          # keep the Output window readable during the long batch
    t_all = time.time(); done = []
    for k, (mode, n_bess, seed) in enumerate(RUN_PLAN):
        configure(mode, n_bess, seed)
        print('\n' + '=' * 90)
        print('RUN %d/%d: MODE=%s  N_BESS=%d  SEED=%d  ->  %s' % (k + 1, len(RUN_PLAN), mode, n_bess, seed, WORK_NAME))
        print('=' * 90)
        t0 = time.time()
        try:
            if mode == 'diag':
                diag()
            else:
                run_one()
            done.append((WORK_NAME, 'ok', time.time() - t0))
        except Exception:
            traceback.print_exc()
            with open(os.path.join(WORK, 'ERROR.txt'), 'w') as fh:
                fh.write(traceback.format_exc())
            done.append((WORK_NAME, 'FAILED (see ERROR.txt)', time.time() - t0))
    lines = ['IN-14 batch run finished in %.1f min' % ((time.time() - t_all) / 60.0), '']
    for name, status, dt in done:
        line = '%-28s %-24s %6.1f min' % (name, status, dt / 60.0)
        sj = os.path.join(HERE, name, 'summary.json')
        if os.path.exists(sj):
            sm = json.load(open(sj))
            line += '   ' + ' + '.join('%.1f MW @ bus %d' % (b['P_MW'], b['bus']) for b in sm['bess'])
            line += ' | worst f %.3f Hz | ripple %.1f %% | feasible=%s' % (
                sm['worst']['f_min'], 100 * sm['worst'].get('bess_ripple_frac', 0.0), sm['worst']['feasible'])
        lines.append(line)
    report = os.path.join(HERE, 'IN14_FINAL_overview.txt')
    with open(report, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    zpath = os.path.join(HERE, 'IN14_FINAL_ALL.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(report, 'IN14_FINAL_overview.txt')
        for name, status, dt in done:
            folder = os.path.join(HERE, name)
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.endswith(('.outx', '.out')):
                        continue                          # channel files are large and not needed
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, HERE))
    print('\n'.join(lines))
    print('\nAll results zipped into', zpath, '- send this file.')

def main():
    global MODE
    if len(sys.argv) == 4:                         # worker: python script.py <mode> <n_bess> <seed>
        MODE = sys.argv[1]
        configure(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
        check_settings(); psse_start(); run_one(); return
    if MODE == 'parallel':
        run_parallel(); return
    if MODE == 'all':
        run_all(); return
    configure(MODE, N_BESS, SEED)
    check_settings()
    psse_start()
    if MODE == 'diag':
        diag(); return
    run_one()

def run_one():
    t0 = time.time()
    if MODE == 'pso':
        best, hist = pso()
        units, R = best['units'], best['R']
    else:
        units, R, hist = BESS_CHECK['units'], BESS_CHECK['R'], []
    J, info = evaluate(units, R)
    wb = info['res']
    # no-BESS references: the fixed contingencies plus the same busbar outages without a BESS
    nob = []
    for k, c in enumerate(contingencies(units)):
        try:
            nob.append(run_case([], R, c, 'noBESS_%d' % k))
        except Exception as e:
            print('  no-BESS reference %s failed (%s)' % (c[0], e)); nob.append(_failed_result([], c))
    print('\n%-44s %-22s %-32s %s' % ('Contingency', 'no BESS: nadir / RoCoF', 'with BESS: nadir / RoCoF / peak',
                                       'SOC end of run -> at AGC'))
    for a, b in zip(nob, wb):
        socs = '  '.join('%.3f->%.3f' % (s[-1], g) for s, g in zip(b['soc'], b['soc_agc']))
        print('%-44s %7.3f Hz / %4.2f Hz/s   %7.3f Hz / %4.2f Hz/s / %.2f Hz (%s)   %s%s' % (
            b['name'], a['nadir'], a['rocof'], b['nadir'], b['rocof'], b['f_over'], b['event'], socs,
            ('   (lost buses: %s)' % b['lost']) if b['lost'] else ''))
    print('BESS: %s | energy %s MWh | cost %.2f M$ | worst f %.3f Hz | SOC %.3f-%.3f (window %.2f-%.2f) | '
          'stable=%s | feasible=%s | controller %s' % (
              fmt(units, R), '+'.join('%.1f' % e for e in info['E']), info['cost'], info['fmin'],
              info['soc']['lo'], info['soc']['hi'], SOC_MIN, SOC_MAX, info['stable'], info['viol'] == 0, BESS_EC_MODEL))
    save_series('noBESS', nob); save_series('BESS', wb); save_soc(wb, units)
    plot(nob, wb, 'PSS(R)E: ' + fmt(units, R, info['E']))
    plot_soc(wb, units, info['E'])
    with open(os.path.join(WORK, 'summary.json'), 'w') as fh:
        json.dump(dict(mode=MODE, n_bess=N_BESS, seed=SEED, bess_controller=BESS_EC_MODEL,
                       soc_settings=dict(SOC_INI=SOC_INI, SOC_MIN=SOC_MIN, SOC_MAX=SOC_MAX, ETA_DIS=ETA_DIS,
                                         ETA_CH=ETA_CH, E_FIXED_H=E_FIXED_H, T_SECONDARY_s=T_SECONDARY),
                       bess=[dict(bus=u['bus'], P_MW=u['P'], E_MWh=e, need_discharge_MWh=nd, need_charge_MWh=nc)
                             for u, e, nd, nc in zip(units, info['E'], info['soc']['need_dis_MWh'],
                                                     info['soc']['need_chg_MWh'])],
                       droop_R_Hz=R, cost_Musd=info['cost'],
                       worst=dict(f_min=info['fmin'], rocof=info['rocof'], bess_ripple_frac=info['ripple'], soc_min=info['soc']['lo'],
                                  soc_max=info['soc']['hi'], stable=info['stable'], feasible=info['viol'] == 0),
                       contingencies=[dict(name=b['name'], nadir_noBESS=a['nadir'], rocof_noBESS=a['rocof'],
                                           nadir_BESS=b['nadir'], rocof_BESS=b['rocof'], peak_BESS=b['f_over'],
                                           event=b['event'], lost_buses=b['lost'],
                                           soc_end=[s[-1] for s in b['soc']], soc_at_agc=b['soc_agc'])
                                      for a, b in zip(nob, wb)],
                       pso_history=hist, runtime_s=time.time() - t0), fh, indent=2)
    print('Results in', WORK)


if os.environ.get('IN14_NO_MAIN') != '1':          # PSS(R)E runs the file directly
    main()
