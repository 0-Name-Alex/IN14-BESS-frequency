"""
IN-14 BESS paper - batch of the PSS(R)E studies requested in the second review (Journal of Energy Storage).

Put this file in the SAME folder as  in14_psse_bess_extra.py  (it imports that file and reuses its models,
contingencies, SOC calculation and PSO). All faults are 3-phase short circuits as in the paper.

Two ways to run it:
  * PSS(R)E GUI: drag and drop this file into PSS(R)E (or File > Run Program). The jobs then run one after
    another inside the GUI (about 5-6 hours in total).
  * Faster: from a normal Command Prompt,  python in14_review2.py  - the launcher then starts N_WORKERS PSS(R)E
    processes at a time, one job each (about 1.5 hours with 4). Every job writes its own folder under
IN14_REVIEW2/, and at the end everything is summarised in IN14_REVIEW2/overview.txt and zipped into
IN14_REVIEW2_ALL.zip  -> send that file.

Studies (reviewer comment in brackets)
  pso_split_sN / pso_single_sN   10 PSO seeds each for split and single BESS                      (6)
  bench_single                    every bus: smallest feasible single BESS by bisection (benchmark) (6)
  w_*                             PSO with changed objective weights / design limits                (7)
  droop_sweep                     final designs with R = 0.05 ... 0.30 Hz                           (10)
  ride_through, band_voltage      REECC1 ride-through x droop sweep; 5 bands with voltage recovery   (4)
  pen_20, pen_58, inertia_restored, headroom_restored   penetration level and separated effects    (3)
All BESS runs use the REECC1 storage controller. Contingencies as in the paper: R1-R3 + own-busbar outage.
  conventional, stages            remaining preliminary values of the paper (Table X, Sec. VI-C)
Set RUN_ONLY = ['droop_sweep'] (for example) to run only some jobs; try N_WORKERS = 1 if PSS(R)E Xplore
refuses to start several copies.
"""
import os, sys, json, time, math, glob, subprocess, traceback

N_WORKERS = 4                         # PSS(R)E processes at the same time (<= CPU cores; 1 = one after another)
RUN_ONLY = None                       # None = all jobs, or a list of job names
PSSE_PSSPY = r"C:\Program Files\PTI\PSSE36\36.2\PSSPY39"   # searched automatically if this folder does not exist

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'IN14_REVIEW2')
os.environ['IN14_NO_MAIN'] = '1'
sys.path.insert(0, HERE)
import in14_psse_bess_extra as ex     # models, contingencies, PSO, SOC (main() is not executed)

# ---------------------------------------------------------------- objective with explicit, changeable weights
W_V, ALPHA_E, W_NADIR = 10.0, 1.5, 0.05   # violation weight, MWh weight in the size index, nadir term weight
S_REF_P = 300.0                            # size index of a 300 MW unit with the 15 min minimum energy is the reference

def evaluate(units, R):
    """Same as the paper, eq. (6): J = S/S_ref + W_V*V + W_NADIR*(50 - f_nadir), S = sum(P + ALPHA_E*E)."""
    res = []
    for c in ex.contingencies(units):
        try:
            res.append(ex.run_case(units, R, c, 'eval'))
        except Exception as e:
            print('  design %s, %s: simulation failed (%s) - treated as collapse' % (ex.fmt(units, R), c[0], e))
            res.append(ex._failed_result(units, c))
    fmin = min(min(r['fbus'], r['nadir']) for r in res)
    rc = max(r['rocof'] for r in res)
    over_ex = max(r['f_over'] - r['over_lim'] for r in res)
    swing = max(r['f_swing'] for r in res)
    ripple = max(r['p_ripple'] for r in res) / max(sum(u['P'] for u in units), 1e-6)
    E, soc = ex.soc_analysis(units, res)
    S = sum(u['P'] + ALPHA_E * e for u, e in zip(units, E))
    S_ref = S_REF_P + ALPHA_E * ex.MIN_DURATION_H * S_REF_P
    viol = (max(0.0, ex.F_MIN - fmin) / 0.1 + max(0.0, rc - ex.ROCOF_MAX) / 0.1
            + max(0.0, over_ex) / 0.1 + max(0.0, swing - ex.F_SWING_MAX) / 0.05 + soc['viol']
            + max(0.0, ripple - ex.P_RIPPLE_MAX) / 0.05)
    J = S / S_ref + W_V * viol + W_NADIR * (ex.F0 - min(r['nadir'] for r in res))
    return J, dict(units=units, R=R, E=E, cost=S, fmin=fmin, rocof=rc, viol=viol, soc=soc, ripple=ripple,
                   swing=swing, over_ex=over_ex,
                   stable=(over_ex <= 0 and swing <= ex.F_SWING_MAX and ripple <= ex.P_RIPPLE_MAX), res=res)

