"""
Central Configuration — Kota–Bijwasan Pipeline UFP System
==========================================================

All physical constants, pipeline geometry, product properties,
and route waypoints in one place. Every value cites its source.

Units convention:
  - Lengths:       metres [m] or kilometres [km]
  - Temperatures:  degrees Celsius [°C] unless noted (°F for API MPMS)
  - Densities:     kg/m³
  - Thermal cond.: W/(m·K)
  - Pressures:     Pa
  - Flow rates:    m³/hr or m/s
  - Volumes:       kilolitres [KL]
"""

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════
# PIPELINE ROUTE — FIXED
# Source: BPCL MMBPL route documentation, PNGRB filings
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Waypoint:
    """A named waypoint along the pipeline."""

    name: str
    lat: float  # degrees North
    lon: float  # degrees East
    km: float  # cumulative distance from Kota [km]


WAYPOINTS: List[Waypoint] = [
    Waypoint("Kota (Dispatch)", 25.18, 75.83, 0.0),
    Waypoint("Bundi", 25.43, 75.65, 40.0),
    Waypoint("Sawai Madhopur", 26.00, 76.35, 110.0),
    Waypoint("Bharatpur", 27.22, 77.49, 210.0),
    Waypoint("Mathura (crossing)", 27.49, 77.67, 250.0),
    Waypoint("Faridabad", 28.41, 77.31, 330.0),
    Waypoint("Piyala", 28.48, 77.28, 340.0),
    Waypoint("Bijwasan (Receipt)", 28.52, 77.08, 360.0),
]

PIPELINE_LENGTH_KM: float = 360.0  # total route length [km]

# Spatial resolution for continuous centerline sampling
CENTERLINE_RESOLUTION_KM: float = 1.0  # sample every 1 km → ~360 points


# ═══════════════════════════════════════════════════════════════════
# PIPELINE GEOMETRY — per segment
# Source: PNGRB, BPCL MMBPL specifications, Wikipedia MMBPL
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PipeSegment:
    """Pipeline geometry for a route segment."""

    name: str
    km_start: float  # start km from Kota
    km_end: float  # end km from Kota
    outer_diameter_inch: float  # nominal OD [inches]
    wall_thickness_mm: float  # wall thickness [mm]

    @property
    def outer_diameter_m(self) -> float:
        """Outer diameter [m]."""
        return self.outer_diameter_inch * 0.0254

    @property
    def inner_diameter_m(self) -> float:
        """Inner diameter [m]."""
        return self.outer_diameter_m - 2.0 * self.wall_thickness_mm / 1000.0

    @property
    def outer_radius_m(self) -> float:
        """Outer radius [m]."""
        return self.outer_diameter_m / 2.0

    @property
    def inner_radius_m(self) -> float:
        """Inner radius [m]."""
        return self.inner_diameter_m / 2.0

    @property
    def cross_section_area_m2(self) -> float:
        """Internal cross-section area [m²]."""
        return math.pi * self.inner_radius_m**2


# MMBPL pipeline segments (Kota to Bijwasan)
# Source: PNGRB tariff orders, BPCL annual reports
# Assumption: Wall thickness ~ Sch 40 for API 5L Grade B
PIPE_SEGMENTS: List[PipeSegment] = [
    PipeSegment("Kota–Piyala", 0.0, 340.0, 16.0, 12.7),  # 16", Sch 40 → t = 12.7 mm
    PipeSegment("Piyala–Bijwasan", 340.0, 360.0, 8.0, 8.18),  # 8", Sch 40 → t = 8.18 mm
]

# Default (single-diameter fallback for simplified calculations)
DEFAULT_OD_INCH: float = 16.0
DEFAULT_WALL_THICKNESS_MM: float = 12.7


def get_pipe_segment(km: float) -> PipeSegment:
    """Return the pipe segment for a given chainage [km]."""
    for seg in PIPE_SEGMENTS:
        if seg.km_start <= km < seg.km_end:
            return seg
    # If exactly at the end, return last segment
    return PIPE_SEGMENTS[-1]


# ═══════════════════════════════════════════════════════════════════
# BURIAL & MATERIAL PROPERTIES
# ═══════════════════════════════════════════════════════════════════

# Burial depth [m]
# Source: OISD-141 (Oil Industry Safety Directorate), §4.3
BURIAL_DEPTH_M: float = 1.2


