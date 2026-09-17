"""Post-processing, plots, and downloads for iBMP Solver.

Reported before/after values are reconstructed from solved allocation fractions
and the coefficient matrix passed to PuLP.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from textwrap import wrap
from typing import Any, Sequence
import math

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from data_processing import ACRE_TO_FT2, SUPPORTED_PARAMETERS

TARGET_PARAMETERS = SUPPORTED_PARAMETERS
CFS_TO_CMS = 0.028316846592
LB_TO_KG = 0.45359237
EPS = 1e-7

def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default

def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").replace("\xa0", " ").strip()

def _sub_sort_key(value: Any) -> tuple[int, str]:
    text = _clean(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return (int(digits) if digits else 10**9, text)

def round_numeric_columns(df: pd.DataFrame, decimals: int = 2) -> pd.DataFrame:
    """Return a display-only copy of a DataFrame with numeric columns rounded."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    numeric_columns = out.select_dtypes(include="number").columns
    out[numeric_columns] = out[numeric_columns].round(decimals)
    return out

def _positive_placement(solution: dict, include_no_bmp: bool = True) -> pd.DataFrame:
    placement = solution.get("placement", pd.DataFrame())
    if placement is None or placement.empty:
        return pd.DataFrame()
    out = placement.copy()
    out["subbasin"] = out.get("subbasin", "").astype(str)
    out["bmp_name"] = out.get("bmp_name", "").astype(str)
    out["decision_value"] = pd.to_numeric(out.get("decision_value", 0.0), errors="coerce").fillna(0.0)
    out = out[out["decision_value"] > EPS].copy()
    if not include_no_bmp:
        out = out[out["bmp_name"] != "No BMP"].copy()
    return out

def _solved_coefficients(solution: dict) -> pd.DataFrame:
    """Return the exact wide coefficient matrix used by PuLP."""
    coef = solution.get("coefficient_matrix", pd.DataFrame())
    if coef is None or coef.empty:
        return pd.DataFrame()
    out = coef.copy()
    out["Subbasin"] = out.get("Subbasin", "").astype(str)
    out["BMP"] = out.get("BMP", "").astype(str)
    return out

def _active_result_parameters(solution: dict) -> list[str]:
    coef = _solved_coefficients(solution)
    if coef.empty:
        return []
    constraints = solution.get("active_constraints", pd.DataFrame())
    ordered: list[str] = []
    if constraints is not None and not constraints.empty and "Parameter" in constraints.columns:
        ordered = [str(x) for x in constraints["Parameter"].tolist() if str(x) in coef.columns]
    if not ordered:
        ordered = [p for p in TARGET_PARAMETERS if p in coef.columns]
    return list(dict.fromkeys(ordered))

