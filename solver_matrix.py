"""Solver-matrix preparation.

This module builds the internal calculation matrix used before the PuLP
optimization model is created.
It collects the engineering coefficients needed by the optimizer.

--------------------------
Equations differ by BMP type and target.
This module separates the logic into three calculation groups:

1. Peak-flow/runoff-volume capture
   * storage BMPs: use BMP storage depth
   * infiltration BMPs: use infiltration depth during the user-defined storm duration
     plus ponding/media storage

2. TSS removal for storage BMPs
   * dry ponds and wet ponds use the settling-based formula

3. Pollutant removal for all other pollutant/BMP combinations
   * uses the zero-order decay expression:
     Abmp_100 = pollutant_loading_rate / (decay_rate * BMP_depth)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_processing import ACRE_TO_FT2, SUPPORTED_PARAMETERS, load_bmp_efficiencies_table

TARGET_PARAMETERS = SUPPORTED_PARAMETERS

POLLUTANT_COLUMNS = {
    "TSS": "TSS (lb)",
    "TP": "TP (lb)",
    "TN": "TN (lb)",
    "NO3": "NO3 (lb)",
    "PO4": "PO4 (lb)",
    "Zn": "Zn (lb)",
    "Cu": "Cu (lb)",
    "Pb": "Pb (lb)",
    "As": "As (lb)",
    "Cr": "Cr (lb)",
    "Ni": "Ni (lb)",
    "Fe": "Fe (lb)",
    "Fecal coliform": "Fecal coliform (lb)",
    "E. coli": "E. coli (lb)",
}

def _as_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a table value to ``float``."""

    try:
        if value is None or value == "":
            return default
        out = float(value)
        if np.isnan(out):
            return default
        return out
    except Exception:
        return default

def _clean_text(value: Any) -> str:
    """Return a stripped string while safely handling missing values."""

    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()

def _is_storage_bmp(bmp_type: str) -> bool:
    return _clean_text(bmp_type).lower() == "storage"

def load_decay_rate_table(data_dir: str | Path) -> pd.DataFrame:
    """Load the validated wide decay-rate CSV as a long table."""
    df = pd.read_csv(Path(data_dir) / "decay_rates.csv")
    df.columns = [_clean_text(c) for c in df.columns]
    if not len(df.columns) or df.columns[0] != "Parameter":
        raise ValueError("decay_rates.csv column A must be Parameter.")
    out = df.melt(id_vars=["Parameter"], var_name="BMP", value_name="Decay rate")
    out["Parameter"] = out["Parameter"].map(_clean_text)
    out["BMP"] = out["BMP"].map(_clean_text)
    out["Decay rate"] = pd.to_numeric(out["Decay rate"], errors="coerce").fillna(0.0).clip(lower=0.0)
    return out

def _infiltration_capture_depth_in(bmp: pd.Series, storm_duration_hours: float) -> float:
    """Effective infiltration/ponding/media capture depth, in inches."""
    duration = max(_as_float(storm_duration_hours), 0.0)
    return (
        _as_float(bmp.get("v_sqms", 0.0)) * duration
        + _as_float(bmp.get("ponding_depth_in", 0.0))
        + _as_float(bmp.get("soil_depth_in", 0.0)) * _as_float(bmp.get("soil_moisture", 0.0))
        + _as_float(bmp.get("gravel_depth_in", 0.0)) * _as_float(bmp.get("gravel_moisture", 0.0))
    )

def _storage_capture_depth_in(bmp: pd.Series) -> float:
    return max(_as_float(bmp.get("depth_ft", 0.0)) * 12.0, 0.0)

