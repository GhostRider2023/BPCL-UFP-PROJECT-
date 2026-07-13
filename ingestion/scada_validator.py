"""
SCADA CSV Validator and Enrichment
====================================

Validates organization-uploaded SCADA CSV batch records and
enriches missing data with physics-based defaults.

Validation rules:
  1. All mandatory columns present
  2. Product name is valid (petrol/diesel/atf)
  3. Numeric values within physical bounds
  4. Datetime is parseable

Auto-fill logic:
  - Missing T_dispatch_C → ERA5 ambient at dispatch location + datetime
  - Missing density_kgm3 → IS standard reference density for product
  - Missing T_receipt_C → flagged for model prediction

Units:
  - Volume:      KL
  - Temperature: °C
  - Density:     kg/m³
  - Flow rate:   m³/hr
"""

import os
import sys
from typing import List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    DENSITY_MAX_KGM3,
    DENSITY_MIN_KGM3,
    PRODUCTS,
    SCADA_MANDATORY_COLUMNS,
    T_MAX_C,
    T_MIN_C,
    VALID_PRODUCTS,
)
from model.soil_profile import soil_temperature_at


class ValidationError:
    """A single validation error or warning."""

    def __init__(self, row_index: int, column: str, severity: str, message: str):
        """
        Parameters
        ----------
        row_index : int
            Row index (0-based, -1 for header-level).
        column : str
            Column name.
        severity : str
            "error" or "warning".
        message : str
            Human-readable description.
        """
        self.row_index = row_index
        self.column = column
        self.severity = severity
        self.message = message

    def __repr__(self):
        if self.row_index >= 0:
            return f"[{self.severity.upper()}] Row {self.row_index + 1}, '{self.column}': {self.message}"
        return f"[{self.severity.upper()}] '{self.column}': {self.message}"


class ValidationResult:
    """Result of SCADA CSV validation."""

    def __init__(self):
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []
        self.auto_fills: List[str] = []
        self.is_valid: bool = True

    def add_error(self, row: int, col: str, msg: str):
        self.errors.append(ValidationError(row, col, "error", msg))
        self.is_valid = False

    def add_warning(self, row: int, col: str, msg: str):
        self.warnings.append(ValidationError(row, col, "warning", msg))

    def add_auto_fill(self, description: str):
        self.auto_fills.append(description)

    def summary(self) -> str:
        lines = []
        lines.append(f"Validation: {'PASSED' if self.is_valid else 'FAILED'}")
        lines.append(f"  Errors:    {len(self.errors)}")
        lines.append(f"  Warnings:  {len(self.warnings)}")
        lines.append(f"  Auto-fills: {len(self.auto_fills)}")

        if self.errors:
            lines.append("\nErrors:")
            for e in self.errors[:20]:  # show max 20
                lines.append(f"  {e}")

        if self.warnings:
            lines.append("\nWarnings:")
            for w in self.warnings[:20]:
                lines.append(f"  {w}")

        if self.auto_fills:
            lines.append("\nAuto-fills applied:")
            for af in self.auto_fills:
                lines.append(f"  • {af}")

        return "\n".join(lines)


