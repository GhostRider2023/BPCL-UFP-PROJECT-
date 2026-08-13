"""
Fluid Properties — rho(T,P), mu(T), Cp(T)
==========================================

The coupled thermal-hydraulic solver needs the fluid's properties as *functions
of the local state*, not as constants. This module provides them.

Why this module exists
----------------------
Temperature and pressure are coupled through the fluid properties:

    T  ->  mu(T)   ->  Re  ->  f  ->  dP/dx        (cooler oil is thicker, so it
                                                     rubs harder)
    T  ->  rho(T)  ->  u = m_dot/(rho*A)           (denser oil moves slower)
    P  ->  rho(P)  ->  compressibility             (squeezed oil is denser)

Without these as callables you cannot integrate the energy and momentum
equations together, and the current codebase does not — it solves heat transfer
with a constant density, then hydraulics with a constant temperature.

Density model
-------------
Density follows directly from the API MPMS volume correction factors, so it is
consistent by construction with the volumes reported downstream:

    rho(T, P) = rho_60 * CTL(T) * CPL(P)

  CTL (API MPMS 11.1) < 1 when T > 15 C  -> warm oil is less dense.  [validated]
  CPL (API MPMS 11.2.1) > 1 when P > 0   -> squeezed oil is denser.

Standard volume is then invariant by construction:

    V_std = V_gross * CTL * CPL = mass / rho_60   = constant

which is exactly the behaviour the project objective requires the simulator to
demonstrate.

Viscosity model
---------------
Andrade / Arrhenius two-point fit: ln(mu) = A + B/T, anchored on the product's
measured viscosity at 20 C and 40 C. Already implemented and validated in
config.ProductProperties.viscosity_at_T(); wrapped here for a uniform interface.

Specific heat
-------------
Currently a per-product constant (config.cp_jkgk). Exposed as a *function* of T
so that a temperature-dependent correlation can be substituted later without
touching the solver. For refined products over 15-45 C, Cp varies by only a few
percent, so the constant is defensible for now — but the interface should not
have to change when that stops being true.

Units (SI internally, always)
-----------------------------
  T   : degrees Celsius        rho : kg/m3
  P   : Pascals (gauge)        mu  : Pa.s
  Cp  : J/(kg.K)
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import PRODUCTS, T_BASE_C, ProductProperties
from model.vcf import compute_ctl

PA_PER_KPA = 1.0e3


# ═══════════════════════════════════════════════════════════════════
# CPL — Correction for Pressure on Liquid (API MPMS 11.2.1M)
# ═══════════════════════════════════════════════════════════════════


def compressibility_factor(
    rho_60_kgm3: float,
    T_C: float,
) -> float:
    """Isothermal compressibility factor F [1/kPa], API MPMS 11.2.1M.

        F = exp(-1.62080 + 0.00021592*t
                + 0.87096e6 / rho_60^2
                + 4.2092e3 * t / rho_60^2)  x 1e-6

    with t in degrees Celsius and rho_60 in kg/m3.

    Source: API MPMS Chapter 11.2.1M / ASTM D1250-19 (metric edition).

    Notes
    -----
    For petrol at 750 kg/m3 and 30 C this gives F ~= 1.17e-6 /kPa, i.e. an
    effective bulk modulus of ~850 MPa. Lighter products are more compressible
    (larger F), which is why F depends on rho_60.
    """
    if rho_60_kgm3 <= 0:
        raise ValueError(f"rho_60 must be positive, got {rho_60_kgm3}")

    r2 = rho_60_kgm3**2
    exponent = -1.62080 + 0.00021592 * T_C + 0.87096e6 / r2 + 4.2092e3 * T_C / r2
    return math.exp(exponent) * 1.0e-6


def compute_cpl(
    rho_60_kgm3: float,
    T_C: float,
    P_Pa: float,
) -> float:
    """Pressure correction factor CPL (dimensionless), API MPMS 11.2.1.

        CPL = 1 / (1 - F * P)

    P is GAUGE pressure. At atmospheric (P = 0) CPL = 1 exactly.

    A note on scope
    ---------------
    `model/friction.py` carries a comment concluding CPL is "negligible" and
    excludes it. That is true for a single batch's custody volume (~0.03%) and
    false for the line's density profile, which this simulator plots. CPL shifts
    rho(x) by ~0.5% at 50 bar — clearly visible, and free to include.
    """
    if P_Pa < 0.0:
        # Sub-atmospheric: the liquid is in tension / possibly flashing.
        # CPL is not defined here; the slack-flow check should already have
        # flagged it. Clamp to 1.0 rather than return nonsense.
        return 1.0

    F = compressibility_factor(rho_60_kgm3, T_C)  # [1/kPa]
    FP = F * (P_Pa / PA_PER_KPA)  # dimensionless

    if FP >= 0.9:
        raise ValueError(
            f"CPL diverges: F*P = {FP:.3f} at P = {P_Pa / 1e5:.1f} bar. "
            f"Pressure is far outside the correlation's valid range."
        )

    return 1.0 / (1.0 - FP)


# ═══════════════════════════════════════════════════════════════════
# Density
# ═══════════════════════════════════════════════════════════════════


def density(
    rho_60_kgm3: float,
    T_C: float,
    P_Pa: float,
    product: ProductProperties,
    include_pressure: bool = True,
    T_base_C: float = T_BASE_C,
) -> float:
    """Density [kg/m3] at the local temperature and pressure.

        rho(T, P) = rho_60 * CTL(T) * CPL(P)

    Consistent by construction with the volume corrections used downstream, so
    mass and standard volume both close exactly.
    """
    ctl = compute_ctl(rho_60_kgm3, T_C, product, T_base_C)
    if not include_pressure:
        return rho_60_kgm3 * ctl

    cpl = compute_cpl(rho_60_kgm3, T_C, P_Pa)
    return rho_60_kgm3 * ctl * cpl


# ═══════════════════════════════════════════════════════════════════
# Viscosity and specific heat
# ═══════════════════════════════════════════════════════════════════


def viscosity(T_C: float, product: ProductProperties) -> float:
    """Dynamic viscosity [Pa.s] via the Andrade two-point fit.

    ln(mu) = A + B/T, anchored on the product's 20 C and 40 C viscosities.
    Delegates to the validated implementation in config.ProductProperties.
    """
    return product.viscosity_at_T(T_C)


def specific_heat(T_C: float, product: ProductProperties) -> float:
    """Specific heat capacity [J/(kg.K)].

    Currently the product's constant value. Kept as a function of T so a
    correlation can be dropped in later without changing the solver's
    signature. `T_C` is deliberately unused for now.
    """
    return product.cp_jkgk


# ═══════════════════════════════════════════════════════════════════
# Bundled state — what the solver asks for at each step
# ═══════════════════════════════════════════════════════════════════


def fluid_state(
    rho_60_kgm3: float,
    T_C: float,
    P_Pa: float,
    product: ProductProperties,
    include_pressure: bool = True,
) -> dict:
    """Every fluid property the solver needs at one (T, P), in one call.

    Returns
    -------
    dict with keys: rho_kgm3, mu_Pas, cp_JkgK, CTL, CPL
    """
    ctl = compute_ctl(rho_60_kgm3, T_C, product)
    cpl = compute_cpl(rho_60_kgm3, T_C, P_Pa) if include_pressure else 1.0

    return {
        "rho_kgm3": rho_60_kgm3 * ctl * cpl,
        "mu_Pas": viscosity(T_C, product),
        "cp_JkgK": specific_heat(T_C, product),
        "CTL": ctl,
        "CPL": cpl,
    }


if __name__ == "__main__":
    from model.ufp import resolve_rho_60

    print("Fluid properties — sanity sweep")
    print("=" * 72)
    for name in ("petrol", "diesel", "atf"):
        p = PRODUCTS[name]
        r60 = resolve_rho_60(name, p.density_ref_kgm3, 15.0, density_is_at_base=True)
        F = compressibility_factor(r60, 30.0)
        print(f"\n{p.name}   rho_60 = {r60:.1f} kg/m3")
        # 1/F is in kPa; convert to MPa.
        print(f"  F (30 C) = {F:.3e} /kPa   -> bulk modulus ~ {1.0 / F / 1e3:.0f} MPa")
        print(f"  {'T [C]':>6} {'P [bar]':>8} {'rho':>9} {'mu [cP]':>9} {'CTL':>9} {'CPL':>9}")
        for T in (15.0, 30.0, 45.0):
            for P_bar in (0.0, 50.0):
                s = fluid_state(r60, T, P_bar * 1e5, p)
                print(
                    f"  {T:6.1f} {P_bar:8.1f} {s['rho_kgm3']:9.2f} "
                    f"{s['mu_Pas'] * 1000:9.3f} {s['CTL']:9.6f} {s['CPL']:9.6f}"
                )