def _calculate_peakflow_row(
    *,
    area_ft2: float,
    runoff_depth_in: float,
    bmp: pd.Series,
    storm_duration_hours: float,
) -> dict[str, float | str]:
    """Calculate runoff-capture matrix variables for one BMP/subbasin.

    Storage BMPs and infiltration BMPs use different capture-depth definitions.
    Storage and infiltration BMPs use their respective capture-depth equations.
    """

    bmp_type = _clean_text(bmp.get("bmp_type", "")).lower()

    if bmp_type == "storage":
        capture_depth_in = _storage_capture_depth_in(bmp)
        formula_type = "peak-flow capture - storage"
    else:
        capture_depth_in = _infiltration_capture_depth_in(bmp, storm_duration_hours)
        formula_type = "peak-flow capture - infiltration"

    if area_ft2 <= 0 or runoff_depth_in <= 0 or capture_depth_in <= 0:
        return {
            "abmp_100": 0.0,
            "abmp_to_aimp": 0.0,
            "max_eff_available": 0.0,
            "abmp_for_max_eff": 0.0,
            "effective_depth_in": capture_depth_in,
            "formula_type": formula_type,
        }

    abmp_100 = area_ft2 * runoff_depth_in / capture_depth_in
    abmp_to_aimp = abmp_100 / area_ft2
    max_eff_available = 1.0 if abmp_to_aimp < 1.0 else capture_depth_in / runoff_depth_in
    max_eff_available = min(1.0, max(0.0, max_eff_available))
    abmp_for_max_eff = area_ft2 if max_eff_available < 1.0 else abmp_100

    return {
        "abmp_100": abmp_100,
        "abmp_to_aimp": abmp_to_aimp,
        "max_eff_available": max_eff_available,
        "abmp_for_max_eff": abmp_for_max_eff,
        "effective_depth_in": capture_depth_in,
        "formula_type": formula_type,
    }

def _storage_tss_settling_velocity_ft_s(bmp: pd.Series, tss_fraction: float) -> float:
    """Calculate storage-BMP TSS settling velocity.

    Formula:

        nu*3.28084/(Dp*0.001) *
        (sqrt(10.36^2 + 1.049*(1-C)^4.7*g*(SG-1)/nu^2*(Dp*0.001)^3) - 10.36)

    where:
    * ``Dp_mm`` = representative particle diameter in mm
    * ``SG`` = particle specific gravity
    * ``storage_v_sqms`` = kinematic viscosity in m2/s
    * ``C`` = TSS fraction for the subbasin, TSS_sub / sum(TSS_all_subbasins)

    Returns ft/s. If inputs are incomplete, returns 0.
    """

    dp_mm = _as_float(bmp.get("Dp_mm", 0.0))
    sg = _as_float(bmp.get("SG", 0.0))
    nu = _as_float(bmp.get("storage_v_sqms", 0.0))
    c = min(1.0, max(0.0, _as_float(tss_fraction, 0.0)))

    if dp_mm <= 0 or sg <= 1.0 or nu <= 0:
        return 0.0

    dp_m = dp_mm * 0.001
    g = 9.81
    term = (10.36**2) + 1.049 * ((1.0 - c) ** 4.7) * g * (sg - 1.0) / (nu**2) * (dp_m**3)
    settling_velocity_ft_s = (nu * 3.28084 / dp_m) * (np.sqrt(term) - 10.36)
    return max(float(settling_velocity_ft_s), 0.0)

def _calculate_storage_tss_row(
    *,
    tss_rate: float,
    peak_flow_cfs: float,
    area_ft2: float,
    impervious_percent: float,
    runoff_depth_in: float,
    bmp: pd.Series,
    tss_fraction: float,
) -> dict[str, float | str]:
    """Calculate TSS removal variables for dry/wet ponds.

    The storage TSS method depends on hydraulic loading, particle properties,
    and the subbasin TSS fraction.
    """

    depth_ft = _as_float(bmp.get("depth_ft", 0.0))
    settling = _storage_tss_settling_velocity_ft_s(bmp, tss_fraction)
    runoff_volume_ft3 = area_ft2 * runoff_depth_in / 12.0

    if tss_rate <= 0 or peak_flow_cfs <= 0 or area_ft2 <= 0 or depth_ft <= 0 or runoff_volume_ft3 <= 0:
        return {
            "abmp_100": 0.0,
            "abmp_to_aimp": 0.0,
            "max_eff_available": 0.0,
            "abmp_for_max_eff": 0.0,
            "formula_type": "TSS settling - storage",
            "settling_velocity_ft_s": settling,
        }

    # Required BMP area from settling and hydraulic storage terms.
    denominator = 0.0
    if settling > 0:
        denominator += settling / peak_flow_cfs 
    denominator += depth_ft / runoff_volume_ft3

    abmp_100 = 1.0 / denominator if denominator > 0 else 0.0
    abmp_to_aimp = abmp_100 / area_ft2 if area_ft2 > 0 else 0.0

    if abmp_to_aimp < 1.0:
        max_eff_available = 1.0
    else:
        # Available efficiency when required BMP area exceeds subbasin area.
        inv_imp = 0.0001 if impervious_percent <= 0 else 100.0 / impervious_percent
        max_eff_denominator = (1.0 / area_ft2 / inv_imp) - (depth_ft / runoff_volume_ft3)
        if max_eff_denominator > 0 and settling > 0:
            max_eff_available = settling / max_eff_denominator * tss_rate
        else:
            max_eff_available = 0.0

    max_eff_available = min(1.0, max(0.0, float(max_eff_available)))
    abmp_for_max_eff = area_ft2 if max_eff_available < 1.0 else abmp_100

    return {
        "abmp_100": abmp_100,
        "abmp_to_aimp": abmp_to_aimp,
        "max_eff_available": max_eff_available,
        "abmp_for_max_eff": abmp_for_max_eff,
        "formula_type": "TSS settling - storage",
        "settling_velocity_ft_s": settling,
    }