# ═══════════════════════════════════════════════════════════════════
# SOIL LAYER SELECTION — which ERA5-Land layer represents the pipe
# ═══════════════════════════════════════════════════════════════════
#
# ERA5-Land soil layers and their depth spans / centroids:
#
#   stl1 / swvl1 :   0 –   7 cm   (centroid   3.5 cm)
#   stl2 / swvl2 :   7 –  28 cm   (centroid  17.5 cm)
#   stl3 / swvl3 :  28 – 100 cm   (centroid  64.0 cm)
#   stl4 / swvl4 : 100 – 289 cm   (centroid 194.5 cm)   <-- pipe lives here
#
# The pipe is buried at BURIAL_DEPTH_M = 1.2 m = 120 cm, which lies inside
# LAYER 4. The model previously read stl3 (28–100 cm) — the wrong layer, and
# a shallower one. Measured at Bijwasan, that carried a systematic seasonal
# error that FLIPS SIGN with the season:
#
#     month      stl3     stl4    error of using stl3
#     January   17.87    23.42          -2.38 C
#     April     27.72    22.67          +2.17 C
#     June      32.39    28.09          +1.85 C
#     December  20.35    24.62          -1.83 C
#
# A 4.5 C peak-to-peak bias, perfectly correlated with season. Shallow soil
# swings harder and leads deep soil in phase, so stl3 both OVERSTATES the
# seasonal amplitude and gets its timing wrong. Propagated through
# alpha_60 (~0.00046/F) that is a ~0.2% volume error — larger than the
# thermal signal being modelled.
#
# Moisture is read from the SAME layer, so that k_soil (Johansen) describes
# the soil actually surrounding the pipe rather than the soil a metre above it.
SOIL_TEMPERATURE_VAR: str = "stl4"  # 100–289 cm
SOIL_MOISTURE_VAR: str = "swvl4"  # 100–289 cm

# All layers pulled from the GRIB and cached. stl3/swvl3 are retained so that
# depth interpolation to an arbitrary burial depth remains possible without a
# re-download (see ERA5_LAYER_DEPTHS_CM).
ERA5_SOIL_VARS: List[str] = ["stl3", "stl4", "swvl3", "swvl4"]

# Layer centroid depths [cm], for future depth interpolation.
ERA5_LAYER_DEPTHS_CM: Dict[str, float] = {
    "stl1": 3.5,
    "stl2": 17.5,
    "stl3": 64.0,
    "stl4": 194.5,
    "swvl1": 3.5,
    "swvl2": 17.5,
    "swvl3": 64.0,
    "swvl4": 194.5,
}

# Thermal conductivity of carbon steel pipe [W/(m·K)]
# Source: API 5L Grade B carbon steel, typical value 50 W/(m·K)
# Reference: Perry's Chemical Engineers' Handbook, 9th Ed., Table 2-324
K_STEEL_WMK: float = 50.0

# Pipe internal roughness [m]
# Source: Moody chart, new commercial steel ε ≈ 0.045 mm
# Reference: Colebrook (1939), Crane TP-410
PIPE_ROUGHNESS_M: float = 0.000045


# ═══════════════════════════════════════════════════════════════════
# PETROLEUM PRODUCT PROPERTIES
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProductProperties:
    """Physical properties for a petroleum product.

    All values at reference/typical operating conditions.
    """

    name: str
    is_standard: str  # Indian Standard reference
    density_min_kgm3: float  # density range lower bound [kg/m³]
    density_max_kgm3: float  # density range upper bound [kg/m³]
    density_ref_kgm3: float  # reference density at 15°C [kg/m³]
    cp_jkgk: float  # specific heat capacity [J/(kg·K)]
    viscosity_20c_mPas: float  # dynamic viscosity at 20°C [mPa·s]
    viscosity_40c_mPas: float  # dynamic viscosity at 40°C [mPa·s]
    price_per_litre: float  # default price [₹/litre]

    # API MPMS Chapter 11.1 commodity-group coefficients
    # Source: API MPMS 11.1, Table 1 / ASTM D1250
    api_K0: float
    api_K1: float
    api_K2: float

    def viscosity_at_T(self, T_C: float) -> float:
        """Estimate dynamic viscosity [Pa·s] at temperature T [°C].

        Uses Andrade equation: ln(μ) = A + B/T
        Fitted from viscosity at 20°C and 40°C.

        Parameters
        ----------
        T_C : float
            Temperature [°C].

        Returns
        -------
        float
            Dynamic viscosity [Pa·s].
        """
        T1 = 20.0 + 273.15  # [K]
        T2 = 40.0 + 273.15  # [K]
        mu1 = self.viscosity_20c_mPas * 1e-3  # [Pa·s]
        mu2 = self.viscosity_40c_mPas * 1e-3  # [Pa·s]

        # Andrade: ln(μ) = A + B/T
        B = math.log(mu1 / mu2) / (1.0 / T1 - 1.0 / T2)
        A = math.log(mu1) - B / T1

        T_K = T_C + 273.15
        return math.exp(A + B / T_K)


