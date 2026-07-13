"""
Unit Tests — Physics Engine Validation
========================================

Tests covering all physics modules:
  1. Johansen soil thermal conductivity
  2. API MPMS 11.1 VCF
  3. Heat transfer ODE
  4. UFP quantification
  5. Friction and pumping hydraulics
  6. Centerline generation
  7. SCADA CSV validation
"""

import os
import sys

import pandas as pd
import pytest

# Ensure package imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import PRODUCTS, T_BASE_C
from data.era5_synthetic import generate_synthetic_era5
from geo.centerline import generate_centerline
from ingestion.scada_validator import validate_scada_csv
from model.friction import (
    compute_friction_factor,
    compute_pressure_drop,
    compute_pumping_cost,
    compute_reynolds,
)
from model.heat_transfer import compute_U_value
from model.soil_profile import (
    compute_k_dry,
    compute_k_sat,
    compute_k_soil,
    compute_kersten_number,
)
from model.ufp import compute_ufp_batch
from model.vcf import (
    celsius_to_fahrenheit,
    compute_alpha_60,
    compute_ctl,
    compute_standard_volume,
    iterate_rho_60,
)

# ═══════════════════════════════════════════════════════════════
# TEST 1: Centerline Generation
# ═══════════════════════════════════════════════════════════════


class TestCenterline:
    """Tests for geodesic centerline sampling."""

    def test_centerline_point_count(self):
        """Should generate approximately 360 points at 1 km resolution."""
        pts = generate_centerline(resolution_km=1.0)
        assert 350 <= len(pts) <= 380, f"Expected ~360 points, got {len(pts)}"

    def test_centerline_starts_at_kota(self):
        """First point should be at Kota (km ≈ 0)."""
        pts = generate_centerline()
        assert pts[0].km == 0.0
        assert pts[0].waypoint_name is not None
        assert "Kota" in pts[0].waypoint_name

    def test_centerline_ends_at_bijwasan(self):
        """Last point should be at Bijwasan (km ≈ 360)."""
        pts = generate_centerline()
        assert 355 <= pts[-1].km <= 365
        assert pts[-1].waypoint_name is not None
        assert "Bijwasan" in pts[-1].waypoint_name

    def test_centerline_monotonic_km(self):
        """km values should be strictly increasing."""
        pts = generate_centerline()
        kms = [p.km for p in pts]
        for i in range(1, len(kms)):
            assert kms[i] >= kms[i - 1], f"Non-monotonic at index {i}"

    def test_centerline_has_waypoints(self):
        """Should contain at least Kota and Bijwasan waypoints."""
        pts = generate_centerline()
        wp_names = [p.waypoint_name for p in pts if p.waypoint_name]
        assert len(wp_names) >= 2

    def test_centerline_pipe_diameter(self):
        """Points before km 340 should have 16" diameter."""
        pts = generate_centerline()
        pt_100 = next(p for p in pts if p.km == 100.0)
        assert abs(pt_100.D_outer_m - 16.0 * 0.0254) < 0.001


# ═══════════════════════════════════════════════════════════════
# TEST 2: Johansen Soil Thermal Conductivity
# ═══════════════════════════════════════════════════════════════