def _calculate_decay_pollutant_row(
    *,
    pollutant_rate: float,
    decay_rate: float,
    bmp_depth_ft: float,
    area_ft2: float,
) -> dict[str, float | str]:
    """Calculate pollutant matrix variables using the zero-order decay logic."""

    if pollutant_rate <= 0 or decay_rate <= 0 or bmp_depth_ft <= 0 or area_ft2 <= 0:
        return {
            "abmp_100": 0.0,
            "abmp_to_aimp": 0.0,
            "max_eff_available": 0.0,
            "abmp_for_max_eff": 0.0,
            "formula_type": "pollutant decay",
        }

    abmp_100 = pollutant_rate / decay_rate / bmp_depth_ft
    abmp_to_aimp = abmp_100 / area_ft2
    max_eff_available = 1.0 if abmp_to_aimp < 1.0 else bmp_depth_ft * decay_rate * area_ft2 / pollutant_rate
    max_eff_available = min(1.0, max(0.0, max_eff_available))
    abmp_for_max_eff = area_ft2 if max_eff_available < 1.0 else abmp_100

    return {
        "abmp_100": abmp_100,
        "abmp_to_aimp": abmp_to_aimp,
        "max_eff_available": max_eff_available,
        "abmp_for_max_eff": abmp_for_max_eff,
        "formula_type": "pollutant decay",
    }

def _calculate_bmp_units(area_required_ft2: float, bmp_area_ft2: float, max_units_by_area: float) -> float:
    """Calculate equivalent BMP units for the available efficiency.

    Equivalent BMP units remain continuous: ``Abmp / default_BMP_area``.
    Do not round because the optimization decision variable is continuous.
    """

    if area_required_ft2 <= 0 or bmp_area_ft2 <= 0 or max_units_by_area <= 0:
        return 0.0

    units = area_required_ft2 / bmp_area_ft2
    return min(float(units), float(max_units_by_area))

def _intrinsic_efficiency_lookup(data_dir: str | Path) -> dict[tuple[str, str], float]:
    wide = load_bmp_efficiencies_table(data_dir)
    long = wide.melt(id_vars=["Parameter"], var_name="BMP", value_name="Efficiency")
    return {
        (_clean_text(row["BMP"]), _clean_text(row["Parameter"])): min(1.0, max(0.0, _as_float(row["Efficiency"])))
        for _, row in long.iterrows()
    }