# Product definitions
# Source: IS:2796 (Petrol), IS:1460 (Diesel), IS:1571 (ATF)
# Cp values: Engineering Toolbox, Perry's Handbook
# Viscosity: typical values for Indian-spec products
# Prices: approximate Indian retail (July 2024)
PRODUCTS: Dict[str, ProductProperties] = {
    "petrol": ProductProperties(
        name="Petrol (Motor Spirit)",
        is_standard="IS:2796",
        density_min_kgm3=720.0,
        density_max_kgm3=780.0,
        density_ref_kgm3=750.0,
        cp_jkgk=2100.0,  # ~2.1 kJ/(kg·K) at ~25°C
        viscosity_20c_mPas=0.60,  # typical gasoline viscosity
        viscosity_40c_mPas=0.40,
        price_per_litre=105.0,  # ₹/litre
        api_K0=192.4571,  # Gasoline commodity group
        api_K1=0.2438,
        api_K2=0.0,
    ),
    "diesel": ProductProperties(
        name="Diesel (HSD)",
        is_standard="IS:1460",
        density_min_kgm3=820.0,
        density_max_kgm3=860.0,
        density_ref_kgm3=840.0,
        cp_jkgk=2050.0,  # ~2.05 kJ/(kg·K)
        viscosity_20c_mPas=4.50,  # typical diesel viscosity
        viscosity_40c_mPas=2.50,
        price_per_litre=90.0,  # ₹/litre
        api_K0=103.8720,  # Fuel Oils commodity group
        api_K1=0.2701,
        api_K2=0.0,
    ),
    "atf": ProductProperties(
        name="Aviation Turbine Fuel",
        is_standard="IS:1571",
        density_min_kgm3=775.0,
        density_max_kgm3=840.0,
        density_ref_kgm3=800.0,
        cp_jkgk=2010.0,  # ~2.01 kJ/(kg·K)
        viscosity_20c_mPas=2.00,  # typical jet fuel viscosity
        viscosity_40c_mPas=1.20,
        price_per_litre=85.0,  # ₹/litre
        api_K0=330.3010,  # Jet Fuels commodity group
        api_K1=0.0,
        api_K2=0.0,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# SOIL & THERMAL CONSTANTS — Johansen (1975) Model
# ═══════════════════════════════════════════════════════════════════

# Soil porosity (dimensionless, typical alluvial/sandy soil)
# Source: Johansen (1975), typical range 0.30–0.50
SOIL_POROSITY: float = 0.40

# Quartz fraction of solid particles (dimensionless)
# Source: Estimated for mixed terrain (sandstone/alluvial)
SOIL_QUARTZ_FRACTION: float = 0.50

# Thermal conductivity of quartz [W/(m·K)]
# Source: Johansen (1975), Table 2
K_QUARTZ_WMK: float = 7.7

# Thermal conductivity of other minerals [W/(m·K)]
# Source: Johansen (1975), Table 2
K_OTHER_MINERALS_WMK: float = 2.0

# Thermal conductivity of water [W/(m·K)]
# Source: CRC Handbook of Chemistry and Physics, at 20°C
K_WATER_WMK: float = 0.594

# Particle density of soil solids [kg/m³]
# Source: Typical quartz/feldspar mixture
SOIL_PARTICLE_DENSITY_KGM3: float = 2650.0

# Soil type classification for Kersten number
# "coarse" or "fine" — affects Ke formula
# Source: Dominant terrain along route is sandy/alluvial → coarse
SOIL_TYPE: str = "coarse"


# ═══════════════════════════════════════════════════════════════════
# API MPMS 11.1 — Temperature Base
# ═══════════════════════════════════════════════════════════════════

# Indian standard base temperature [°C]
# Source: IS:2796, IS:1460, IS:1571 — all reference 15°C
T_BASE_C: float = 15.0

# Converted to °F for API MPMS formula (which uses ΔT in °F)
T_BASE_F: float = T_BASE_C * 9.0 / 5.0 + 32.0  # = 59.0°F


# ═══════════════════════════════════════════════════════════════════
# ERA5 CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Bounding box for ERA5 data download [North, West, South, East]
# Source: Route geometry with buffer
ERA5_BBOX: Tuple[float, float, float, float] = (29.0, 75.5, 25.0, 78.0)

# ERA5 variables to download
ERA5_VARIABLES: List[str] = [
    "soil_temperature_level_2",  # 7–28 cm depth
    "soil_temperature_level_3",  # 28–100 cm depth (primary)
    "volumetric_soil_water_layer_3",  # 28–100 cm depth
]

# Time configuration
ERA5_YEARS: List[int] = list(range(2020, 2025))  # 2020–2024
ERA5_MONTHS: List[int] = list(range(1, 13))  # all months
ERA5_DAYS: List[int] = [1, 8, 15, 22]  # 4 days per month
ERA5_TIMES: List[str] = ["00:00", "06:00", "12:00", "18:00"]

# Output filename
ERA5_OUTPUT_FILE: str = "kota_bijwasan_soil_data.nc"

# ── Real 2025 GRIB Data ──────────────────────────────────────
# DATASET_SOIL folder path (contains monthly ZIP archives with GRIB data)
DATASET_SOIL_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "DATASET_SOIL"
)

# Cached NetCDF file (auto-generated from GRIB on first load)
REAL_ERA5_CACHE_FILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "real_soil_data_2025.nc"
)