ex.evaluate = evaluate                 # PSO and all studies of the imported file now use this objective

FINAL_SPLIT = [dict(bus=5, P=113.26), dict(bus=9, P=27.17)]
FINAL_SINGLE = [dict(bus=5, P=142.65)]
REN = (list(ex.SYNC), list(ex.RE), list(ex.SHUNTS))

def set_case(sync=None, re=None, shunts=None):
    ex.SYNC = list(sync if sync is not None else REN[0])
    ex.RE = list(re if re is not None else REN[1])
    ex.SHUNTS = list(shunts if shunts is not None else REN[2])

def dump(name, obj):
    with open(os.path.join(ex.WORK, name), 'w') as fh:
        json.dump(obj, fh, indent=1)

def csv(name, cols):
    with open(os.path.join(ex.WORK, name), 'w') as fh:
        fh.write(','.join(n for n, _ in cols) + '\n')
        for i in range(len(cols[0][1])):
            fh.write(','.join('%.5f' % c[i] for _, c in cols) + '\n')

def brief(info):
    return dict(fmin=info['fmin'], rocof=info['rocof'], swing=info['swing'], ripple_pct=100 * info['ripple'],
                soc_lo=info['soc']['lo'], soc_hi=info['soc']['hi'], E_MWh=info['E'], stable=info['stable'],
                feasible=info['viol'] == 0, per_contingency=[dict(name=r['name'], nadir=r['nadir'], fbus=r['fbus'],
                rocof=r['rocof'], peak=r['f_over'], f10=r['f_end']) for r in info['res']])

# ---------------------------------------------------------------- jobs
def job_pso(n_bess, seed, tag=None):
    ex.configure('pso', n_bess, seed)
    ex.WORK = os.path.join(OUT, tag or ('pso_%s_s%d' % ('split' if n_bess == 2 else 'single', seed)))
    os.makedirs(ex.WORK, exist_ok=True)
    ex.run_one()                       # PSO + final evaluation + no-BESS references + summary.json + CSV + plots

def job_bench_single():
    """Benchmark for the PSO: at every bus, the smallest feasible single BESS (bisection on P, R = 0.15 Hz)."""
    rows = []
    for b in range(1, 15):
        J, top = evaluate([dict(bus=b, P=300.0)], 0.15)
        if top['viol'] > 0:
            rows.append(dict(bus=b, feasible=False, P_min=None, fmin_at_300MW=top['fmin'])); print(rows[-1]); continue
        lo, hi = 10.0, 300.0
        J, low = evaluate([dict(bus=b, P=lo)], 0.15)
        if low['viol'] == 0:
            hi = lo
        while hi - lo > 1.0:
            mid = 0.5 * (lo + hi)
            J, m = evaluate([dict(bus=b, P=mid)], 0.15)
            if m['viol'] == 0:
                hi = mid
            else:
                lo = mid
        J, best = evaluate([dict(bus=b, P=hi)], 0.15)
        rows.append(dict(bus=b, feasible=True, P_min=hi, E_MWh=best['E'][0], fmin=best['fmin'], rocof=best['rocof'],
                         soc_lo=best['soc']['lo']))
        print(rows[-1])
    dump('bench_single.json', rows)