def build_solver_matrix_audit(
    subbasins_display: pd.DataFrame,
    bmps: pd.DataFrame,
    targets: pd.DataFrame,
    data_dir: str | Path,
    event_duration_seconds: float = 0.0,
    storm_duration_hours: float = 0.0,
) -> pd.DataFrame:
    """Build the long-format engineering matrix used for optimization and reporting.

    Rows are calculated for every parameter present in the watershed inputs.
    ``build_optimization_data`` later selects only parameters with a positive
    requested reduction for the LP constraints. Keeping the additional rows here
    allows Results to report incidental reductions for non-target pollutants.
    """

    if subbasins_display is None or subbasins_display.empty or bmps is None or bmps.empty:
        return pd.DataFrame()

    event_duration_seconds = _as_float(event_duration_seconds, 0.0)
    storm_duration_hours = _as_float(storm_duration_hours, 0.0)
    result_parameters = _present_result_parameters(targets)
    if not result_parameters:
        return pd.DataFrame()

    decay = load_decay_rate_table(data_dir)
    decay_lookup = {
        (_clean_text(row["BMP"]), _clean_text(row["Parameter"])): _as_float(row["Decay rate"])
        for _, row in decay.iterrows()
    }
    intrinsic_lookup = _intrinsic_efficiency_lookup(data_dir)

    target_units: dict[str, str] = {}
    if targets is not None and not targets.empty:
        for _, row in targets.iterrows():
            target_units[_clean_text(row.get("Parameter", ""))] = _clean_text(
                row.get("Internal Unit", "")
            )

    # TSS fraction is used by the storage-BMP settling formula.
    if event_duration_seconds > 0 and "TSS (lb)" in subbasins_display.columns:
        tss_rates = pd.to_numeric(subbasins_display["TSS (lb)"], errors="coerce").fillna(0.0) / event_duration_seconds
    else:
        tss_rates = pd.Series([0.0] * len(subbasins_display), index=subbasins_display.index)
    total_tss_rate = float(tss_rates.sum())

    rows: list[dict[str, Any]] = []

    for sub_idx, sub in subbasins_display.iterrows():
        subbasin = _clean_text(sub.get("Sub", ""))
        area_ac = _as_float(sub.get("area (ac)", 0.0))
        area_ft2 = area_ac * ACRE_TO_FT2
        impervious_percent = _as_float(sub.get("imp (%)", 0.0))
        runoff_depth_in = _as_float(sub.get("runoff depth(in)", 0.0))
        peak_flow_cfs = _as_float(sub.get("Peak flow-raw (cfs)", 0.0))
        tss_rate_for_sub = _as_float(tss_rates.loc[sub_idx], 0.0)
        tss_fraction = tss_rate_for_sub / total_tss_rate if total_tss_rate > 0 else 0.0

        for _, bmp in bmps.iterrows():
            bmp_name = _clean_text(bmp.get("bmp_name", ""))
            if not bmp_name or bmp_name.lower() == "no bmp":
                continue

            bmp_area_ft2 = _as_float(bmp.get("area_ft2", 0.0))
            bmp_depth_ft = _as_float(bmp.get("depth_ft", 0.0))
            max_units_by_area = (area_ft2 / bmp_area_ft2) if bmp_area_ft2 > 0 and area_ft2 > 0 else 0.0
            bmp_type = _clean_text(bmp.get("bmp_type", "")).lower()

            for parameter in result_parameters:
                settling_velocity = ""
                if parameter == "Peak Flow":
                    input_value = peak_flow_cfs
                    unit = "cfs"
                    decay_rate = np.nan
                    calc = _calculate_peakflow_row(
                        area_ft2=area_ft2,
                        runoff_depth_in=runoff_depth_in,
                        bmp=bmp,
                        storm_duration_hours=storm_duration_hours,
                    )
                    effective_depth_in = calc.get("effective_depth_in", 0.0)
                    formula_type = calc.get("formula_type", "peak-flow capture")
                else:
                    source_col = POLLUTANT_COLUMNS[parameter]
                    pollutant_load_lb = _as_float(sub.get(source_col, 0.0))
                    input_value = pollutant_load_lb / event_duration_seconds if event_duration_seconds > 0 else 0.0
                    unit = target_units.get(parameter, "lb/s")
                    decay_rate = decay_lookup.get((bmp_name, parameter), 0.0)
                    effective_depth_in = bmp_depth_ft * 12.0

                    if parameter == "TSS" and _is_storage_bmp(bmp_type):
                        calc = _calculate_storage_tss_row(
                            tss_rate=input_value,
                            peak_flow_cfs=peak_flow_cfs,
                            area_ft2=area_ft2,
                            impervious_percent=impervious_percent,
                            runoff_depth_in=runoff_depth_in,
                            bmp=bmp,
                            tss_fraction=tss_fraction,
                        )
                        formula_type = calc.get("formula_type", "TSS settling - storage")
                        settling_velocity = calc.get("settling_velocity_ft_s", "")
                    else:
                        calc = _calculate_decay_pollutant_row(
                            pollutant_rate=input_value,
                            decay_rate=decay_rate,
                            bmp_depth_ft=bmp_depth_ft,
                            area_ft2=area_ft2,
                        )
                        formula_type = calc.get("formula_type", "pollutant decay")

                bmp_units_for_max_eff = _calculate_bmp_units(
                    area_required_ft2=_as_float(calc["abmp_for_max_eff"]),
                    bmp_area_ft2=bmp_area_ft2,
                    max_units_by_area=max_units_by_area,
                )
                intrinsic_eff = intrinsic_lookup.get((bmp_name, parameter), 1.0)
                max_eff_available = min(1.0, max(0.0, _as_float(calc["max_eff_available"])))
                effective_max_eff = max_eff_available * intrinsic_eff

                rows.append(
                    {
                        "Subbasin": subbasin,
                        "BMP": bmp_name,
                        "BMP type": bmp_type,
                        "Target parameter": parameter,
                        "Formula type": formula_type,
                        "Input value": input_value,
                        "Unit": unit,
                        "Subbasin area (ac)": round(area_ac, 6),
                        "Subbasin area (ft2)": round(area_ft2, 3),
                        "Impervious (%)": round(impervious_percent, 6),
                        "Runoff depth (in)": round(runoff_depth_in, 6),
                        "BMP area (ft2)": round(bmp_area_ft2, 3),
                        "BMP depth (ft)": round(bmp_depth_ft, 6),
                        "Effective capture depth (in)": round(_as_float(effective_depth_in), 6),
                        "Decay rate": round(decay_rate, 10) if not pd.isna(decay_rate) else "",
                        "TSS fraction": round(tss_fraction, 10) if parameter == "TSS" else "",
                        "Storage Dp_mm": round(_as_float(bmp.get("Dp_mm", 0.0)), 12) if parameter == "TSS" else "",
                        "Storage SG": round(_as_float(bmp.get("SG", 0.0)), 12) if parameter == "TSS" else "",
                        "Storage v_sqms": round(_as_float(bmp.get("storage_v_sqms", 0.0)), 12) if parameter == "TSS" else "",
                        "Settling velocity (ft/s)": round(_as_float(settling_velocity), 12) if settling_velocity != "" else "",
                        "Abmp for 100% eff (ft2)": round(_as_float(calc["abmp_100"]), 10),
                        "Abmp/Aimp": round(_as_float(calc["abmp_to_aimp"]), 10),
                        "# BMP for max eff available": bmp_units_for_max_eff,
                        "Max eff available (Abmp=Aimp)": max_eff_available,
                        "Intrinsic BMP efficiency": intrinsic_eff,
                        "Effective max removal efficiency": effective_max_eff,
                        "Abmp for max eff available (ft2)": round(_as_float(calc["abmp_for_max_eff"]), 10),
                        "Max units by area": max_units_by_area,
                    }
                )

    return pd.DataFrame(rows)