# ═══════════════════════════════════════════════════════════════════
# FLOW RATE OPTIMIZER DEFAULTS
# ═══════════════════════════════════════════════════════════════════

# Velocity bounds for optimization [m/s]
VELOCITY_MIN_MS: float = 0.5
VELOCITY_MAX_MS: float = 2.5

# Pump efficiency (dimensionless)
# Source: Typical centrifugal pump efficiency for petroleum service
PUMP_EFFICIENCY: float = 0.75

# Electricity rate [₹/kWh]
# Source: Approximate industrial tariff, Rajasthan/Haryana 2024
ELECTRICITY_RATE_INR_PER_KWH: float = 8.0


# ═══════════════════════════════════════════════════════════════════
# SCADA CSV COLUMN DEFINITIONS
# ═══════════════════════════════════════════════════════════════════

SCADA_MANDATORY_COLUMNS: List[str] = [
    "batch_id",
    "product",
    "dispatch_datetime",
    "v_dispatch_kl",
    "t_dispatch_c",
    "density_kgm3",
    "flow_rate_m3hr",
    "v_receipt_kl",
    "t_receipt_c",
]

# Valid product names (case-insensitive match)
VALID_PRODUCTS: List[str] = ["petrol", "diesel", "atf"]

# Validation bounds
T_MIN_C: float = -5.0
T_MAX_C: float = 65.0
DENSITY_MIN_KGM3: float = 680.0
DENSITY_MAX_KGM3: float = 900.0


# ═══════════════════════════════════════════════════════════════════
# TERRAIN ZONES (for synthetic data and visualization)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TerrainZone:
    """Terrain characteristics for a route segment."""

    name: str
    km_start: float
    km_end: float
    soil_type: str  # "coarse" or "fine"
    porosity: float  # local porosity estimate
    quartz_frac: float  # local quartz fraction


TERRAIN_ZONES: List[TerrainZone] = [
    TerrainZone("Chambal Ravines", 0.0, 110.0, "coarse", 0.35, 0.60),
    TerrainZone("Rajasthan Plains", 110.0, 210.0, "coarse", 0.42, 0.55),
    TerrainZone("Yamuna Alluvial", 210.0, 250.0, "fine", 0.45, 0.40),
    TerrainZone("Haryana Plains", 250.0, 360.0, "fine", 0.45, 0.35),
]


def get_terrain_zone(km: float) -> TerrainZone:
    """Return the terrain zone for a given chainage [km]."""
    for zone in TERRAIN_ZONES:
        if zone.km_start <= km < zone.km_end:
            return zone
    return TERRAIN_ZONES[-1]