def job_weights(tag):
    """Reviewer 7: same PSO (split, seed 1) with one weight or design limit changed."""
    global W_V, ALPHA_E
    change = {'w_V5': ('W_V', 5.0), 'w_V20': ('W_V', 20.0), 'w_E1.0': ('ALPHA_E', 1.0), 'w_E2.0': ('ALPHA_E', 2.0),
              'w_fmin49.6': ('F_MIN', 49.6), 'w_rocof1.0': ('ROCOF_MAX', 1.0)}[tag]
    if change[0] in ('W_V', 'ALPHA_E'):
        globals()[change[0]] = change[1]
    else:
        setattr(ex, change[0], change[1])
    job_pso(2, 1, tag)
    dump('setting.json', dict(changed=change[0], value=change[1]))

def job_droop_sweep():
    """Reviewer 10: final split and single designs with the droop varied below and above the 0.15 Hz bound."""
    out = []
    for name, units in (('split', FINAL_SPLIT), ('single', FINAL_SINGLE)):
        for R in (0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30):
            J, info = evaluate(units, R)
            out.append(dict(design=name, R=R, **brief(info))); print(name, R, info['fmin'], info['stable'])
            if name == 'split' and R in (0.05, 0.10, 0.15):
                r = info['res'][0]
                csv('droop_R%.2f_R1.csv' % R, [('time', r['t']), ('fCOI', r['fcoi']), ('PBESS_MW', r['pb'])])
    dump('droop_sweep.json', out)

def job_ride_through():
    """Reviewer 4: ride-through band x droop sweep with the REECC1 storage controller only (232 MW at bus 1, R1, R2)."""
    band0 = (ex.BESS_VDIP, ex.BESS_VUP)
    ex.BESS_EC_MODEL = 'REECC1'
    ex.CONT_OVERRIDE = list(ex.FIXED_CONT[:2])
    units, out = [dict(bus=1, P=232.0)], []
    for band in ((0.9, 1.1), (0.5, 1.3)):
        for R in (0.10, 0.15, 0.20, 0.30, 0.50):
            ex.BESS_VDIP, ex.BESS_VUP = band
            J, info = evaluate(units, R)
            out.append(dict(Vdip=band[0], Vup=band[1], R=R, peak=max(r['f_over'] for r in info['res']),
                            meets_limits=info['stable'] and info['fmin'] >= ex.F_MIN and info['rocof'] <= ex.ROCOF_MAX,
                            **brief(info)))
            print(band, R, info['fmin'], info['stable'])
            if R == 0.10:                                  # time series for the limit-cycle / stable-response figure
                r = info['res'][0]
                csv('rt_REECC1_%.1f-%.1f_R1.csv' % band, [('time', r['t']), ('fCOI', r['fcoi']), ('PBESS_MW', r['pb'])]
                    + [('VBESS_pu', r['vb_units'][0])])
    ex.BESS_VDIP, ex.BESS_VUP = band0; ex.CONT_OVERRIDE = None
    dump('ride_through_REECC1.json', out)

def _v_recovery(t, v, t_clear, level=0.9):
    """Time after clearing until the voltage stays above `level` pu (None if it never does)."""
    last_below = None
    for x, y in zip(t, v):
        if x >= t_clear and y < level:
            last_below = x
    return 0.0 if last_below is None else (last_below - t_clear if last_below < t[-1] - 1e-6 else None)

def job_band_voltage():
    """Reviewer 4: final split design with five ride-through bands; frequency AND voltage recovery."""
    band0 = (ex.BESS_VDIP, ex.BESS_VUP); out = []
    for band in ((0.9, 1.1), (0.8, 1.2), (0.7, 1.2), (0.6, 1.25), (0.5, 1.3)):
        ex.BESS_VDIP, ex.BESS_VUP = band
        J, info = evaluate(FINAL_SPLIT, 0.15)
        vr = []
        for r in info['res']:
            for k, v in enumerate(r['vb_units']):
                if len(v) > 3:
                    vr.append(dict(contingency=r['name'], unit=k + 1, v_min=min(v),
                                   t_recover_0p9=_v_recovery(r['t'], v, ex.T_CLEAR)))
        out.append(dict(Vdip=band[0], Vup=band[1], voltage=vr, **brief(info)))
        r = info['res'][0]
        csv('band_%.1f-%.2f_R1.csv' % band, [('time', r['t']), ('fCOI', r['fcoi']), ('PBESS_MW', r['pb'])]
            + [('VBESS%d_pu' % (k + 1), v) for k, v in enumerate(r['vb_units'])])
        print(band, info['fmin'], info['stable'])
    ex.BESS_VDIP, ex.BESS_VUP = band0
    dump('band_voltage.json', out)

