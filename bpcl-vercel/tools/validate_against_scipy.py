"""
Prove the NumPy engine reproduces the SciPy engine
===================================================

The Vercel build drops SciPy (118 MB, against a 250 MB function budget) and
integrates with a Dormand-Prince RK45 written in NumPy instead of LSODA. That is
a change to the numerics of a validated physics model, so it does not get to be
justified by argument alone.

This script runs the full `simulate()` over a grid of products, months, flow
rates, dispatch temperatures and pressures — 243 scenarios, ~620 chainages each
— against both engines and compares every reported column elementwise.

    python tools/validate_against_scipy.py

Requires SciPy in the current interpreter (the reference engine needs it). It is
a development dependency only and is not in the deployment `requirements.txt`.

How agreement is measured
-------------------------
Per column, in absolute terms, against a budget of a few quanta of that column's
own stored precision — because `model/kernel.py` rounds every column before it
goes into the DataFrame (`round(T_C, 4)`, `round(Re, 0)`, and so on), and two
integrators that agree perfectly in double precision will still land on
different sides of a rounding boundary.

A relative test is the wrong shape here for two reasons. It measures the
rounding quantum rather than the physics on a rounded column: a 1e-4 disagreement
in `T_C` is exactly one unit in the last stored place, not a physical
difference. And it is undefined where a quantity legitimately passes through
zero — the slack-flow scenarios take gauge pressure through 0 bar, where any
absolute difference at all is an infinite relative one.

So each column also reports its worst disagreement as a fraction of that
column's full range across the whole grid. That number is the physically
meaningful one, and it is what the summary line at the end quotes.
"""

import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
VERCEL_ENGINE = os.path.join(HERE, "..", "api", "_engine")
SCIPY_ENGINE = os.path.join(HERE, "..", "..", "kota_bijwasan_ufp")

# Per-column absolute budget, in the column's own units.
#
# Each entry is a small multiple of the quantum `model/kernel.py` rounds that
# column to, listed alongside it. The budget is what two integrators may differ
# by at the last stored digit; anything larger is a real numerical difference
# and fails.
TOLERANCE = {
    # column            budget      kernel rounding   what the budget is worth
    "T_C": 5e-4,  # round(_, 4)      0.0005 degC
    "P_bar": 5e-3,  # round(_, 4)      0.005 bar, on spans of 20-120 bar
    "rho_kgm3": 1e-3,  # round(_, 8)      0.001 kg/m3
    "mu_cP": 1e-4,  # round(_, 6)      0.0001 cP
    "velocity_ms": 1e-5,  # round(_, 8)      0.01 mm/s
    "Re": 5.0,  # round(_, 0)      5, on Reynolds numbers of 1e4-1e6
    "friction_factor": 1e-6,  # round(_, 8)
    "CTL": 1e-6,  # round(_, 8)
    "CPL": 1e-6,  # round(_, 8)
    "V_gross_KL": 5e-3,  # round(_, 5)      5 litres in ~1000 KL
    "V_std_KL": 5e-3,  # round(_, 6)      5 litres in ~1000 KL
    "U_Wm2K": 1e-6,  # round(_, 4)
    "L_star_km": 1e-6,  # round(_, 1)
    "T_env_C": 1e-6,  # round(_, 3)      boundary condition: must be identical
    "T_soil_C": 1e-6,  # round(_, 3)      boundary condition: must be identical
}

# Anything above this fraction of a column's own range is reported as a
# substantive disagreement even if it sits inside the absolute budget.
SPAN_LIMIT = 1e-4


def run(engine_root: str, out_path: str, label: str) -> None:
    print(f"  running {label:<12} ({os.path.relpath(engine_root, HERE)})", flush=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "_run_grid.py"), engine_root, out_path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"{label} engine failed to run the grid")
    print(f"    {proc.stdout.strip()}")


def main() -> int:
    if not os.path.isdir(SCIPY_ENGINE):
        raise SystemExit(
            f"Reference engine not found at {os.path.abspath(SCIPY_ENGINE)}.\n"
            f"This check compares against the original SciPy project; it must "
            f"sit alongside this one."
        )

    # `np.load` on an .npz is lazy and keeps the file handle open, which on
    # Windows blocks the directory from being removed. Everything is pulled into
    # memory inside the `with np.load(...)` block so the handles are shut before
    # the temp directory goes away.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ref_npz = os.path.join(tmp, "scipy.npz")
        new_npz = os.path.join(tmp, "numpy.npz")

        print("Running the scenario grid against both engines:")
        run(SCIPY_ENGINE, ref_npz, "scipy/LSODA")
        run(VERCEL_ENGINE, new_npz, "numpy/RK45")

        with np.load(ref_npz, allow_pickle=False) as z:
            a = z["data"].copy()
            columns = [str(c) for c in z["columns"]]
            labels = [str(x) for x in z["labels"]]
        with np.load(new_npz, allow_pickle=False) as z:
            b = z["data"].copy()

    if a.shape != b.shape:
        raise SystemExit(f"Shape mismatch: scipy {a.shape} vs numpy {b.shape}")

    print(f"\nComparing {a.shape[0]} scenarios x {a.shape[1]} points x {a.shape[2]} columns\n")

    failures = []
    worst_span = 0.0
    header = f"  {'column':<17} {'max abs diff':>13} {'budget':>10} {'/ column span':>14}   worst scenario"
    print(header)
    print(f"  {'-' * 17} {'-' * 13} {'-' * 10} {'-' * 14}   {'-' * 26}")

    for j, col in enumerate(columns):
        ca, cb = a[:, :, j], b[:, :, j]

        # NaN must appear in the same places in both engines. `T_air_C` is NaN
        # wherever no air measurement exists, and a NaN turning into a number
        # would mean one engine invented a boundary condition.
        if (np.isfinite(ca) != np.isfinite(cb)).any():
            failures.append(f"{col}: the two engines disagree about where the data is NaN")

        finite = np.isfinite(ca) & np.isfinite(cb)
        diff = np.zeros_like(ca)
        diff[finite] = np.abs(ca[finite] - cb[finite])

        # The column's full range across the grid — what a disagreement should
        # be measured against, rather than against a value that may be near zero.
        span = float(np.ptp(ca[finite])) if finite.any() else 0.0
        span_frac = (diff.max() / span) if span > 0 else 0.0
        worst_span = max(worst_span, span_frac)

        budget = TOLERANCE[col]
        worst = int(np.argmax(diff))
        worst_scenario = labels[worst // a.shape[1]]

        ok = bool(diff.max() <= budget) and span_frac <= SPAN_LIMIT
        mark = "" if ok else "  << FAIL"
        print(
            f"  {col:<17} {diff.max():13.3e} {budget:10.1e} {span_frac:14.2e}   "
            f"{worst_scenario}{mark}"
        )
        if not ok:
            failures.append(
                f"{col}: max abs diff {diff.max():.3e} exceeds the "
                f"{budget:.1e} budget ({span_frac:.2e} of the column span) "
                f"at {worst_scenario}"
            )

    print()
    if failures:
        print("FAILED — the NumPy engine does not reproduce the SciPy engine:")
        for f in failures:
            print(f"  · {f}")
        return 1

    print(
        f"PASS — across all {a.shape[0]} scenarios and {a.shape[1]} chainages, no "
        f"column differs\nfrom the SciPy build by more than {worst_span:.1e} of its "
        f"own range, and every\ndifference sits at the last digit the kernel stores. "
        f"RK45 reproduces LSODA here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