def build_constraint_matrix_from_audit(audit_matrix: pd.DataFrame) -> pd.DataFrame:
    """Summarize the audit matrix into constraint-oriented coefficients."""

    if audit_matrix is None or audit_matrix.empty:
        return pd.DataFrame()

    out = audit_matrix.copy()
    out["Input value"] = pd.to_numeric(out["Input value"], errors="coerce").fillna(0.0)
    out["Max eff available (Abmp=Aimp)"] = pd.to_numeric(out["Max eff available (Abmp=Aimp)"], errors="coerce").fillna(0.0)
    if "Effective max removal efficiency" in out.columns:
        effective_eff = pd.to_numeric(out["Effective max removal efficiency"], errors="coerce").fillna(0.0)
    else:
        effective_eff = out["Max eff available (Abmp=Aimp)"]
    out["Estimated removed value at max eff"] = out["Input value"] * effective_eff
    out["Estimated remaining value at max eff"] = out["Input value"] - out["Estimated removed value at max eff"]

    keep = [
        "Subbasin",
        "BMP",
        "BMP type",
        "Target parameter",
        "Formula type",
        "Input value",
        "Max eff available (Abmp=Aimp)",
        "Intrinsic BMP efficiency",
        "Effective max removal efficiency",
        "Estimated removed value at max eff",
        "Estimated remaining value at max eff",
        "Unit",
        "# BMP for max eff available",
    ]
    # Availability fields are added by app.py after the engineering equations
    # are built. Preserve them in this compact constraint audit when present.
    keep += [
        c
        for c in ["Allowed", "Exclusion reason", "Decision upper bound"]
        if c in out.columns
    ]
    return out[keep]

def _active_target_parameters(targets: pd.DataFrame | None) -> list[str]:
    """Return parameters with a positive requested reduction."""
    if targets is None or targets.empty:
        return []
    active = {
        _clean_text(row.get("Parameter", ""))
        for _, row in targets.iterrows()
        if _as_float(row.get("% reduction", 0.0)) > 0
        and _as_float(row.get("Input value", 0.0)) > 0
        and _as_float(row.get("Target", 0.0)) < _as_float(row.get("Input value", 0.0))
    }
    return [p for p in TARGET_PARAMETERS if p in active]