def validate_scada_csv(
    df: pd.DataFrame,
    auto_fill: bool = True,
) -> Tuple[pd.DataFrame, ValidationResult]:
    """Validate and optionally enrich a SCADA CSV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Raw SCADA CSV data.
    auto_fill : bool
        If True, fill missing values with defaults.

    Returns
    -------
    tuple of (pd.DataFrame, ValidationResult)
        Cleaned DataFrame and validation result.
    """
    result = ValidationResult()

    # ── Step 1: Check mandatory columns ────────────────────────
    df.columns = [c.strip().lower() for c in df.columns]  # normalize

    # Map common column name variations
    column_aliases = {
        "batch": "batch_id",
        "product_name": "product",
        "product_type": "product",
        "dispatch_date": "dispatch_datetime",
        "dispatch_time": "dispatch_datetime",
        "v_dispatch": "v_dispatch_kl",
        "dispatch_volume_kl": "v_dispatch_kl",
        "t_dispatch": "t_dispatch_c",
        "dispatch_temp_c": "t_dispatch_c",
        "density": "density_kgm3",
        "density_kg_m3": "density_kgm3",
        "flow_rate": "flow_rate_m3hr",
        "v_receipt": "v_receipt_kl",
        "receipt_volume_kl": "v_receipt_kl",
        "t_receipt": "t_receipt_c",
        "receipt_temp_c": "t_receipt_c",
    }

    for old_name, new_name in column_aliases.items():
        if old_name in df.columns and new_name not in df.columns:
            df = df.rename(columns={old_name: new_name})

    missing_cols = [c for c in SCADA_MANDATORY_COLUMNS if c not in df.columns]
    for col in missing_cols:
        result.add_error(-1, col, f"Mandatory column '{col}' is missing")

    if missing_cols:
        return df, result

    # Make a copy for modification
    df = df.copy()

    # ── Step 2: Validate product names ─────────────────────────
    for idx, row in df.iterrows():
        product = str(row["product"]).strip().lower()
        if product not in VALID_PRODUCTS:
            result.add_error(
                idx,
                "product",
                f"Invalid product '{row['product']}'. Must be one of: {VALID_PRODUCTS}",
            )
        else:
            df.at[idx, "product"] = product

    # ── Step 3: Parse datetime ─────────────────────────────────
    for idx, row in df.iterrows():
        try:
            dt = pd.to_datetime(row["dispatch_datetime"])
            df.at[idx, "dispatch_datetime"] = dt
        except (ValueError, TypeError):
            result.add_error(
                idx, "dispatch_datetime", f"Cannot parse datetime: '{row['dispatch_datetime']}'"
            )

    # ── Step 4: Numeric validation and coercion ────────────────
    numeric_cols = [
        "v_dispatch_kl",
        "t_dispatch_c",
        "density_kgm3",
        "flow_rate_m3hr",
        "v_receipt_kl",
        "t_receipt_c",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ── Step 5: Range checks ───────────────────────────────────
    for idx, row in df.iterrows():
        # Temperature bounds
        for t_col in ["t_dispatch_c", "t_receipt_c"]:
            val = row[t_col]
            if pd.notna(val):
                if val < T_MIN_C or val > T_MAX_C:
                    result.add_warning(
                        idx,
                        t_col,
                        f"Temperature {val}°C outside expected range [{T_MIN_C}, {T_MAX_C}]°C",
                    )

        # Density bounds
        density = row["density_kgm3"]
        if pd.notna(density):
            if density < DENSITY_MIN_KGM3 or density > DENSITY_MAX_KGM3:
                result.add_warning(
                    idx,
                    "density_kgm3",
                    f"Density {density} kg/m³ outside expected range "
                    f"[{DENSITY_MIN_KGM3}, {DENSITY_MAX_KGM3}]",
                )

        # Volume must be positive
        for v_col in ["v_dispatch_kl", "v_receipt_kl"]:
            val = row[v_col]
            if pd.notna(val) and val <= 0:
                result.add_error(idx, v_col, f"Volume must be positive, got {val}")

        # Flow rate must be positive
        if pd.notna(row["flow_rate_m3hr"]) and row["flow_rate_m3hr"] <= 0:
            result.add_error(
                idx, "flow_rate_m3hr", f"Flow rate must be positive, got {row['flow_rate_m3hr']}"
            )

    # ── Step 6: Auto-fill missing values ───────────────────────
    if auto_fill:
        for idx, row in df.iterrows():
            product = str(row["product"]).strip().lower()
            if product not in PRODUCTS:
                continue

            prod_props = PRODUCTS[product]

            # Missing density → IS standard reference
            if pd.isna(row["density_kgm3"]):
                df.at[idx, "density_kgm3"] = prod_props.density_ref_kgm3
                result.add_auto_fill(
                    f"Row {idx + 1}: density_kgm3 set to {prod_props.density_ref_kgm3} "
                    f"({prod_props.is_standard} reference for {product})"
                )
                result.add_warning(
                    idx, "density_kgm3", "Missing density auto-filled with IS standard reference"
                )

            # Missing T_dispatch_C → derive from the ERA5 soil temperature at
            # the ERA5 cell nearest the dispatch terminal, for that month.
            # NEVER fall back to a constant "reasonable ambient": a fixed 30 °C
            # discards the seasonal signal (origin soil ranges 20.9–33.5 °C
            # across the year) and biases the dispatch VCF in every winter month.
            if pd.isna(row["t_dispatch_c"]):
                try:
                    month = pd.to_datetime(row["dispatch_datetime"]).month
                    T_origin = soil_temperature_at(km=0.0, month=month)
                except (ValueError, TypeError) as exc:
                    result.add_error(
                        idx,
                        "t_dispatch_c",
                        f"Missing dispatch temperature and it cannot be derived from ERA5: {exc}",
                    )
                    continue

                df.at[idx, "t_dispatch_c"] = round(T_origin, 2)
                result.add_auto_fill(
                    f"Row {idx + 1}: t_dispatch_c set to {T_origin:.2f}°C — ERA5 "
                    f"soil temperature at the dispatch terminal, month {month} "
                    f"(physics-derived, not a constant)"
                )
                result.add_warning(
                    idx,
                    "t_dispatch_c",
                    f"Missing dispatch temperature; derived {T_origin:.2f}°C from "
                    f"the ERA5 cell nearest the origin",
                )

            # Missing T_receipt_C → the heat-transfer model supplies it.
            # This is the DESIGNED path, not a degraded one: receipt temperature
            # is exactly what the physics engine exists to predict.
            if pd.isna(row["t_receipt_c"]):
                result.add_warning(
                    idx,
                    "t_receipt_c",
                    "Missing receipt temperature — will be solved from the "
                    "heat-transfer model against the monthly ERA5 soil profile",
                )

            # Missing flow_rate_m3hr → use reasonable default
            if pd.isna(row["flow_rate_m3hr"]):
                df.at[idx, "flow_rate_m3hr"] = 150.0  # typical
                result.add_auto_fill(f"Row {idx + 1}: flow_rate_m3hr set to 150.0 (default)")

    return df, result


def load_and_validate_csv(
    filepath: str,
    auto_fill: bool = True,
) -> Tuple[pd.DataFrame, ValidationResult]:
    """Load a SCADA CSV file and validate it.

    Parameters
    ----------
    filepath : str
        Path to the CSV file.
    auto_fill : bool
        Whether to auto-fill missing values.

    Returns
    -------
    tuple of (pd.DataFrame, ValidationResult)
    """
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        result = ValidationResult()
        result.add_error(-1, "file", f"Cannot read CSV: {e}")
        return pd.DataFrame(), result

    return validate_scada_csv(df, auto_fill=auto_fill)


def generate_sample_csv(filepath: str, n_batches: int = 10):
    """Generate a sample SCADA CSV for testing.

    Parameters
    ----------
    filepath : str
        Output file path.
    n_batches : int
        Number of sample batch records.
    """
    from model.heat_transfer import get_receipt_temperature, solve_from_csv_profile
    from model.soil_profile import available_months, soil_temperature_at
    from model.ufp import resolve_rho_60
    from model.vcf import compute_ctl

    rng = np.random.RandomState(42)
    records = []

    products = ["petrol", "diesel", "atf"]
    months = available_months()

    for i in range(n_batches):
        product = products[i % len(products)]
        prod_props = PRODUCTS[product]
        month = months[i % len(months)]

        # Dispatch temperature: terminal tankage equilibrates toward the ground
        # temperature at the origin. Derived from the ERA5 cell nearest the
        # dispatch terminal — never a hardcoded per-month band.
        T_dispatch = float(soil_temperature_at(km=0.0, month=month) + rng.normal(0.0, 3.0))

        density = round(prod_props.density_ref_kgm3 + rng.uniform(-10, 10), 1)
        V_dispatch = round(rng.uniform(500, 2000), 2)
        flow_rate = round(rng.uniform(100, 250), 1)

        # Receipt temperature FROM THE VALIDATED HEAT-TRANSFER MODEL, against
        # the monthly ERA5 soil profile. The old hardcoded receipt bands were a
        # fiction that never touched the physics.
        profile = solve_from_csv_profile(
            product_name=product,
            T_dispatch_C=T_dispatch,
            flow_rate_m3hr=flow_rate,
            density_kgm3=density,
            month=month,
        )
        T_receipt = get_receipt_temperature(profile)

        # Mass-conserving receipt volume (no injected loss here — this fixture
        # is the clean baseline; leak injection belongs in the Phase 6 harness).
        rho_60 = resolve_rho_60(product, density, T_dispatch)
        ctl_d = compute_ctl(rho_60, T_dispatch, prod_props)
        ctl_r = compute_ctl(rho_60, T_receipt, prod_props)
        V_receipt = (V_dispatch * ctl_d) / ctl_r

        records.append(
            {
                "batch_id": f"BATCH-2025{month:02d}-{i + 1:03d}",
                "product": product,
                "dispatch_datetime": f"2025-{month:02d}-{rng.randint(1, 28):02d}"
                f"T{rng.randint(0, 23):02d}:00:00",
                "V_dispatch_KL": round(V_dispatch, 2),
                "T_dispatch_C": round(T_dispatch, 1),
                "density_kgm3": density,
                "flow_rate_m3hr": flow_rate,
                "V_receipt_KL": round(V_receipt, 2),
                "T_receipt_C": round(T_receipt, 1),
            }
        )

    df = pd.DataFrame(records)
    df.to_csv(filepath, index=False)
    print(f"Sample SCADA CSV saved to: {filepath}")
    return df


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    print("SCADA CSV Validator — Test")
    print("=" * 55)

    # Generate and validate sample data
    sample_path = os.path.join(os.path.dirname(__file__), "..", "..", "sample_scada.csv")
    sample_df = generate_sample_csv(sample_path)

    df, result = load_and_validate_csv(sample_path)
    print(f"\n{result.summary()}")
    print(f"\nValidated records: {len(df)}")
    print(df.head().to_string())
