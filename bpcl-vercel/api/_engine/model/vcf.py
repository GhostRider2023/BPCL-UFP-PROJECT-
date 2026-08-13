"""
Volume Correction Factor Engine — API MPMS Chapter 11.1
========================================================

Implements the full API MPMS Chapter 11.1 (ASTM D1250 / IP 200)
Temperature Correction Factor (CTL) calculation for petroleum
products.

Key formula:
  CTL = exp[−α₆₀ × ΔT × (1 + 0.8 × α₆₀ × ΔT)]

Where:
  α₆₀ = K₀/ρ₆₀² + K₁/ρ₆₀ + K₂    [per °F]

Commodity group coefficients (K₀, K₁, K₂):
  - Gasoline (Petrol):   192.4571, 0.2438, 0
  - Fuel Oils (Diesel):  103.8720, 0.2701, 0
  - Jet Fuels (ATF):     330.3010, 0, 0

Standards referenced:
  - API MPMS Chapter 11.1 (2004 / 2019 addendum)
  - ASTM D1250-19
  - IP 200
  - IS:2796, IS:1460, IS:1571 (Indian petroleum product standards)

Units:
  - Density:     kg/m³
  - Temperature: °C (input) → °F (internal conversion for API formula)
  - VCF (CTL):   dimensionless
  - Volume:      kilolitres [KL]
"""

import math
import os
import sys
from typing import Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import PRODUCTS, T_BASE_C, ProductProperties


def celsius_to_fahrenheit(T_C: float) -> float:
    """Convert temperature from Celsius to Fahrenheit.

    Parameters
    ----------
    T_C : float
        Temperature [°C].

    Returns
    -------
    float
        Temperature [°F].
    """
    return T_C * 9.0 / 5.0 + 32.0


def compute_alpha_60(
    rho_60_kgm3: float,
    product: ProductProperties,
) -> float:
    """Compute thermal expansion coefficient α₆₀ [per °F].

    Parameters
    ----------
    rho_60_kgm3 : float
        Density at base temperature (15°C / 59°F) [kg/m³].
    product : ProductProperties
        Product with API MPMS K coefficients.

    Returns
    -------
    float
        Thermal expansion coefficient α₆₀ [per °F].

    Notes
    -----
    Formula: α₆₀ = K₀/ρ₆₀² + K₁/ρ₆₀ + K₂
    Source: API MPMS Chapter 11.1, Table 1.
    """
    K0 = product.api_K0
    K1 = product.api_K1
    K2 = product.api_K2

    alpha = K0 / (rho_60_kgm3**2) + K1 / rho_60_kgm3 + K2
    return alpha


def compute_ctl(
    rho_60_kgm3: float,
    T_observed_C: float,
    product: ProductProperties,
    T_base_C: float = T_BASE_C,
) -> float:
    """Compute Temperature Correction Factor (CTL / VCF).

    Parameters
    ----------
    rho_60_kgm3 : float
        Density at base temperature [kg/m³].
    T_observed_C : float
        Observed product temperature [°C].
    product : ProductProperties
        Product with API MPMS K coefficients.
    T_base_C : float
        Base (reference) temperature [°C]. Default: 15°C.

    Returns
    -------
    float
        CTL (Volume Correction Factor), dimensionless.
        VCF > 1 means product is colder than base → contracts at base.
        VCF < 1 means product is warmer than base → expands at base.

    Notes
    -----
    CTL = exp[−α₆₀ × ΔT_F × (1 + 0.8 × α₆₀ × ΔT_F)]

    where ΔT_F = T_observed_F − T_base_F

    Source: API MPMS Chapter 11.1, §6.
    """
    alpha = compute_alpha_60(rho_60_kgm3, product)

    # Temperature difference in °F
    T_obs_F = celsius_to_fahrenheit(T_observed_C)
    T_base_F_val = celsius_to_fahrenheit(T_base_C)
    delta_T_F = T_obs_F - T_base_F_val

    # CTL formula
    exponent = -alpha * delta_T_F * (1.0 + 0.8 * alpha * delta_T_F)
    ctl = math.exp(exponent)

    return ctl