# operating points for reviewer 3 (bus, PG, QT, QB, VS, MBASE, PT, PB, kind, H) and (bus, P, QT, QB, VS, MBASE)
# 20 %: one 210 MW coal unit at bus 1 (as in the base case, so R3 again trips one unit) and the gas unit back in service
SYNC_20 = [(1, 0.0, 130, -60, 1.04, 247, 210, 116, 'coal', 3.2), (2, 140.0, 130, -60, 1.03, 247, 210, 116, 'coal', 3.2),
           (3, 90.0, 100, -50, 1.02, 176, 150, 60, 'gas', 5.0),
           (6, 50.0, 70, -40, 1.02, 118, 100, 0, 'hydro', 3.5), (8, 30.0, 45, -25, 1.02, 71, 60, 0, 'hydro', 3.5)]
RE_20 = [(12, 69.0, 23, -23, 1.02, 74), (14, 46.0, 15, -15, 1.02, 48)]          # 115 MW = 20.1 % of load
SYNC_58 = [(1, 0.0, 130, -60, 1.04, 247, 210, 116, 'coal', 3.2), (2, 116.0, 130, -60, 1.03, 247, 210, 116, 'coal', 3.2),
           (6, 10.0, 70, -40, 1.02, 118, 100, 0, 'hydro', 3.5), (8, 5.0, 45, -25, 1.02, 71, 60, 0, 'hydro', 3.5)]
RE_58 = [(12, 198.0, 66, -66, 1.02, 211), (14, 132.0, 44, -44, 1.02, 139)]      # 330 MW = 57.6 % of load

def _scaled_inertia(sync, target=3913.0):
    k = target / sum(g[9] * g[5] for g in sync)
    return [g[:9] + (round(g[9] * k, 3),) for g in sync]

def _more_headroom(sync):
    return [g[:6] + (float(g[5]),) + g[7:] if g[8] == 'coal' else g for g in sync]   # coal limit = MVA rating

def job_case(tag):
    """Reviewer 3: no-BESS response and PSO (split, seed 1) at another penetration or with one effect removed."""
    sync, re = {'pen_20': (SYNC_20, RE_20), 'pen_58': (SYNC_58, RE_58),
                'inertia_restored': (_scaled_inertia(REN[0]), REN[1]),
                'headroom_restored': (_more_headroom(REN[0]), REN[1])}[tag]
    set_case(sync, re)
    ex.configure('pso', 2, 1)
    ex.WORK = os.path.join(OUT, tag); os.makedirs(ex.WORK, exist_ok=True)
    dump('case.json', dict(sync=sync, re=re, kinetic_energy_MWs=sum(g[9] * g[5] for g in sync),
                           re_share=sum(r[1] for r in re) / 573.0))
    ex.run_one()

def job_conventional():
    ex._REN = REN
    ex.study_conventional()

def job_stages():
    ex._REN = REN
    ex.study_stages()

JOBS = {}
for s in range(1, 11):
    JOBS['pso_split_s%d' % s] = (job_pso, (2, s))
    JOBS['pso_single_s%d' % s] = (job_pso, (1, s))
for t in ('w_V5', 'w_V20', 'w_E1.0', 'w_E2.0', 'w_fmin49.6', 'w_rocof1.0'):
    JOBS[t] = (job_weights, (t,))
for t in ('pen_20', 'pen_58', 'inertia_restored', 'headroom_restored'):
    JOBS[t] = (job_case, (t,))
