"""
UFP Quantification
===================

Computes Unaccounted-For Product (UFP) per batch by comparing
standard volumes at dispatch (Kota) and receipt (Bijwasan).

UFP = V_dispatch_std − V_receipt_std

Where standard volume is computed using API MPMS Chapter 11.1
VCF at each metering point:
  V_std = V_observed × CTL(ρ, T)

Positive UFP = product "lost" (volume appears smaller at receipt
when corrected to base temperature).

In summer: product arrives warmer → receipt volume is larger
at observed T but smaller when corrected to 15°C → thermal UFP.

Units:
  - Volume: kilolitres [KL]
  - Financial: Indian Rupees [₹]
"""

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import PRODUCTS, T_BASE_C
from model.vcf import (
    compute_alpha_60,
    compute_ctl,
    iterate_rho_60,
)


def resolve_rho_60(
    product_name: str,
    density_kgm3: float,
    density_measured_at_C: float,
    density_is_at_base: bool = False,
    T_base_C: float = T_BASE_C,
) -> float:
    """Resolve the batch's base density rho_60 ONCE, from a single measurement.

    A batch has exactly one rho_60: it is a property of the product, not of the
    meter reading it. Resolving it separately at each meter — by feeding the
    same lab density into ``iterate_rho_60`` once at T_dispatch and again at
    T_receipt — yields two different rho_60 for the same oil (767.92 vs 756.29
    kg/m3 for a petrol batch at 35 C / 22 C, an 11.6 kg/m3 spread). The two
    resulting CTLs then disagree, and the difference shows up as UFP conjured
    out of nothing: -0.209 KL on a batch constructed to conserve mass exactly.

    Resolve once, here, and reuse at every meter.

    Parameters
    ----------
    density_kgm3 : float
        The measured density.
    density_measured_at_C : float
        The temperature at which that density was measured. For custody data
        this is the dispatch meter temperature.
    density_is_at_base : bool
        True if `density_kgm3` is already referred to 15 C, in which case it
        *is* rho_60 and no iteration is needed.

    Returns
    -------
    float
        Base density rho_60 [kg/m3].
    """
    product = PRODUCTS[product_name.lower()]
    if density_is_at_base:
        return density_kgm3
    rho_60, _ = iterate_rho_60(density_kgm3, density_measured_at_C, product, T_base_C)
    return rho_60