def _present_result_parameters(targets: pd.DataFrame | None) -> list[str]:
    """Return parameters that are present in the watershed input data.

    Unlike ``_active_target_parameters``, this includes parameters with a 0%
    requested reduction so their incidental/intrinsic treatment performance can
    still be reported after the optimization is solved.
    """
    if targets is None or targets.empty:
        return []
    present = {
        _clean_text(row.get("Parameter", ""))
        for _, row in targets.iterrows()
        if _as_float(row.get("Input value", 0.0)) > 0
    }
    return [p for p in TARGET_PARAMETERS if p in present]

def _baseline_value(sub: pd.Series, parameter: str, event_duration_seconds: float) -> float:
    if parameter == "Peak Flow":
        return _as_float(sub.get("Peak flow-raw (cfs)", 0.0))
    load = _as_float(sub.get(POLLUTANT_COLUMNS[parameter], 0.0))
    return load / event_duration_seconds if event_duration_seconds > 0 else 0.0


def build_optimization_data(
    subbasins_display: pd.DataFrame,
    bmps: pd.DataFrame,
    targets: pd.DataFrame,
    audit_matrix: pd.DataFrame,
    event_duration_seconds: float = 0.0,
    include_no_bmp: bool = True,
) -> pd.DataFrame:
    """Build the PuLP coefficient matrix from the engineering audit matrix."""
    active_targets = _active_target_parameters(targets)
    if subbasins_display is None or subbasins_display.empty or not active_targets:
        return pd.DataFrame()

    event_duration_seconds = _as_float(event_duration_seconds)
    bmps = pd.DataFrame() if bmps is None else bmps
    audit = pd.DataFrame() if audit_matrix is None else audit_matrix.copy()
    unit_cost = {
        _clean_text(row.get("bmp_name", "")): _as_float(row.get("unit_total_cost", 0.0))
        for _, row in bmps.iterrows()
    }
    rows: list[dict[str, Any]] = []

    for _, sub in subbasins_display.iterrows():
        subbasin = _clean_text(sub.get("Sub", ""))
        sub_audit = audit[audit["Subbasin"] == subbasin] if not audit.empty else pd.DataFrame()

        if not sub_audit.empty:
            for bmp_name in sub_audit["BMP"].drop_duplicates().tolist():
                group = sub_audit[sub_audit["BMP"] == bmp_name]
                first = group.iloc[0]
                row: dict[str, Any] = {
                    "Subbasin": subbasin,
                    "BMP": bmp_name,
                    "BMP type": _clean_text(first["BMP type"]),
                    "BMP unit total cost": unit_cost.get(bmp_name, 0.0),
                    "Max units by area": _as_float(first["Max units by area"]),
                }
                units = []
                for parameter in active_targets:
                    match = group[group["Target parameter"] == parameter]
                    if match.empty:
                        continue
                    item = match.iloc[0]
                    before = _as_float(item["Input value"])
                    max_eff = _as_float(item["Max eff available (Abmp=Aimp)"])
                    intrinsic = _as_float(item["Intrinsic BMP efficiency"], 1.0)
                    effective = _as_float(item["Effective max removal efficiency"])
                    max_units = _as_float(item["# BMP for max eff available"])
                    row[parameter] = before * (1.0 - effective)
                    row[f"{parameter} max eff"] = max_eff
                    row[f"{parameter} intrinsic efficiency"] = intrinsic
                    row[f"{parameter} effective max efficiency"] = effective
                    row[f"{parameter} max units"] = max_units
                    units.append(max_units)

                max_units = max(units) if units else 0.0
                row["Max BMP units across active targets"] = max_units
                row["Cost coefficient"] = row["BMP unit total cost"] * max_units
                rows.append(row)

        if include_no_bmp:
            row = {
                "Subbasin": subbasin, "BMP": "No BMP", "BMP type": "none",
                "BMP unit total cost": 0.0, "Max units by area": 0.0,
                "Max BMP units across active targets": 0.0, "Cost coefficient": 0.0,
            }
            for parameter in active_targets:
                row[parameter] = _baseline_value(sub, parameter, event_duration_seconds)
                row[f"{parameter} max eff"] = 0.0
                row[f"{parameter} max units"] = 0.0
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    first = [
        "Subbasin", "BMP", "BMP type", "Cost coefficient", "BMP unit total cost",
        "Max BMP units across active targets", "Max units by area",
    ]
    target_cols = [p for p in active_targets if p in out.columns]
    detail_cols = [c for c in out.columns if c not in first + target_cols]
    return out[first + target_cols + detail_cols]