JOBS.update(bench_single=(job_bench_single, ()), droop_sweep=(job_droop_sweep, ()),
            ride_through=(job_ride_through, ()), band_voltage=(job_band_voltage, ()),
            conventional=(job_conventional, ()), stages=(job_stages, ()))
LONG_FIRST = [k for k in JOBS if k.startswith(('pso_', 'w_', 'pen_', 'inertia', 'headroom', 'stages', 'bench'))]

# ---------------------------------------------------------------- PSS(R)E start-up and launcher
def find_psspy():
    """PSSPY folder that matches the Python running this script (psspy only loads in its own Python version)."""
    want = 'PSSPY%d%d' % sys.version_info[:2]
    found = []
    for root in (os.path.dirname(PSSE_PSSPY), r"C:\Program Files\PTI", r"C:\Program Files (x86)\PTI"):
        if os.path.isdir(root):
            found += glob.glob(os.path.join(root, '**', 'PSSPY*', 'psspy.py*'), recursive=True)
    folders = sorted({os.path.dirname(f) for f in found})
    match = [f for f in folders if os.path.basename(f).upper() == want]
    if match:
        return match[-1]
    raise RuntimeError('This Python is %d.%d, so PSS(R)E needs a folder %s, but only these exist:\n  %s\n'
                       'Run the script with a matching Python (type  py -0  to list installed versions, then e.g.\n'
                       '  py -3.9 in14_review2.py ) or drag the file into the PSS(R)E window.'
                       % (sys.version_info[0], sys.version_info[1], want, '\n  '.join(folders) or '(none found)'))

def psse_paths(p):
    """Make psspy importable outside the GUI: Python path + the PSS(R)E DLL folders (PSSBIN, PSSLIB, PSSPY)."""
    base = os.path.dirname(p)
    sys.path.insert(0, p)
    for d in (os.path.join(base, 'PSSBIN'), os.path.join(base, 'PSSLIB'), p):
        if os.path.isdir(d):
            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(d)
    for f in sorted(glob.glob(os.path.join(p, 'psse3*.py'))):    # e.g. psse36.py: sets PSS(R)E paths itself
        try:
            __import__(os.path.splitext(os.path.basename(f))[0]); break
        except Exception:
            pass

def psse_init():
    psse_paths(find_psspy())
    import psspy
    psspy.psseinit(50)
    psspy.progress_output(2, os.path.join(ex.WORK, 'psse_progress.txt'), [0, 0])
    psspy.report_output(2, os.path.join(ex.WORK, 'psse_report.txt'), [0, 0])
    ex.psspy = psspy

def run_job(name):
    ex.check_settings()
    ex.WORK = os.path.join(OUT, name); os.makedirs(ex.WORK, exist_ok=True)
    psse_init()
    fn, args = JOBS[name]
    t0 = time.time()
    fn(*args)
    with open(os.path.join(OUT, name, 'DONE.txt'), 'w') as fh:
        fh.write('%.1f min\n' % ((time.time() - t0) / 60))

def _defaults():
    global W_V, ALPHA_E
    W_V, ALPHA_E = 10.0, 1.5

def run_in_gui(names, psspy):
    """Started from the PSS(R)E GUI (drag and drop / File > Run): run the jobs one after another in this process."""
    import importlib
    global ex
    os.makedirs(OUT, exist_ok=True)
    psspy.progress_output(6, '', [0, 0])                 # keep the GUI fast; results go to the job folders
    t_all, done = time.time(), []
    for k, n in enumerate(names):
        ex = importlib.reload(ex); ex.evaluate = evaluate; ex.psspy = psspy; _defaults()   # clean state per job
        ex.check_settings()
        ex.WORK = os.path.join(OUT, n); os.makedirs(ex.WORK, exist_ok=True)
        print('\n=== job %d/%d: %s ===' % (k + 1, len(names), n)); t0 = time.time()
        try:
            fn, args = JOBS[n]; fn(*args)
            open(os.path.join(ex.WORK, 'DONE.txt'), 'w').write('%.1f min\n' % ((time.time() - t0) / 60))
            done.append('%-20s %-34s %5.1f min' % (n, 'ok', (time.time() - t0) / 60))
        except Exception:
            traceback.print_exc()
            open(os.path.join(ex.WORK, 'ERROR.txt'), 'w').write(traceback.format_exc())
            done.append('%-20s %-34s %5.1f min' % (n, 'FAILED (see ERROR.txt)', (time.time() - t0) / 60))
        print(done[-1])
    _finish(done, t_all)