def compute_ufp_batch(
    product_name: str,
    V_dispatch_KL: float,
    T_dispatch_C: float,
    density_kgm3: float,
    V_receipt_KL: float,
    T_receipt_C: float,
    price_per_litre: Optional[float] = None,
    T_base_C: float = T_BASE_C,
    density_is_at_base: bool = False,
) -> dict:
    """Compute UFP for a single batch.

    Returns three distinct quantities that this codebase previously conflated:

    ``UFP_net_KL``
        V_dispatch@15C - V_receipt@15C. Standard volume is a mass proxy, so
        this is the REAL unaccounted product. Under mass conservation it is
        exactly zero, however hard the batch cools — temperature correction is
        what removes the thermal effect. Non-zero means leak, theft, or
        mis-metering. This is what Phase 6 reconciles against.

    ``UFP_thermal_KL``
        The volume a loss-free batch appears to lose purely by cooling:
        V_dispatch - V_dispatch@15C / CTL_receipt. This is the artifact the
        model exists to predict, and the number to subtract from a reported
        gross UFP. Typically 1-2% of batch for a 10-15 C drop.

    ``UFP_gross_KL``
        The raw meter-to-meter difference, V_dispatch - V_receipt, i.e. what
        naive volumetric accounting sees. Exactly decomposes as
        ``gross = thermal + net / CTL_receipt``.

    Parameters
    ----------
    product_name : str
        Product type ("petrol", "diesel", "atf").
    V_dispatch_KL : float
        Dispatched volume at Kota meter [KL].
    T_dispatch_C : float
        Temperature at dispatch meter [°C].
    density_kgm3 : float
        Product density [kg/m³] (at observed temperature or base).
    V_receipt_KL : float
        Received volume at Bijwasan meter [KL].
    T_receipt_C : float
        Temperature at receipt meter [°C].
    price_per_litre : float, optional
        Product price [₹/litre]. Default: from product config.
    T_base_C : float
        Base temperature [°C]. Default: 15°C.

    Returns
    -------
    dict
        UFP results with keys:
          - V_dispatch_std_KL: dispatch volume at base T [KL]
          - V_receipt_std_KL: receipt volume at base T [KL]
          - UFP_KL: thermal UFP [KL] (positive = loss)
          - UFP_litres: UFP in litres
          - UFP_rupees: financial loss [₹]
          - UFP_percent: UFP as % of dispatched volume
          - CTL_dispatch: VCF at dispatch
          - CTL_receipt: VCF at receipt
          - T_dispatch_C, T_receipt_C: temperatures
    """
    product = PRODUCTS[product_name.lower()]

    if price_per_litre is None:
        price_per_litre = product.price_per_litre

    # ── The batch has ONE base density. Resolve it once. ──────────────
    # The density is measured at the dispatch meter, so that is the
    # temperature it must be referred from.
    rho_60 = resolve_rho_60(
        product_name=product_name,
        density_kgm3=density_kgm3,
        density_measured_at_C=T_dispatch_C,
        density_is_at_base=density_is_at_base,
        T_base_C=T_base_C,
    )

    # ── One rho_60, evaluated at each meter's temperature. ────────────
    CTL_dispatch = compute_ctl(rho_60, T_dispatch_C, product, T_base_C)
    CTL_receipt = compute_ctl(rho_60, T_receipt_C, product, T_base_C)

    V_dispatch_std = V_dispatch_KL * CTL_dispatch  # [KL @ 15 C]
    V_receipt_std = V_receipt_KL * CTL_receipt  # [KL @ 15 C]

    # ── UFP_net: the real unaccounted product. ────────────────────────
    # Standard volume is a proxy for mass. If nothing leaks, is stolen, or is
    # mis-metered, this is EXACTLY ZERO no matter how much the product cools:
    # temperature correction is precisely what removes the thermal effect.
    # A non-zero value here is real loss (or a metering error) and nothing else.
    UFP_net_KL = V_dispatch_std - V_receipt_std

    # ── UFP_thermal: the artifact BPCL's volumetric accounting actually sees.
    # If the batch had suffered no loss at all, its standard volume would be
    # conserved, so the volume physically arriving at the receipt meter would be
    #     V_receipt_no_loss = V_dispatch_std / CTL_receipt
    # The gap between what was dispatched and that is pure thermal contraction
    # (or expansion). This is the number to subtract from a reported gross UFP.
    V_receipt_no_loss_KL = V_dispatch_std / CTL_receipt
    UFP_thermal_KL = V_dispatch_KL - V_receipt_no_loss_KL

    # ── UFP_gross: the raw volumetric difference across the two meters. ──
    # Decomposition (exact):  gross = thermal + net / CTL_receipt
    UFP_gross_KL = V_dispatch_KL - V_receipt_KL

    UFP_litres = UFP_net_KL * 1000.0
    UFP_rupees = UFP_litres * price_per_litre
    UFP_percent = (UFP_net_KL / V_dispatch_std * 100.0) if V_dispatch_std > 0 else 0.0

    UFP_thermal_percent = (UFP_thermal_KL / V_dispatch_KL * 100.0) if V_dispatch_KL > 0 else 0.0
    UFP_thermal_rupees = UFP_thermal_KL * 1000.0 * price_per_litre
    UFP_gross_rupees = UFP_gross_KL * 1000.0 * price_per_litre

    return {
        "product": product_name,
        "V_dispatch_KL": V_dispatch_KL,
        "V_receipt_KL": V_receipt_KL,
        "T_dispatch_C": T_dispatch_C,
        "T_receipt_C": T_receipt_C,
        "density_kgm3": density_kgm3,
        "rho_60_kgm3": round(rho_60, 3),
        "alpha_60_per_F": compute_alpha_60(rho_60, product),
        "V_dispatch_std_KL": round(V_dispatch_std, 4),
        "V_receipt_std_KL": round(V_receipt_std, 4),
        "V_receipt_no_loss_KL": round(V_receipt_no_loss_KL, 4),
        "CTL_dispatch": round(CTL_dispatch, 6),
        "CTL_receipt": round(CTL_receipt, 6),
        # Real unaccounted product (zero under mass conservation).
        "UFP_net_KL": round(UFP_net_KL, 6),
        "UFP_litres": round(UFP_litres, 1),
        "UFP_rupees": round(UFP_rupees, 2),
        "UFP_percent": round(UFP_percent, 4),
        # Thermal artifact — the quantity the physics model predicts.
        "UFP_thermal_KL": round(UFP_thermal_KL, 4),
        "UFP_thermal_percent": round(UFP_thermal_percent, 4),
        "UFP_thermal_rupees": round(UFP_thermal_rupees, 2),
        # Raw meter-to-meter volumetric difference.
        "UFP_gross_KL": round(UFP_gross_KL, 4),
        "UFP_gross_rupees": round(UFP_gross_rupees, 2),
        # ── Backwards-compatible aliases ──
        # UFP_KL has always meant the standard-volume difference; keep that.
        "UFP_KL": round(UFP_net_KL, 6),
        "Physical_Shrinkage_KL": round(UFP_gross_KL, 4),
        "Physical_Shrinkage_rupees": round(UFP_gross_rupees, 2),
    }