class TestJohansenModel:
    """Tests for Johansen (1975) soil thermal conductivity."""

    def test_k_dry_range(self):
        """Dry conductivity should be in [0.1, 0.5] W/(m·K)."""
        k = compute_k_dry(porosity=0.40)
        assert 0.1 < k < 0.5, f"k_dry = {k} outside expected range"

    def test_k_sat_range(self):
        """Saturated conductivity should be in [1.0, 4.0] W/(m·K)."""
        k = compute_k_sat(porosity=0.40, quartz_fraction=0.50)
        assert 1.0 < k < 4.0, f"k_sat = {k} outside expected range"

    def test_kersten_zero_saturation(self):
        """Ke should be 0 for zero saturation."""
        ke = compute_kersten_number(0.0, "coarse")
        assert ke == 0.0

    def test_kersten_full_saturation(self):
        """Ke should be 1.0 for full saturation."""
        ke = compute_kersten_number(1.0, "coarse")
        assert abs(ke - 1.0) < 0.01

    def test_k_soil_between_dry_and_sat(self):
        """k_soil should be between k_dry and k_sat."""
        k_dry = compute_k_dry(0.40)
        k_sat = compute_k_sat(0.40, 0.50)
        k = compute_k_soil(moisture_m3m3=0.20, porosity=0.40, quartz_fraction=0.50)
        assert k_dry <= k <= k_sat, f"k_soil = {k} not in [{k_dry}, {k_sat}]"

    def test_k_soil_increases_with_moisture(self):
        """Higher moisture should give higher k_soil."""
        k_low = compute_k_soil(0.10)
        k_high = compute_k_soil(0.30)
        assert k_high > k_low

    def test_k_soil_typical_range(self):
        """k_soil should be in [0.2, 2.5] W/(m·K) for typical conditions."""
        k = compute_k_soil(0.20)
        assert 0.2 < k < 2.5


# ═══════════════════════════════════════════════════════════════
# TEST 3: API MPMS 11.1 VCF
# ═══════════════════════════════════════════════════════════════


class TestVCF:
    """Tests for API MPMS Chapter 11.1 VCF engine."""

    def test_ctl_at_base_temperature(self):
        """CTL should be 1.0 at base temperature (15°C)."""
        for product in PRODUCTS.values():
            ctl = compute_ctl(product.density_ref_kgm3, T_BASE_C, product)
            assert abs(ctl - 1.0) < 1e-10, f"{product.name}: CTL at base T = {ctl}, expected 1.0"

    def test_ctl_above_base_less_than_one(self):
        """CTL < 1 when T > T_base (product expands)."""
        for product in PRODUCTS.values():
            ctl = compute_ctl(product.density_ref_kgm3, 40.0, product)
            assert ctl < 1.0, f"{product.name}: CTL at 40°C = {ctl}, expected < 1.0"

    def test_ctl_below_base_greater_than_one(self):
        """CTL > 1 when T < T_base (product contracts)."""
        for product in PRODUCTS.values():
            ctl = compute_ctl(product.density_ref_kgm3, 5.0, product)
            assert ctl > 1.0, f"{product.name}: CTL at 5°C = {ctl}, expected > 1.0"

    def test_alpha_positive(self):
        """Thermal expansion coefficient should be positive."""
        for product in PRODUCTS.values():
            alpha = compute_alpha_60(product.density_ref_kgm3, product)
            assert alpha > 0, f"{product.name}: α₆₀ = {alpha}, expected > 0"

    def test_alpha_reasonable_range(self):
        """α₆₀ should be in range [0.0004, 0.002] per °F for petroleum."""
        for product in PRODUCTS.values():
            alpha = compute_alpha_60(product.density_ref_kgm3, product)
            assert 0.0004 < alpha < 0.002, f"{product.name}: α₆₀ = {alpha} outside expected range"

    def test_iterate_rho60_converges(self):
        """Iterative ρ₆₀ should converge from observed density."""
        product = PRODUCTS["diesel"]
        rho_60, ctl = iterate_rho_60(
            rho_observed_kgm3=835.0,
            T_observed_C=35.0,
            product=product,
        )
        assert abs(rho_60 - 835.0 / ctl) < 0.1

    def test_standard_volume_calculation(self):
        """Standard volume should be computable for all products."""
        for prod_name, product in PRODUCTS.items():
            result = compute_standard_volume(
                V_observed_KL=100.0,
                rho_kgm3=product.density_ref_kgm3,
                T_observed_C=35.0,
                product_name=prod_name,
            )
            assert result["V_std_KL"] > 0
            assert result["CTL"] > 0
            assert result["CTL"] < 1.0  # T > T_base

    def test_celsius_to_fahrenheit(self):
        """Temperature conversion should be correct."""
        assert celsius_to_fahrenheit(0.0) == 32.0
        assert celsius_to_fahrenheit(100.0) == 212.0
        assert abs(celsius_to_fahrenheit(15.0) - 59.0) < 0.01