def _target_definition_lookup(target_definitions: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for definition in target_definitions or []:
        parameter = _clean(definition.get("parameter", ""))
        if parameter:
            out[parameter] = dict(definition)
    return out

def _display_factor(parameter: str, definition: dict[str, Any], event_duration_seconds: float) -> float:
    return max(_num(event_duration_seconds), 0.0) if str(definition.get("method", "")) == "load_rate" else 1.0

def _display_unit(parameter: str, definition: dict[str, Any]) -> str:
    if definition:
        return _clean(definition.get("unit", definition.get("internal_unit", "")))
    return "cfs" if parameter == "Peak Flow" else "internal units"

def _internal_unit(parameter: str, definition: dict[str, Any]) -> str:
    if definition:
        return _clean(definition.get("internal_unit", definition.get("unit", "")))
    return "cfs" if parameter == "Peak Flow" else "internal units"

def _coefficient_value(coef: pd.DataFrame, subbasin: str, bmp_name: str, parameter: str) -> float | None:
    if coef.empty or parameter not in coef.columns:
        return None
    match = coef[(coef["Subbasin"] == str(subbasin)) & (coef["BMP"] == str(bmp_name))]
    if match.empty:
        return None
    value = pd.to_numeric(match.iloc[0][parameter], errors="coerce")
    return None if pd.isna(value) else float(value)

def _subbasin_internal_before_after(solution: dict, subbasin: str, parameter: str) -> tuple[float, float] | None:
    """Reconstruct one subbasin from the solved allocation.

    Before is the untreated No-BMP coefficient; after is the allocation-weighted
    sum of remaining coefficients, including any No BMP bypass fraction.
    """
    coef = _solved_coefficients(solution)
    placement = _positive_placement(solution, include_no_bmp=True)
    if coef.empty or placement.empty or parameter not in coef.columns:
        return None

    before = _coefficient_value(coef, subbasin, "No BMP", parameter)
    if before is None:
        return None

    psub = placement[placement["subbasin"] == str(subbasin)]
    after = 0.0
    for _, row in psub.iterrows():
        remaining = _coefficient_value(coef, subbasin, str(row["bmp_name"]), parameter)
        if remaining is None:
            # This should not happen for a solved option, but falling back to the
            # untreated coefficient is conservative and keeps reporting robust.
            remaining = before
        after += _num(row["decision_value"]) * remaining
    return float(before), float(after)

def summarize_solution(solution: dict) -> pd.DataFrame:
    """Compact optimization overview using actual (not co-benefit-adjusted) cost."""
    placement = _positive_placement(solution, include_no_bmp=True)
    real = _positive_placement(solution, include_no_bmp=False)
    total_cost = pd.to_numeric(real.get("total_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum() if not real.empty else 0.0
    total_units = pd.to_numeric(real.get("estimated_units", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum() if not real.empty else 0.0
    return pd.DataFrame([
        {"Metric": "Solver status", "Value": solution.get("status", "Unknown")},
        {"Metric": "Objective function", "Value": solution.get("objective_name", "Cost Only")},
        {"Metric": "Co-benefit weight lambda", "Value": round(_num(solution.get("lambda_cobenefit", 0.0)), 4)},
        {"Metric": "Objective value", "Value": round(_num(solution.get("objective_value", 0.0)), 2)},
        {"Metric": "Actual life-cycle cost ($)", "Value": round(float(total_cost), 2)},
        {"Metric": "Actual life-cycle cost (M$)", "Value": round(float(total_cost) / 1e6, 4)},
        {"Metric": "Implemented equivalent BMP units", "Value": round(float(total_units), 3)},
        {"Metric": "Real BMP allocations", "Value": int(len(real))},
        {"Metric": "Treated subbasins", "Value": int(real["subbasin"].nunique()) if not real.empty else 0},
    ])

def allocation_cost_summary(solution: dict, bmp_costs: pd.DataFrame | None) -> pd.DataFrame:
    """BMP allocation and life-cycle cost table."""
    real = _positive_placement(solution, include_no_bmp=False)
    columns = [
        "ID", "Subbasin", "BMP Type", "Maximum BMP Units",
        "Implementation fraction", "Implemented BMP Units",
        "Total Cost (10^6 $)", "Construction Cost (10^6 $)", "O&M Cost (10^6 $)", "Land Cost (10^6 $)",
    ]
    if real.empty:
        return pd.DataFrame(columns=columns)

    costs = pd.DataFrame() if bmp_costs is None else bmp_costs.copy()
    if not costs.empty:
        costs["_bmp_key"] = costs["bmp_name"].map(_clean)
        costs = costs.drop_duplicates("_bmp_key", keep="first")

    rows: list[dict[str, Any]] = []
    real = real.sort_values("subbasin", key=lambda s: s.map(_sub_sort_key))
    for idx, (_, item) in enumerate(real.iterrows(), start=1):
        bmp = _clean(item.get("bmp_name", ""))
        units = _num(item.get("estimated_units", 0.0))
        total_cost = _num(item.get("total_cost", 0.0))
        bmp_type = ""
        construction = om = land = 0.0
        if not costs.empty and "_bmp_key" in costs.columns:
            match = costs[costs["_bmp_key"] == bmp]
            if not match.empty:
                c = match.iloc[0]
                bmp_type = _clean(c.get("bmp_type", "")).title()
                volume = _num(c.get("unit_volume_ft3", c.get("volume_ft3", 0.0)))
                enr = _num(c.get("cost_coefficient_enr", 1.0), 1.0)
                construction = units * volume * _num(c.get("construction_cost_per_ft3", 0.0)) * enr
                om = units * volume * _num(c.get("om_present_cost_per_ft3", 0.0)) * enr
                land = units * volume * _num(c.get("land_cost_per_ft3", 0.0)) * enr

        rows.append({
            "ID": idx,
            "Subbasin": _clean(item.get("subbasin", "")),
            "BMP Type": bmp,
            "Maximum BMP Units": _num(item.get("max_units_across_targets", 0.0)),
            "Implementation fraction": _num(item.get("decision_value", 0.0)),
            "Implemented BMP Units": units,
            "Total Cost (10^6 $)": total_cost / 1e6,
            "Construction Cost (10^6 $)": construction / 1e6,
            "O&M Cost (10^6 $)": om / 1e6,
            "Land Cost (10^6 $)": land / 1e6,
        })
    return pd.DataFrame(rows, columns=columns)

def subbasin_reduction_summary(
    solution: dict,
    target_definitions: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """Before/after performance for treated subbasins in solver units."""
    columns = ["ID", "Subbasin", "Parameter", "Before", "After", "Reduction", "Reduction (%)", "Unit"]
    real = _positive_placement(solution, include_no_bmp=False)
    if real.empty:
        return pd.DataFrame(columns=columns)
    definitions = _target_definition_lookup(target_definitions)
    parameters = _active_result_parameters(solution)
    treated = sorted(real["subbasin"].astype(str).unique().tolist(), key=_sub_sort_key)

    rows: list[dict[str, Any]] = []
    row_id = 1
    for sub in treated:
        for parameter in parameters:
            values = _subbasin_internal_before_after(solution, sub, parameter)
            if values is None:
                continue
            before, after = values
            reduction = before - after
            pct = reduction / before * 100.0 if before > 0 else 0.0
            rows.append({
                "ID": row_id,
                "Subbasin": sub,
                "Parameter": parameter,
                "Before": before,
                "After": after,
                "Reduction": reduction,
                "Reduction (%)": pct,
                "Unit": _internal_unit(parameter, definitions.get(parameter, {})),
            })
            row_id += 1
    return pd.DataFrame(rows, columns=columns)

def all_parameter_subbasin_reduction_summary(
    solution: dict,
    audit_matrix: pd.DataFrame | None,
    target_definitions: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """Before/after performance for all parameters present in the input data.

    The LP itself is still constrained only by user-selected positive reduction
    targets. This reporting calculation reuses the engineering audit coefficients
    to quantify incidental reductions produced by the solved BMP allocation for
    other pollutants that were present but not selected as objectives.
    """
    columns = ["ID", "Subbasin", "Parameter", "Before", "After", "Reduction", "Reduction (%)", "Unit"]
    real = _positive_placement(solution, include_no_bmp=False)
    placement = _positive_placement(solution, include_no_bmp=True)
    audit = pd.DataFrame() if audit_matrix is None else audit_matrix.copy()
    if real.empty or placement.empty or audit.empty:
        return pd.DataFrame(columns=columns)

    required = {"Subbasin", "BMP", "Target parameter", "Input value", "Effective max removal efficiency"}
    if not required.issubset(audit.columns):
        return pd.DataFrame(columns=columns)

    audit["Subbasin"] = audit["Subbasin"].astype(str)
    audit["BMP"] = audit["BMP"].astype(str)
    audit["Target parameter"] = audit["Target parameter"].astype(str)
    definitions = _target_definition_lookup(target_definitions)

    ordered_parameters = [
        _clean(definition.get("parameter", ""))
        for definition in (target_definitions or [])
        if _clean(definition.get("parameter", ""))
    ]
    available_parameters = set(audit["Target parameter"].dropna().astype(str))
    parameters = [p for p in ordered_parameters if p in available_parameters]
    if not parameters:
        parameters = [p for p in TARGET_PARAMETERS if p in available_parameters]

    treated = sorted(real["subbasin"].astype(str).unique().tolist(), key=_sub_sort_key)
    rows: list[dict[str, Any]] = []
    row_id = 1

    for sub in treated:
        psub = placement[placement["subbasin"].astype(str) == str(sub)]
        sub_audit = audit[audit["Subbasin"] == str(sub)]
        for parameter in parameters:
            param_audit = sub_audit[sub_audit["Target parameter"] == parameter]
            if param_audit.empty:
                continue

            before = _num(pd.to_numeric(param_audit.iloc[0]["Input value"], errors="coerce"))
            after = 0.0
            for _, placed in psub.iterrows():
                fraction = _num(placed.get("decision_value", 0.0))
                bmp = _clean(placed.get("bmp_name", ""))
                if bmp == "No BMP":
                    remaining = before
                else:
                    match = param_audit[param_audit["BMP"] == bmp]
                    if match.empty:
                        remaining = before
                    else:
                        effective = min(
                            1.0,
                            max(0.0, _num(match.iloc[0].get("Effective max removal efficiency", 0.0))),
                        )
                        remaining = before * (1.0 - effective)
                after += fraction * remaining

            reduction = before - after
            pct = reduction / before * 100.0 if before > 0 else 0.0
            rows.append({
                "ID": row_id,
                "Subbasin": sub,
                "Parameter": parameter,
                "Before": before,
                "After": after,
                "Reduction": reduction,
                "Reduction (%)": pct,
                "Unit": _internal_unit(parameter, definitions.get(parameter, {})),
            })
            row_id += 1

    return pd.DataFrame(rows, columns=columns)

def international_reduction_summary(
    subbasin_summary: pd.DataFrame,
    event_duration_seconds: float,
    target_definitions: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Return a scalable international-unit summary for all reported parameters.

    Peak flow is converted from cfs to m³/s. Mass pollutants in the standard
    iBMP Solver schema are converted from lb/event to 10³ kg/event. Biological
    pollutants retain their corresponding event-load units because they may be
    represented as counts or surrogate loads depending on the SWMM definition.
    """
    cols = ["Subbasin", "Parameter", "Before", "After", "Reduction (%)", "Unit"]
    if subbasin_summary is None or subbasin_summary.empty:
        return pd.DataFrame(columns=cols)

    definitions = _target_definition_lookup(target_definitions)
    seconds = max(_num(event_duration_seconds), 0.0)
    biological = {"Fecal coliform", "E. coli"}
    mass_pollutants = set(TARGET_PARAMETERS) - {"Peak Flow"} - biological
    rows: list[dict[str, Any]] = []

    for _, item in subbasin_summary.iterrows():
        parameter = _clean(item.get("Parameter", ""))
        before = _num(item.get("Before", 0.0))
        after = _num(item.get("After", 0.0))
        pct = _num(item.get("Reduction (%)", 0.0))

        if parameter == "Peak Flow":
            display_before = before * CFS_TO_CMS
            display_after = after * CFS_TO_CMS
            unit = "m³/s"
        elif parameter in mass_pollutants:
            display_before = before * seconds * LB_TO_KG / 1000.0
            display_after = after * seconds * LB_TO_KG / 1000.0
            unit = "10³ kg/event"
        else:
            # Biological inputs may represent counts or surrogate loads. Convert
            # the internal rate back to its event total without assuming mass.
            display_before = before * seconds
            display_after = after * seconds
            unit = _clean(definitions.get(parameter, {}).get("unit", "model units/event")) or "model units/event"

        rows.append({
            "Subbasin": _clean(item.get("Subbasin", "")),
            "Parameter": parameter,
            "Before": display_before,
            "After": display_after,
            "Reduction (%)": pct,
            "Unit": unit,
        })

    return pd.DataFrame(rows, columns=cols)

def watershed_performance_summary(
    solution: dict,
    targets: pd.DataFrame | None,
    target_definitions: list[dict[str, Any]] | None,
    event_duration_seconds: float,
) -> pd.DataFrame:
    """Watershed before/target/after values and required-vs-achieved reduction."""
    columns = [
        "Parameter", "Before", "Target", "After", "Required reduction (%)",
        "Achieved reduction (%)", "Meets target", "Unit",
    ]
    coef = _solved_coefficients(solution)
    placement = _positive_placement(solution, include_no_bmp=True)
    if coef.empty or placement.empty:
        return pd.DataFrame(columns=columns)
    definitions = _target_definition_lookup(target_definitions)
    parameters = _active_result_parameters(solution)
    target_df = pd.DataFrame() if targets is None else targets.copy()
    rows = []
    for parameter in parameters:
        if parameter not in coef.columns:
            continue
        no_bmp = coef[coef["BMP"] == "No BMP"].drop_duplicates("Subbasin")
        before_internal = float(pd.to_numeric(no_bmp[parameter], errors="coerce").fillna(0.0).sum())
        after_internal = 0.0
        for _, item in placement.iterrows():
            remaining = _coefficient_value(coef, str(item["subbasin"]), str(item["bmp_name"]), parameter)
            if remaining is not None:
                after_internal += _num(item["decision_value"]) * remaining

        target_row = target_df[target_df.get("Parameter", pd.Series(dtype=str)).astype(str) == parameter] if not target_df.empty and "Parameter" in target_df.columns else pd.DataFrame()
        required_pct = _num(target_row.iloc[0].get("% reduction", 0.0)) if not target_row.empty else 0.0
        target_internal = _num(target_row.iloc[0].get("Internal Target", before_internal * (1.0 - required_pct / 100.0))) if not target_row.empty else before_internal * (1.0 - required_pct / 100.0)
        achieved_pct = (before_internal - after_internal) / before_internal * 100.0 if before_internal > 0 else 0.0
        definition = definitions.get(parameter, {})
        factor = _display_factor(parameter, definition, event_duration_seconds)
        tolerance = max(abs(target_internal), 1.0) * 1e-6
        rows.append({
            "Parameter": parameter,
            "Before": before_internal * factor,
            "Target": target_internal * factor,
            "After": after_internal * factor,
            "Required reduction (%)": required_pct,
            "Achieved reduction (%)": achieved_pct,
            "Meets target": bool(after_internal <= target_internal + tolerance),
            "Unit": _display_unit(parameter, definition),
        })
    return pd.DataFrame(rows, columns=columns)

def treatment_performance_summary(
    solution: dict,
    target_definitions: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """Per-BMP inflow/outflow and effective treatment efficiency.

    Inflow = F_ij × untreated value and outflow = F_ij × remaining coefficient.
    """
    columns = ["ID", "Subbasin", "BMP Type", "Parameter", "Implementation fraction", "BMP Inflow", "BMP Outflow", "Treatment Efficiency (%)", "Unit"]
    real = _positive_placement(solution, include_no_bmp=False)
    coef = _solved_coefficients(solution)
    if real.empty or coef.empty:
        return pd.DataFrame(columns=columns)
    definitions = _target_definition_lookup(target_definitions)
    parameters = _active_result_parameters(solution)
    rows = []
    row_id = 1
    for _, item in real.sort_values("subbasin", key=lambda s: s.map(_sub_sort_key)).iterrows():
        sub = str(item["subbasin"])
        bmp = str(item["bmp_name"])
        taf = _num(item["decision_value"])
        for parameter in parameters:
            before = _coefficient_value(coef, sub, "No BMP", parameter)
            remaining = _coefficient_value(coef, sub, bmp, parameter)
            if before is None or remaining is None:
                continue
            inflow = taf * before
            outflow = taf * remaining
            efficiency = (inflow - outflow) / inflow * 100.0 if inflow > 0 else 0.0
            rows.append({
                "ID": row_id,
                "Subbasin": sub,
                "BMP Type": bmp,
                "Parameter": parameter,
                "Implementation fraction": taf,
                "BMP Inflow": inflow,
                "BMP Outflow": outflow,
                "Treatment Efficiency (%)": efficiency,
                "Unit": _internal_unit(parameter, definitions.get(parameter, {})),
            })
            row_id += 1
    return pd.DataFrame(rows, columns=columns)

def footprint_summary(solution: dict, subbasins: pd.DataFrame | None, bmp_costs: pd.DataFrame | None) -> pd.DataFrame:
    """Implemented BMP footprint by allocation."""
    columns = ["ID", "Subbasin", "BMP Type", "Subbasin Area (ac)", "Estimated BMP Footprint (ac)", "BMP Footprint as % of Subbasin Area"]
    real = _positive_placement(solution, include_no_bmp=False)
    if real.empty:
        return pd.DataFrame(columns=columns)
    subs = pd.DataFrame() if subbasins is None else subbasins.copy()
    if not subs.empty and "Sub" in subs.columns:
        subs["Sub"] = subs["Sub"].astype(str)
    costs = pd.DataFrame() if bmp_costs is None else bmp_costs.copy()
    if not costs.empty:
        costs["_bmp_key"] = costs["bmp_name"].map(_clean)
        costs = costs.drop_duplicates("_bmp_key", keep="first")
    rows = []
    for idx, (_, item) in enumerate(real.sort_values("subbasin", key=lambda s: s.map(_sub_sort_key)).iterrows(), start=1):
        sub = str(item["subbasin"]); bmp = _clean(item["bmp_name"])
        sub_area = 0.0
        if not subs.empty and "Sub" in subs.columns:
            match_sub = subs[subs["Sub"] == sub]
            if not match_sub.empty:
                sub_area = _num(match_sub.iloc[0].get("area (ac)", 0.0))
        bmp_area_ft2 = 0.0
        if not costs.empty and "_bmp_key" in costs.columns:
            match = costs[costs["_bmp_key"] == bmp]
            if not match.empty:
                bmp_area_ft2 = _num(match.iloc[0].get("area_ft2", 0.0))
        footprint_ac = _num(item.get("estimated_units", 0.0)) * bmp_area_ft2 / ACRE_TO_FT2
        rows.append({
            "ID": idx, "Subbasin": sub, "BMP Type": bmp,
            "Subbasin Area (ac)": sub_area,
            "Estimated BMP Footprint (ac)": footprint_ac,
            "BMP Footprint as % of Subbasin Area": footprint_ac / sub_area * 100.0 if sub_area > 0 else 0.0,
        })
    return pd.DataFrame(rows, columns=columns)

def cost_effectiveness_summary(solution: dict, subbasin_summary: pd.DataFrame) -> pd.DataFrame:
    """Cost-effectiveness metrics aggregated by treated subbasin."""
    columns = [
        "Subbasin", "Total BMP Cost ($)", "Peak Flow Reduction (m³/s)",
        "Peak Flow Reduction (%)", "$M per m³/s Reduced", "$K per % Peak Reduction",
        "TSS Reduction (lb/s)", "$M per lb/s TSS Reduced",
    ]
    real = _positive_placement(solution, include_no_bmp=False)
    if real.empty or subbasin_summary is None or subbasin_summary.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    treated = sorted(real["subbasin"].unique().tolist(), key=_sub_sort_key)
    for sub in treated:
        cost = float(pd.to_numeric(real.loc[real["subbasin"] == sub, "total_cost"], errors="coerce").fillna(0.0).sum())
        perf = subbasin_summary[subbasin_summary["Subbasin"] == sub]
        peak = perf[perf["Parameter"] == "Peak Flow"]
        tss = perf[perf["Parameter"] == "TSS"]
        peak_red_cms = peak_pct = tss_red = 0.0
        if not peak.empty:
            peak_red_cms = _num(peak.iloc[0]["Reduction"]) * CFS_TO_CMS
            peak_pct = _num(peak.iloc[0]["Reduction (%)"])
        if not tss.empty:
            tss_red = _num(tss.iloc[0]["Reduction"])
        rows.append({
            "Subbasin": sub,
            "Total BMP Cost ($)": cost,
            "Peak Flow Reduction (m³/s)": peak_red_cms,
            "Peak Flow Reduction (%)": peak_pct,
            "$M per m³/s Reduced": cost / peak_red_cms / 1e6 if peak_red_cms > 0 else pd.NA,
            "$K per % Peak Reduction": cost / peak_pct / 1000.0 if peak_pct > 0 else pd.NA,
            "TSS Reduction (lb/s)": tss_red,
            "$M per lb/s TSS Reduced": cost / tss_red / 1e6 if tss_red > 0 else pd.NA,
        })
    return pd.DataFrame(rows, columns=columns)

def make_subbasin_before_after_chart(subbasin_summary: pd.DataFrame, parameter: str, max_subbasins: int = 15) -> Figure:
    """Grouped before/after bars by treated subbasin for one parameter.

    The figure height scales with the number of subbasins actually displayed,
    which keeps small solutions compact while preserving readable spacing for
    larger portfolios. The caller is responsible for supplying user-facing
    units (for example pollutant event loads in lb/event rather than the
    optimizer's internal lb/s coefficients).
    """
    if subbasin_summary is None or subbasin_summary.empty:
        fig, ax = plt.subplots(figsize=(7.6, 3.0), dpi=140)
        ax.text(0.5, 0.5, "Run the solver to see treated-subbasin performance", ha="center", va="center")
        ax.axis("off")
        return fig

    data = subbasin_summary[subbasin_summary["Parameter"].astype(str) == str(parameter)].copy()
    if data.empty:
        fig, ax = plt.subplots(figsize=(7.6, 3.0), dpi=140)
        ax.text(0.5, 0.5, "No results for the selected parameter", ha="center", va="center")
        ax.axis("off")
        return fig

    data["Before"] = pd.to_numeric(data["Before"], errors="coerce").fillna(0.0)
    data["After"] = pd.to_numeric(data["After"], errors="coerce").fillna(0.0)
    data = data.sort_values("Before", ascending=False)
    truncated = len(data) > max_subbasins
    if truncated:
        data = data.head(max_subbasins)
    data = data.sort_values("Before", ascending=True)

    # Dynamic height: compact for a few treated subbasins, taller when needed.
    number_of_subbasins = len(data)
    fig_height = max(2.4, 0.34 * number_of_subbasins + 1.35)
    fig, ax = plt.subplots(figsize=(7.6, fig_height), dpi=140)

    y = list(range(number_of_subbasins))
    bar_height = 0.18
    ax.barh([i + bar_height / 2 for i in y], data["Before"], height=bar_height, label="Before")
    ax.barh([i - bar_height / 2 for i in y], data["After"], height=bar_height, label="After")
    ax.set_yticks(y, data["Subbasin"].astype(str).tolist())
    unit = _clean(data.iloc[0].get("Unit", ""))
    ax.set_xlabel(f"{parameter} ({unit})" if unit else parameter)
    suffix = f" — top {max_subbasins} by baseline" if truncated else ""
    ax.set_title(f"{parameter}: before vs after by treated subbasin{suffix}", pad=14)
    # Place the Before/After legend outside the plotting area so it never
    # overlaps the bars, even when only a few subbasins are displayed.
    ax.legend(
        frameon=False,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        ncol=1,
        borderaxespad=0.0,
    )
    ax.grid(axis="x", alpha=0.25)
    ax.margins(y=0.08)
    # Reserve space on the right for the external legend.
    fig.tight_layout(rect=(0.0, 0.0, 0.83, 1.0), pad=0.9)
    return fig

def make_cost_by_subbasin_chart(cost_effectiveness: pd.DataFrame, max_subbasins: int = 15) -> Figure:
    """Actual life-cycle cost by treated subbasin with dynamic plot height."""
    if cost_effectiveness is None or cost_effectiveness.empty:
        fig, ax = plt.subplots(figsize=(8.2, 3.0), dpi=140)
        ax.text(0.5, 0.5, "Run the solver to see cost distribution", ha="center", va="center")
        ax.axis("off")
        return fig

    data = cost_effectiveness.copy()
    data["Cost (M$)"] = pd.to_numeric(data["Total BMP Cost ($)"], errors="coerce").fillna(0.0) / 1e6
    data = data.sort_values("Cost (M$)", ascending=False)
    truncated = len(data) > max_subbasins
    if truncated:
        data = data.head(max_subbasins)
    data = data.sort_values("Cost (M$)", ascending=True)

    # Dynamic height based on the number of treated subbasins displayed.
    number_of_subbasins = len(data)
    fig_height = max(2.6, 0.34 * number_of_subbasins + 1.45)
    fig, ax = plt.subplots(figsize=(8.2, fig_height), dpi=140)

    ax.barh(data["Subbasin"].astype(str), data["Cost (M$)"], height=0.35)
    ax.set_xlabel("Actual life-cycle cost (M$)")
    ax.set_ylabel("")
    suffix = f" — top {max_subbasins}" if truncated else ""

    # Keep the internal chart title, as requested for the Results page/report.
    ax.set_title(f"BMP cost by treated subbasin{suffix}")
    ax.grid(axis="x", alpha=0.25)
    ax.margins(y=0.08)
    fig.tight_layout(pad=1.2)
    return fig

def export_solution_excel(solution: dict, path: str | Path, report_tables: dict[str, pd.DataFrame] | None = None) -> Path:
    """Export solver outputs plus optional user-facing result tables."""
    path = Path(path)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summarize_solution(solution).to_excel(writer, sheet_name="Summary", index=False)
        for sheet_name, table in (report_tables or {}).items():
            safe_name = str(sheet_name)[:31]
            (pd.DataFrame() if table is None else table).to_excel(writer, sheet_name=safe_name, index=False)
        solution.get("placement", pd.DataFrame()).to_excel(writer, sheet_name="Placement_Long", index=False)
        solution.get("performance", pd.DataFrame()).to_excel(writer, sheet_name="Performance_Raw", index=False)
        solution.get("active_constraints", pd.DataFrame()).to_excel(writer, sheet_name="Constraints", index=False)
        solution.get("allocation_caps", pd.DataFrame()).to_excel(writer, sheet_name="TAF_Caps", index=False)
        solution.get("coefficient_matrix", pd.DataFrame()).to_excel(writer, sheet_name="Coefficient_Matrix", index=False)
    return path

# =============================================================================
# PDF report generation
# =============================================================================

PAGE_SIZE = (11.0, 8.5)  # landscape US Letter


def _safe_text(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if pd.isna(value):
            return "-"
        if abs(value) >= 1_000_000:
            return f"{value:,.3g}"
        return f"{value:,.5g}"
    return str(value)


def _wrapped_lines(text: object, width: int = 92) -> list[str]:
    value = _safe_text(text)
    return wrap(value, width=max(15, width), break_long_words=False, break_on_hyphens=False) or [""]


def _summary_pages(pdf: PdfPages, summary_groups: Sequence[tuple[str, Sequence[tuple[str, object]]]]) -> None:
    """Write the report cover/scenario summary, paginating if necessary."""

    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor("white")
    y = 0.93

    def new_page(continuation: bool = False) -> tuple[Figure, float]:
        nonlocal fig
        if continuation:
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)
            fig = plt.figure(figsize=PAGE_SIZE)
            fig.patch.set_facecolor("white")
            fig.text(0.055, 0.94, "iBMP Solver Optimization Report - scenario summary", fontsize=16, weight="bold", va="top")
            return fig, 0.88
        return fig, y

    fig.text(0.055, y, "iBMP Solver Optimization Report", fontsize=22, weight="bold", va="top")
    y -= 0.055
    fig.text(
        0.055,
        y,
        "Web-based decision support tool for BMP allocation in urban watersheds",
        fontsize=10,
        color="#64748b",
        va="top",
    )
    y -= 0.06

    for group_title, entries in summary_groups:
        if y < 0.14:
            fig, y = new_page(True)
        fig.text(0.055, y, str(group_title), fontsize=14, weight="bold", color="#1f2937", va="top")
        y -= 0.035
        for label, value in entries:
            value_lines = _wrapped_lines(value, width=95)
            needed = 0.028 * max(1, len(value_lines))
            if y - needed < 0.07:
                fig, y = new_page(True)
            fig.text(0.06, y, str(label), fontsize=8.7, weight="bold", color="#334155", va="top")
            fig.text(0.30, y, "\n".join(value_lines), fontsize=8.7, color="#334155", va="top")
            y -= needed
            # light row divider
            line = plt.Line2D([0.06, 0.95], [y + 0.005, y + 0.005], transform=fig.transFigure, color="#e5e7eb", linewidth=0.5)
            fig.add_artist(line)
        y -= 0.025

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _figure_page(pdf: PdfPages, section_title: str, source_figure: Figure) -> None:
    """Place an existing result chart on a consistent landscape report page."""

    image_buffer = BytesIO()
    source_figure.savefig(image_buffer, format="png", dpi=170, bbox_inches="tight")
    image_buffer.seek(0)
    image = plt.imread(image_buffer)

    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor("white")
    fig.text(0.055, 0.94, section_title, fontsize=14, weight="bold", color="#1f2937", va="top")
    ax = fig.add_axes([0.07, 0.08, 0.86, 0.80])
    ax.imshow(image)
    ax.axis("off")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _table_pages(pdf: PdfPages, section_title: str, df: pd.DataFrame) -> None:
    """Write a dataframe across one or more readable landscape pages."""

    data = pd.DataFrame() if df is None else df.copy()
    if data.empty:
        fig = plt.figure(figsize=PAGE_SIZE)
        fig.text(0.055, 0.94, section_title, fontsize=14, weight="bold", va="top")
        fig.text(0.055, 0.86, "No data available", fontsize=10, color="#64748b", va="top")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)
        return

    # More columns require a smaller font and fewer rows per page.
    ncols = max(1, len(data.columns))
    if ncols <= 6:
        font_size, rows_per_page = 7.0, 28
    elif ncols <= 10:
        font_size, rows_per_page = 6.2, 25
    else:
        font_size, rows_per_page = 5.3, 22

    formatted = data.copy()
    for col in formatted.columns:
        formatted[col] = formatted[col].map(_safe_text)

    total = len(formatted)
    for start in range(0, total, rows_per_page):
        chunk = formatted.iloc[start : start + rows_per_page]
        fig, ax = plt.subplots(figsize=PAGE_SIZE)
        fig.patch.set_facecolor("white")
        ax.axis("off")
        suffix = "" if total <= rows_per_page else f" ({start + 1}-{min(start + rows_per_page, total)} of {total})"
        fig.text(0.055, 0.95, section_title + suffix, fontsize=14, weight="bold", color="#1f2937", va="top")

        table = ax.table(
            cellText=chunk.values.tolist(),
            colLabels=[str(c) for c in chunk.columns],
            cellLoc="left",
            colLoc="left",
            bbox=[0.01, 0.04, 0.98, 0.86],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(font_size)
        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor("#d7dee8")
            cell.set_linewidth(0.35)
            if row == 0:
                cell.set_facecolor("#eef2f7")
                cell.set_text_props(weight="bold", color="#1f2937")
            elif row % 2 == 0:
                cell.set_facecolor("#fbfcfe")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def build_optistorm_pdf_report(
    path: str | Path,
    *,
    summary_groups: Sequence[tuple[str, Sequence[tuple[str, object]]]],
    tables: Sequence[tuple[str, pd.DataFrame]],
    figures: Sequence[tuple[str, Figure]],
) -> Path:
    """Create the downloadable iBMP Solver results report.

    All inputs are already-calculated display objects. This function never
    changes or recomputes the optimization solution.
    """

    path = Path(path)
    with PdfPages(path) as pdf:
        _summary_pages(pdf, summary_groups)
        for title, figure in figures:
            _figure_page(pdf, title, figure)
        for title, table in tables:
            _table_pages(pdf, title, table)
    return path