def compute_ufp_from_model(
    product_name: str,
    V_dispatch_KL: float,
    T_dispatch_C: float,
    density_kgm3: float,
    T_receipt_modeled_C: float,
    price_per_litre: Optional[float] = None,
    T_base_C: float = T_BASE_C,
    density_is_at_base: bool = False,
) -> dict:
    """Compute thermal UFP and shrinkage using modeled receipt temperature.

    Assumes standard volume (mass) is perfectly conserved. Computes the
    expected observed physical volume at receipt due to thermal expansion/shrinkage.

    Parameters
    ----------
    product_name : str
        Product type.
    V_dispatch_KL : float
        Dispatched volume [KL].
    T_dispatch_C : float
        Dispatch temperature [°C].
    density_kgm3 : float
        Product density [kg/m³].
    T_receipt_modeled_C : float
        Modeled receipt temperature from ODE solver [°C].
    price_per_litre : float, optional
        Product price [₹/litre].
    T_base_C : float
        Base temperature [°C].

    Returns
    -------
    dict
        Same format as compute_ufp_batch().
    """
    product = PRODUCTS[product_name.lower()]

    # ONE rho_60 for the batch — the same one compute_ufp_batch will resolve.
    # Previously this function passed the observed density straight in as if it
    # were already rho_60, while compute_ufp_batch iterated for it. The two
    # disagreed, and the disagreement surfaced as phantom UFP.
    rho_60 = resolve_rho_60(
        product_name=product_name,
        density_kgm3=density_kgm3,
        density_measured_at_C=T_dispatch_C,
        density_is_at_base=density_is_at_base,
        T_base_C=T_base_C,
    )

    CTL_dispatch = compute_ctl(rho_60, T_dispatch_C, product, T_base_C)
    CTL_receipt = compute_ctl(rho_60, T_receipt_modeled_C, product, T_base_C)

    # Mass conserved => standard volume conserved => the volume physically
    # arriving at the receipt meter is fixed by the receipt temperature alone.
    V_dispatch_std_KL = V_dispatch_KL * CTL_dispatch
    V_receipt_expected_KL = V_dispatch_std_KL / CTL_receipt

    result = compute_ufp_batch(
        product_name=product_name,
        V_dispatch_KL=V_dispatch_KL,
        T_dispatch_C=T_dispatch_C,
        density_kgm3=density_kgm3,
        V_receipt_KL=V_receipt_expected_KL,
        T_receipt_C=T_receipt_modeled_C,
        price_per_litre=price_per_litre,
        T_base_C=T_base_C,
        density_is_at_base=density_is_at_base,
    )

    # This batch is loss-free by construction, so UFP_net must be 0 to within
    # floating-point noise. Assert it: if this ever trips, the VCF path has
    # become internally inconsistent again.
    if abs(result["UFP_net_KL"]) > 1e-6 * max(1.0, V_dispatch_KL):
        raise AssertionError(
            f"compute_ufp_from_model produced UFP_net = {result['UFP_net_KL']} KL "
            f"on a mass-conserving batch; it must be 0. The VCF path is "
            f"internally inconsistent (check resolve_rho_60)."
        )

    return result


