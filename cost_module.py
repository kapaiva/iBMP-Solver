"""BMP life-cycle cost preprocessing."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from data_processing import ACRE_TO_FT2, load_bmp_database_from_csv


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).replace("\ufeff", "").replace("\xa0", " ").strip()


def read_enr_database(data_dir: str | Path) -> pd.DataFrame:
    path = Path(data_dir) / "ENR_CCI.csv"
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            df = pd.read_csv(path, encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        df = pd.read_csv(path)

    if df.empty:
        raise ValueError("ENR_CCI.csv is empty.")
    df.columns = [_clean_text(c) for c in df.columns]
    df = df.rename(columns={df.columns[0]: "Year"})
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Year"]).copy()
    for col in df.columns[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def available_enr_cities(data_dir: str | Path) -> list[str]:
    return [str(c) for c in read_enr_database(data_dir).columns if c != "Year"]


def available_enr_years(data_dir: str | Path) -> list[int]:
    return sorted(int(y) for y in read_enr_database(data_dir)["Year"].dropna())


def _enr_value(enr: pd.DataFrame, city: str, year: int) -> float:
    city = _clean_text(city)
    if city not in enr.columns:
        raise ValueError(f"ENR city not found in ENR_CCI.csv: {city}")
    values = enr.loc[enr["Year"] == int(year), city]
    if values.empty:
        raise ValueError(f"ENR year not found in ENR_CCI.csv: {year}")
    value = float(values.iloc[0])
    if not pd.notna(value) or value <= 0:
        raise ValueError(f"ENR index must be greater than zero for {city}, {year}.")
    return value


def present_value_factor(interest_rate_pct: float, life_cycle_years: float) -> float:
    """Present-value factor for a uniform annual O&M cost."""
    n = max(float(life_cycle_years or 0.0), 0.0)
    if n <= 0:
        return 0.0
    i = max(float(interest_rate_pct or 0.0), 0.0) / 100.0
    return n if i == 0 else ((1.0 + i) ** n - 1.0) / (i * (1.0 + i) ** n)


def calculate_bmp_unit_cost_table(
    data_dir: str | Path,
    city: str,
    year: int,
    life_cycle_years: float,
    interest_rate_pct: float,
    land_cost_per_acre: float,
) -> pd.DataFrame:
    """Calculate BMP-specific ENR-adjusted life-cycle unit costs."""
    data_dir = Path(data_dir)
    bmps = load_bmp_database_from_csv(data_dir).copy()
    required = {"cost_reference_city", "cost_reference_year"}
    if not required.issubset(bmps.columns):
        raise ValueError("Cost_Database.csv must include Cost_reference_City and Cost_reference_Year.")

    selected_city, selected_year = _clean_text(city), int(year)
    enr = read_enr_database(data_dir)
    selected_index = _enr_value(enr, selected_city, selected_year)

    ref_city = bmps["cost_reference_city"].map(_clean_text)
    ref_year = pd.to_numeric(bmps["cost_reference_year"], errors="coerce")
    invalid = ref_city.eq("") | ref_year.isna()
    if invalid.any():
        bad = bmps.loc[invalid, "bmp_name"].astype(str).tolist()[:10]
        raise ValueError("Every BMP must have a valid cost reference city and year. Check: " + ", ".join(bad))
    ref_year = ref_year.astype(int)
    reference_index = pd.Series(
        [_enr_value(enr, c, y) for c, y in zip(ref_city, ref_year)],
        index=bmps.index,
        dtype="float64",
    )

    pv = present_value_factor(interest_rate_pct, life_cycle_years)
    land = float(land_cost_per_acre or 0.0) / ACRE_TO_FT2
    construction = pd.to_numeric(bmps["construction_cost_per_ft3"], errors="coerce").fillna(0.0)
    om_annual = pd.to_numeric(bmps["om_cost_per_ft3"], errors="coerce").fillna(0.0)
    coefficient = selected_index / reference_index

    bmps["cost_reference_city"] = ref_city
    bmps["cost_reference_year"] = ref_year
    bmps["reference_enr_index"] = reference_index
    bmps["cost_coefficient_enr"] = coefficient
    bmps["land_cost_per_ft3"] = land
    bmps["om_present_cost_per_ft3"] = om_annual * pv
    bmps["unit_cost_per_ft3"] = (construction + bmps["om_present_cost_per_ft3"] + land) * coefficient
    bmps["unit_volume_ft3"] = bmps["area_ft2"] * bmps["depth_ft"]
    bmps["unit_total_cost"] = bmps["unit_cost_per_ft3"] * bmps["unit_volume_ft3"]
    return bmps