# ═══════════════════════════════════════════════════════════════
# TEST 4: Heat Transfer
# ═══════════════════════════════════════════════════════════════


class TestHeatTransfer:
    """Tests for heat transfer calculations."""

    def test_u_value_positive(self):
        """U-value should be positive."""
        U = compute_U_value(
            D_outer_m=0.4064,  # 16"
            D_inner_m=0.381,
            k_soil_WmK=1.0,
        )
        assert U > 0

    def test_u_value_increases_with_k_soil(self):
        """Higher k_soil should give higher U (more heat transfer)."""
        U_low = compute_U_value(0.4064, 0.381, k_soil_WmK=0.5)
        U_high = compute_U_value(0.4064, 0.381, k_soil_WmK=2.0)
        assert U_high > U_low

    def test_u_value_reasonable_range(self):
        """U should be in [0.5, 20] W/(m²·K) for buried steel pipe."""
        U = compute_U_value(0.4064, 0.381, k_soil_WmK=1.0)
        assert 0.5 < U < 20, f"U = {U} outside expected range"


# ═══════════════════════════════════════════════════════════════
# TEST 5: UFP Quantification
# ═══════════════════════════════════════════════════════════════


class TestUFP:
    """Tests for UFP calculation."""

    def test_ufp_zero_when_same_temperature(self):
        """UFP should be zero when dispatch and receipt T are equal."""
        result = compute_ufp_batch(
            product_name="diesel",
            V_dispatch_KL=1000.0,
            T_dispatch_C=30.0,
            density_kgm3=840.0,
            V_receipt_KL=1000.0,
            T_receipt_C=30.0,
        )
        assert abs(result["UFP_KL"]) < 0.01

    def test_ufp_positive_when_receipt_warmer(self):
        """UFP > 0 when receipt is warmer (summer scenario)."""
        result = compute_ufp_batch(
            product_name="diesel",
            V_dispatch_KL=1000.0,
            T_dispatch_C=30.0,
            density_kgm3=840.0,
            V_receipt_KL=1000.0,
            T_receipt_C=35.0,  # warmer at receipt
        )
        # When receipt is warmer, receipt CTL < dispatch CTL
        # V_receipt_std < V_dispatch_std → UFP > 0
        # Actually, V_receipt_std = V_receipt × CTL_receipt (smaller CTL)
        # V_dispatch_std = V_dispatch × CTL_dispatch (larger CTL at 30°C)
        # Both CTLs < 1 (both above 15°C), but CTL_receipt < CTL_dispatch
        # So V_receipt_std < V_dispatch_std → UFP > 0
        assert result["UFP_KL"] > 0

    # Reconciliation / leak-classification tests removed: the project objective
    # states the simulator is "not intended to be a leak detection system or an
    # accounting reconciliation engine". model.ufp.reconcile_ufp() is orphaned.


# ═══════════════════════════════════════════════════════════════
# TEST 6: Friction & Pumping
# ═══════════════════════════════════════════════════════════════


class TestFriction:
    """Tests for Darcy-Weisbach friction calculations."""

    def test_reynolds_number(self):
        """Reynolds number should be > 4000 for typical pipeline flow."""
        Re = compute_reynolds(1.0, 0.381, 840.0, 0.003)
        assert Re > 4000  # turbulent flow

    def test_friction_factor_laminar(self):
        """Laminar friction factor = 64/Re."""
        f = compute_friction_factor(Re=1000, D_inner_m=0.381)
        assert abs(f - 64.0 / 1000.0) < 0.001

    def test_friction_factor_turbulent_range(self):
        """Turbulent friction factor should be in [0.005, 0.05]."""
        Re = compute_reynolds(1.5, 0.381, 840.0, 0.003)
        f = compute_friction_factor(Re, 0.381)
        assert 0.005 < f < 0.05

    def test_pressure_drop_positive(self):
        """Pressure drop should be positive."""
        dP = compute_pressure_drop(
            velocity_ms=1.5,
            density_kgm3=840.0,
            viscosity_pas=0.003,
            length_m=360000.0,
            D_inner_m=0.381,
        )
        assert dP > 0

    def test_pumping_cost_increases_with_velocity(self):
        """Higher velocity should mean higher pumping cost."""
        c1 = compute_pumping_cost(1.0, "diesel", 840.0)
        c2 = compute_pumping_cost(2.0, "diesel", 840.0)
        assert c2["pumping_cost_inr"] > c1["pumping_cost_inr"]