def reconcile_ufp(
    reported_ufp_KL: float,
    thermal_ufp_KL: float,
    leakage_threshold_KL: float = 0.5,
) -> dict:
    """Reconcile reported vs thermal UFP.

    Parameters
    ----------
    reported_ufp_KL : float
        UFP reported by depot records [KL].
    thermal_ufp_KL : float
        Physics-predicted thermal UFP [KL].
    leakage_threshold_KL : float
        Threshold for flagging potential leakage [KL].

    Returns
    -------
    dict
        Reconciliation result with:
          - reported_KL, thermal_KL, residual_KL
          - leakage_flag: True if residual > threshold
          - category: "normal", "potential_leakage", "metering_error"
    """
    residual = reported_ufp_KL - thermal_ufp_KL

    if abs(residual) <= leakage_threshold_KL:
        category = "normal"
        leakage_flag = False
    elif residual > leakage_threshold_KL:
        category = "potential_leakage"
        leakage_flag = True
    else:
        category = "metering_error"
        leakage_flag = False

    return {
        "reported_ufp_KL": round(reported_ufp_KL, 4),
        "thermal_ufp_KL": round(thermal_ufp_KL, 4),
        "residual_KL": round(residual, 4),
        "leakage_flag": leakage_flag,
        "category": category,
    }


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    print("UFP Quantification — Sample Calculations")
    print("=" * 55)

    # Scenario: Diesel dispatched at 35°C, arrives at 30°C
    result = compute_ufp_batch(
        product_name="diesel",
        V_dispatch_KL=1000.0,
        T_dispatch_C=35.0,
        density_kgm3=840.0,
        V_receipt_KL=1000.0,
        T_receipt_C=30.0,
    )

    print("\nDiesel: 1000 KL at 35°C → arrives at 30°C")
    print(f"  V_dispatch_std = {result['V_dispatch_std_KL']:.4f} KL")
    print(f"  V_receipt_std  = {result['V_receipt_std_KL']:.4f} KL")
    print(f"  UFP            = {result['UFP_KL']:.4f} KL ({result['UFP_percent']:.4f}%)")
    print(f"  Financial loss = ₹{result['UFP_rupees']:,.2f}")

    # Reconciliation
    print(f"\n{'=' * 55}")
    print("Reconciliation example:")
    recon = reconcile_ufp(
        reported_ufp_KL=2.5,
        thermal_ufp_KL=1.8,
    )
    print(f"  Reported: {recon['reported_ufp_KL']} KL")
    print(f"  Thermal:  {recon['thermal_ufp_KL']} KL")
    print(f"  Residual: {recon['residual_KL']} KL")
    print(f"  Category: {recon['category']}")
    print(f"  Leakage:  {recon['leakage_flag']}")