def launcher():
    names = [n for n in JOBS if RUN_ONLY is None or n in RUN_ONLY]
    in_gui = 'psspy' in sys.modules or not os.path.basename(sys.executable).lower().startswith('python')
    if in_gui:                                            # started inside the PSS(R)E GUI: run jobs one by one here
        import psspy
        names.sort(key=lambda n: (n not in LONG_FIRST, n))
        return run_in_gui(names, psspy)
    print('Python %d.%d -> PSS(R)E folder %s' % (sys.version_info[0], sys.version_info[1], find_psspy()))
    chk = subprocess.run([sys.executable, os.path.abspath(__file__), 'check'], cwd=HERE, capture_output=True, text=True)
    if chk.returncode != 0:
        print(chk.stderr); print('psspy cannot be loaded with this Python - nothing was started.'); return
    names.sort(key=lambda n: (n not in LONG_FIRST, n))          # long PSO jobs first, quick studies fill the gaps
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, IN14_NO_MAIN='1')
    queue, running, done, t_all = list(names), [], [], time.time()
    print('%d jobs, %d at a time. Progress of each job: IN14_REVIEW2\\<job>\\console_log.txt' % (len(names), N_WORKERS))
    while queue or running:
        while queue and len(running) < N_WORKERS:
            n = queue.pop(0); d = os.path.join(OUT, n); os.makedirs(d, exist_ok=True)
            log = open(os.path.join(d, 'console_log.txt'), 'w')
            pr = subprocess.Popen([sys.executable, os.path.abspath(__file__), 'job', n], cwd=HERE, env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
            running.append((pr, n, time.time(), log)); print('  started  %-20s (%d waiting)' % (n, len(queue)))
        time.sleep(5)
        for item in running[:]:
            pr, n, t0, log = item
            if pr.poll() is not None:
                log.close(); running.remove(item)
                ok = os.path.exists(os.path.join(OUT, n, 'DONE.txt'))
                done.append('%-20s %-34s %5.1f min' % (n, 'ok' if ok else 'FAILED (see console_log.txt)', (time.time() - t0) / 60))
                print('  finished ' + done[-1])
    _finish(done, t_all)

def _finish(done, t_all):
    lines = ['IN-14 review-2 batch finished in %.1f min' % ((time.time() - t_all) / 60), ''] + sorted(done)
    with open(os.path.join(OUT, 'overview.txt'), 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    import zipfile
    zpath = os.path.join(HERE, 'IN14_REVIEW2_ALL.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT):
            for f in files:
                if not f.endswith(('.outx', '.out', '.raw', '.dyr')):
                    full = os.path.join(root, f); z.write(full, os.path.relpath(full, HERE))
    print('\n'.join(lines)); print('\nAll results zipped into', zpath, '- send this file.')

if os.environ.get('IN14_REVIEW2_IMPORT') != '1':        # runs when started from the GUI or a Command Prompt
    if len(sys.argv) == 2 and sys.argv[1] == 'check':     # launcher test: can PSS(R)E be loaded here?
        psse_paths(find_psspy()); import psspy; psspy.psseinit(50); print('psspy OK')
    elif len(sys.argv) == 3 and sys.argv[1] == 'job':
        try:
            run_job(sys.argv[2])
        except Exception:
            traceback.print_exc()
            with open(os.path.join(OUT, sys.argv[2], 'ERROR.txt'), 'w') as fh:
                fh.write(traceback.format_exc())
            sys.exit(1)
    else:
        launcher()