# ═══════════════════════════════════════════════════════════════
# TEST 7: SCADA CSV Validation
# ═══════════════════════════════════════════════════════════════


class TestSCADAValidator:
    """Tests for SCADA CSV validation."""

    def test_valid_csv(self):
        """Valid CSV should pass validation."""
        df = pd.DataFrame(
            [
                {
                    "batch_id": "B001",
                    "product": "diesel",
                    "dispatch_datetime": "2024-06-15T10:00:00",
                    "V_dispatch_KL": 1000.0,
                    "T_dispatch_C": 35.0,
                    "density_kgm3": 840.0,
                    "flow_rate_m3hr": 150.0,
                    "V_receipt_KL": 1000.0,
                    "T_receipt_C": 30.0,
                }
            ]
        )
        df_clean, result = validate_scada_csv(df)
        assert result.is_valid

    def test_missing_column(self):
        """Missing mandatory column should fail validation."""
        df = pd.DataFrame(
            [
                {
                    "batch_id": "B001",
                    "product": "diesel",
                    # Missing dispatch_datetime and others
                }
            ]
        )
        df_clean, result = validate_scada_csv(df)
        assert not result.is_valid

    def test_invalid_product(self):
        """Invalid product name should fail validation."""
        df = pd.DataFrame(
            [
                {
                    "batch_id": "B001",
                    "product": "kerosene",  # not valid
                    "dispatch_datetime": "2024-06-15T10:00:00",
                    "V_dispatch_KL": 1000.0,
                    "T_dispatch_C": 35.0,
                    "density_kgm3": 840.0,
                    "flow_rate_m3hr": 150.0,
                    "V_receipt_KL": 1000.0,
                    "T_receipt_C": 30.0,
                }
            ]
        )
        df_clean, result = validate_scada_csv(df)
        assert not result.is_valid

    def test_auto_fill_density(self):
        """Missing density should be auto-filled."""
        df = pd.DataFrame(
            [
                {
                    "batch_id": "B001",
                    "product": "diesel",
                    "dispatch_datetime": "2024-06-15T10:00:00",
                    "V_dispatch_KL": 1000.0,
                    "T_dispatch_C": 35.0,
                    "density_kgm3": None,
                    "flow_rate_m3hr": 150.0,
                    "V_receipt_KL": 1000.0,
                    "T_receipt_C": 30.0,
                }
            ]
        )
        df_clean, result = validate_scada_csv(df, auto_fill=True)
        assert result.is_valid
        assert df_clean.iloc[0]["density_kgm3"] == 840.0  # diesel reference


# ═══════════════════════════════════════════════════════════════
# TEST 8: Synthetic ERA5
# ═══════════════════════════════════════════════════════════════


class TestSyntheticERA5:
    """Tests for synthetic ERA5 data generation."""

    def test_synthetic_data_structure(self):
        """Synthetic dataset should have correct variables."""
        ds = generate_synthetic_era5()
        assert "stl2" in ds
        assert "stl3" in ds
        assert "swvl3" in ds

    def test_synthetic_temperature_range(self):
        """Soil temperature should be in reasonable range."""
        ds = generate_synthetic_era5()
        T_min = float(ds.stl3.min()) - 273.15  # to °C
        T_max = float(ds.stl3.max()) - 273.15
        assert T_min > -5.0, f"T_min = {T_min}°C too cold"
        assert T_max < 55.0, f"T_max = {T_max}°C too hot"

    def test_synthetic_moisture_range(self):
        """Soil moisture should be in [0, 0.5] m³/m³."""
        ds = generate_synthetic_era5()
        assert float(ds.swvl3.min()) >= 0.0
        assert float(ds.swvl3.max()) <= 0.55  # with noise margin


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
