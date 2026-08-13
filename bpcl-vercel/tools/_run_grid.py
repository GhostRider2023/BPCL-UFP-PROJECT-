"""
Run the scenario grid against one engine root and dump the numbers.
====================================================================

Invoked twice by `validate_against_scipy.py` — once against the original
SciPy/LSODA project, once against the vendored NumPy/RK45 engine — in separate
processes, because both engines expose the same top-level module names
(`config`, `model`, `geo`) and cannot coexist in one interpreter.

    python tools/_run_grid.py <engine_root> <output.npz>

The grid is deliberately deterministic: no air temperature is fetched, so the
boundary condition is the ERA5 soil profile alone and two runs of this script
differ only by the integrator.
"""

import itertools
import os
import sys
import warnings

import numpy as np

ENGINE_ROOT = os.path.abspath(sys.argv[1])
OUT_PATH = sys.argv[2]

sys.path.insert(0, ENGINE_ROOT)

from geo.route import Route  # noqa: E402
from model.kernel import SimulationInputs, simulate  # noqa: E402
from model.soil_profile import _load_soil_csv, available_months  # noqa: E402

# Columns the dashboard actually reads. Compared elementwise across every
# chainage of every scenario.
COLUMNS = [
    "T_C",
    "P_bar",
    "rho_kgm3",
    "mu_cP",
    "velocity_ms",
    "Re",
    "friction_factor",
    "CTL",
    "CPL",
    "V_gross_KL",
    "V_std_KL",
    "U_Wm2K",
    "L_star_km",
    "T_env_C",
    "T_soil_C",
]

MONTHS = available_months()


def grid():
    """A spread wide enough to exercise every branch that matters.

    Includes the infeasible corner (diesel at 465 m3/hr from 70 bar) on purpose:
    the slack-flow path runs the integrator into the region where the pressure
    solution is steepest, which is exactly where two integrators are most likely
    to disagree.
    """
    products = ["petrol", "diesel", "atf"]
    months = [MONTHS[0], MONTHS[len(MONTHS) // 2], MONTHS[-1]]
    flows = [150.0, 349.0, 465.0]
    temps = [22.0, 40.0, 52.0]
    pressures = [40.0, 70.0, 100.0]

    for p, m, q, t, pr in itertools.product(products, months, flows, temps, pressures):
        yield SimulationInputs(
            product=p,
            T_dispatch_C=t,
            V_dispatch_KL=1000.0,
            flow_rate_m3hr=q,
            month=m,
            P_dispatch_bar=pr,
        )


def main():
    route = Route.from_csv()
    soil_all = _load_soil_csv()

    stacks = []
    labels = []
    with warnings.catch_warnings():
        # The slack-flow scenarios warn by design; that is the model working.
        warnings.simplefilter("ignore")
        for inp in grid():
            soil = (
                soil_all[soil_all["month"] == inp.month]
                .rename(columns={"waypoint_km": "km"})
                .reset_index(drop=True)
            )
            df = simulate(inp, route, soil)
            stacks.append(df[COLUMNS].to_numpy(dtype=float))
            labels.append(
                f"{inp.product}/m{inp.month}/{inp.flow_rate_m3hr:.0f}"
                f"/{inp.T_dispatch_C:.0f}C/{inp.P_dispatch_bar:.0f}bar"
            )

    np.savez_compressed(
        OUT_PATH,
        data=np.stack(stacks),  # (n_scenarios, n_points, n_columns)
        columns=np.array(COLUMNS),
        labels=np.array(labels),
    )
    print(f"{len(stacks)} scenarios x {stacks[0].shape[0]} points -> {OUT_PATH}")


if __name__ == "__main__":
    main()