def iterate_rho_60(
    rho_observed_kgm3: float,
    T_observed_C: float,
    product: ProductProperties,
    T_base_C: float = T_BASE_C,
    max_iterations: int = 50,
    tolerance_kgm3: float = 0.01,
) -> Tuple[float, float]:
    """Iteratively determine base density ρ₆₀ from observed density.

    When density is measured at observed temperature (not at 15°C),
    an iterative procedure is needed because α₆₀ depends on ρ₆₀.

    Parameters
    ----------
    rho_observed_kgm3 : float
        Density measured at observed temperature [kg/m³].
    T_observed_C : float
        Temperature at which density was measured [°C].
    product : ProductProperties
        Product properties.
    T_base_C : float
        Base temperature [°C]. Default: 15°C.
    max_iterations : int
        Maximum iterations for convergence.
    tolerance_kgm3 : float
        Convergence tolerance for ρ₆₀ [kg/m³].

    Returns
    -------
    tuple of (float, float)
        (rho_60_kgm3, CTL) — base density and corresponding CTL.

    Notes
    -----
    Iterative procedure:
      1. Initial guess: ρ₆₀ = ρ_observed
      2. Compute CTL(ρ₆₀, T_observed)
      3. Update: ρ₆₀(new) = ρ_observed / CTL
      4. Repeat until |Δρ₆₀| < tolerance

    Source: API MPMS Chapter 11.1, §7 (Implementation Procedure).
    """
    rho_60 = rho_observed_kgm3  # initial guess

    for _ in range(max_iterations):
        ctl = compute_ctl(rho_60, T_observed_C, product, T_base_C)
        rho_60_new = rho_observed_kgm3 / ctl

        if abs(rho_60_new - rho_60) < tolerance_kgm3:
            return rho_60_new, ctl

        rho_60 = rho_60_new

    # If not converged, return best estimate
    ctl = compute_ctl(rho_60, T_observed_C, product, T_base_C)
    return rho_60, ctl


def compute_standard_volume(
    V_observed_KL: float,
    rho_kgm3: float,
    T_observed_C: float,
    product_name: str,
    density_is_at_base: bool = False,
    T_base_C: float = T_BASE_C,
) -> dict:
    """Compute standard volume at base temperature.

    Parameters
    ----------
    V_observed_KL : float
        Observed volume [KL] at metering temperature.
    rho_kgm3 : float
        Density [kg/m³] (at observed T or at base T).
    T_observed_C : float
        Observed temperature [°C].
    product_name : str
        Product identifier ("petrol", "diesel", "atf").
    density_is_at_base : bool
        If True, rho_kgm3 is already at base temperature.
        If False, iterate to find ρ₆₀.
    T_base_C : float
        Base temperature [°C]. Default: 15°C.

    Returns
    -------
    dict
        Keys: V_std_KL, CTL, rho_60_kgm3, alpha_60, delta_T_F
    """
    product = PRODUCTS[product_name.lower()]

    if density_is_at_base:
        rho_60 = rho_kgm3
        ctl = compute_ctl(rho_60, T_observed_C, product, T_base_C)
    else:
        rho_60, ctl = iterate_rho_60(rho_kgm3, T_observed_C, product, T_base_C)

    alpha = compute_alpha_60(rho_60, product)
    delta_T_F = celsius_to_fahrenheit(T_observed_C) - celsius_to_fahrenheit(T_base_C)
    V_std = V_observed_KL * ctl

    return {
        "V_std_KL": round(V_std, 6),
        "CTL": round(ctl, 6),
        "rho_60_kgm3": round(rho_60, 2),
        "alpha_60_per_F": alpha,
        "delta_T_F": round(delta_T_F, 2),
    }


# ─── Validation / quick test ─────────────────────────────────
if __name__ == "__main__":
    print("API MPMS Chapter 11.1 — VCF Validation")
    print("=" * 55)

    for pname, product in PRODUCTS.items():
        print(f"\n{product.name} ({product.is_standard})")
        print(f"  Reference density: {product.density_ref_kgm3} kg/m³")

        rho_ref = product.density_ref_kgm3
        alpha = compute_alpha_60(rho_ref, product)
        print(f"  α₆₀ = {alpha:.8f} per °F")

        # Test at various temperatures
        for T_C in [15.0, 25.0, 35.0, 45.0]:
            ctl = compute_ctl(rho_ref, T_C, product)
            print(f"  T={T_C:5.1f}°C → CTL = {ctl:.6f}")

    # End-to-end: compute standard volume
    print("\n" + "=" * 55)
    print("Sample standard volume calculation:")
    result = compute_standard_volume(
        V_observed_KL=100.0,
        rho_kgm3=750.0,
        T_observed_C=35.0,
        product_name="petrol",
    )
    print("  V_obs = 100.0 KL at 35°C, ρ = 750 kg/m³ (petrol)")
    print(f"  CTL = {result['CTL']}")
    print(f"  V_std = {result['V_std_KL']:.4f} KL at 15°C")
    print(f"  ρ₆₀  = {result['rho_60_kgm3']} kg/m³")
