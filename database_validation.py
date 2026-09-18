"""Validation and session-safe normalization for database packages.

This module validates the eight CSV databases contained in the ``data/`` folder.  
Custom user packages are validated in a temporary session folder.

The validator logic is: a custom package becomes active only when
all file-level and cross-file checks pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import math
import re
import shutil

import pandas as pd

from data_processing import DATABASE_FILES, SUPPORTED_PARAMETERS, DECAY_PARAMETERS

RESERVED_BMP_NAMES = {"no bmp"}

INFILTRATION_COLUMNS = [
    "BMP_Name",
    "v_sqms",
    "gravel moisture_in",
    "gravel depth_in",
    "soil moisture",
    "soil depth_in",
    "pondind depth_in",
]

STORAGE_COLUMNS = ["BMP_Name", "Dp_mm", "SG", "v_sqms"]

COST_COLUMNS = [
    "BMP_Name",
    "Construction-Cost_usdpercf",
    "OM_Cost_usdpercf",
    "Default_area_sqf",
    "Default_depth_ft",
    "Cost_reference_City",
    "Cost_reference_Year",
]

def _clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).replace("\ufeff", "").replace("\xa0", " ").strip()

def _case_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value)).casefold()

def read_csv_flexible(path: str | Path, *, dtype: Any | None = None) -> pd.DataFrame:
    """Read CSVs using common text encodings."""
    path = Path(path)
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding, dtype=dtype)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, dtype=dtype)

def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_clean(c) for c in out.columns]
    return out

def _duplicate_names(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    dup: list[str] = []
    for value in values:
        key = _case_key(value)
        if not key:
            continue
        if key in seen and seen[key] not in dup:
            dup.append(value)
        else:
            seen[key] = value
    return dup

def _numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")

def _parse_percent_cell(value: Any) -> float | None:
    """Parse a user-facing efficiency value as percentage points (0..100).

    ``95%`` and ``95`` both mean 95%.  Numeric ``0.95`` means 0.95%, not 95%,
    because the custom database contract explicitly asks users for percentages.
    """
    text = _clean(value)
    if not text:
        return None
    try:
        if text.endswith("%"):
            return float(text[:-1].strip())
        return float(text)
    except Exception:
        return None

def _canonical_bmp_map(names: list[str]) -> dict[str, str]:
    return {_case_key(name): name for name in names}

def _safe_id(value: Any) -> str:
    text = _clean(value).lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "item"

def _id_collisions(values: list[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for value in values:
        groups.setdefault(_safe_id(value), []).append(value)
    return [" / ".join(items) for items in groups.values() if len(items) > 1]

@dataclass
class DatabaseValidationResult:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    normalized_dir: Path | None = None

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def summary_frame(self) -> pd.DataFrame:
        rows = [{"Check": key, "Value": value} for key, value in self.summary.items()]
        return pd.DataFrame(rows)

def _find_package_root(source_dir: Path) -> Path:
    """Find the folder containing all required CSVs (root or one nested data/)."""
    candidates = [source_dir, source_dir / "data"]
    for child in source_dir.iterdir() if source_dir.exists() else []:
        if child.is_dir():
            candidates.append(child)
            candidates.append(child / "data")
    for candidate in candidates:
        if candidate.exists() and all((candidate / name).exists() for name in DATABASE_FILES):
            return candidate
    return source_dir

def validate_database_package(
    source_dir: str | Path,
    *,
    normalized_dir: str | Path | None = None,
    default_data_dir: str | Path | None = None,
) -> DatabaseValidationResult:
    """Validate and optionally normalize a full OptiStorm database package."""
    result = DatabaseValidationResult()
    source_dir = _find_package_root(Path(source_dir))
    out_dir = Path(normalized_dir) if normalized_dir is not None else None

    missing = [name for name in DATABASE_FILES if not (source_dir / name).exists()]
    if missing:
        result.add_error("Database package is missing required file(s): " + ", ".join(missing))
        return result

    frames: dict[str, pd.DataFrame] = {}
    for filename in DATABASE_FILES:
        try:
            frames[filename] = _normalize_headers(read_csv_flexible(source_dir / filename, dtype=object))
        except Exception as exc:
            result.add_error(f"{filename}: could not be read as CSV ({exc}).")
    if result.errors:
        return result

    # ------------------------------------------------------------------
    # BMP_types.csv is the canonical BMP registry.
    # ------------------------------------------------------------------
    types = frames["BMP_types.csv"]
    if list(types.columns) != ["BMP Name", "Type"]:
        result.add_error("BMP_types.csv must contain exactly two columns: BMP Name, Type.")
        bmp_names: list[str] = []
        type_lookup: dict[str, str] = {}
    else:
        types = types.copy()
        types["BMP Name"] = types["BMP Name"].map(_clean)
        types["Type"] = types["Type"].map(_clean).str.title()
        if types.empty:
            result.add_error("BMP_types.csv must contain at least one BMP.")
        if (types["BMP Name"] == "").any():
            result.add_error("BMP_types.csv contains a blank BMP name.")
        duplicates = _duplicate_names(types["BMP Name"].tolist())
        if duplicates:
            result.add_error("BMP_types.csv contains duplicate BMP name(s): " + ", ".join(duplicates))
        id_collisions = _id_collisions(types["BMP Name"].tolist())
        if id_collisions:
            result.add_error("BMP_types.csv contains BMP names that would create the same internal UI id: " + "; ".join(id_collisions))
        reserved = [name for name in types["BMP Name"] if _case_key(name) in RESERVED_BMP_NAMES]
        if reserved:
            result.add_error("BMP_types.csv cannot contain the reserved internal option 'No BMP'.")
        bad_types = types.loc[~types["Type"].isin(["Storage", "Infiltration"]), "BMP Name"].tolist()
        if bad_types:
            result.add_error(
                "BMP_types.csv Type must be exactly Storage or Infiltration. Check: "
                + ", ".join(map(str, bad_types[:10]))
            )
        bmp_names = types["BMP Name"].tolist()
        canonical = _canonical_bmp_map(bmp_names)
        # Canonicalize casing/spacing consistently throughout all later files.
        types["BMP Name"] = [canonical[_case_key(name)] for name in types["BMP Name"]]
        type_lookup = dict(zip(types["BMP Name"], types["Type"]))

    canonical_bmp = _canonical_bmp_map(bmp_names)
    expected_bmp_keys = set(canonical_bmp)

    def canonicalize_bmp_columns(df: pd.DataFrame, fixed_cols: list[str], filename: str) -> tuple[pd.DataFrame, list[str]]:
        out = df.copy()
        rename: dict[str, str] = {}
        bmp_cols: list[str] = []
        for col in out.columns:
            if col in fixed_cols:
                continue
            key = _case_key(col)
            if key in RESERVED_BMP_NAMES:
                result.add_error(f"{filename}: 'No BMP' is internal and must not appear as a user BMP column.")
                continue
            if key in canonical_bmp:
                rename[col] = canonical_bmp[key]
                bmp_cols.append(canonical_bmp[key])
            else:
                bmp_cols.append(_clean(col))
        out = out.rename(columns=rename)
        return out, bmp_cols

    def check_bmp_set(found_names: list[str], filename: str) -> None:
        found_keys = {_case_key(name) for name in found_names if _clean(name)}
        missing_bmps = [canonical_bmp[k] for k in sorted(expected_bmp_keys - found_keys)]
        extra_bmps = [name for name in found_names if _case_key(name) not in expected_bmp_keys and _clean(name)]
        if missing_bmps:
            result.add_error(f"{filename}: missing BMP(s) from BMP_types.csv: " + ", ".join(missing_bmps))
        if extra_bmps:
            result.add_error(f"{filename}: BMP(s) not listed in BMP_types.csv: " + ", ".join(extra_bmps))

    # ------------------------------------------------------------------
    # ENR_CCI.csv
    # ------------------------------------------------------------------
    enr = frames["ENR_CCI.csv"].copy()
    if len(enr.columns) < 2:
        result.add_error("ENR_CCI.csv must contain Year plus at least one city column.")
        enr_years: set[int] = set()
        enr_cities: list[str] = []
    else:
        first = enr.columns[0]
        if _case_key(first) != "year":
            result.add_error("ENR_CCI.csv column A must be named Year.")
        enr = enr.rename(columns={first: "Year"})
        enr_cities = [_clean(c) for c in enr.columns[1:]]
        if any(not city for city in enr_cities):
            result.add_error("ENR_CCI.csv contains a blank city header.")
        dup_city = _duplicate_names(enr_cities)
        if dup_city:
            result.add_error("ENR_CCI.csv contains duplicate city name(s): " + ", ".join(dup_city))
        years_num = pd.to_numeric(enr["Year"], errors="coerce")
        invalid_year = years_num.isna() | ((years_num % 1) != 0)
        if invalid_year.any():
            result.add_error("ENR_CCI.csv Year must contain whole-number years only.")
        else:
            enr["Year"] = years_num.astype(int)
        if enr["Year"].duplicated().any():
            result.add_error("ENR_CCI.csv contains duplicate years.")
        enr_years = set(int(v) for v in enr["Year"].dropna())

        if default_data_dir is not None:
            try:
                default_enr = _normalize_headers(read_csv_flexible(Path(default_data_dir) / "ENR_CCI.csv", dtype=object))
                default_first = default_enr.columns[0]
                default_years = set(pd.to_numeric(default_enr[default_first], errors="coerce").dropna().astype(int).tolist())
                missing_default_years = sorted(default_years - enr_years)
                if missing_default_years:
                    result.add_error(
                        "ENR_CCI.csv cannot remove existing default year(s): "
                        + ", ".join(map(str, missing_default_years[:15]))
                    )
                if default_years:
                    default_max = max(default_years)
                    invalid_new_years = sorted(y for y in (enr_years - default_years) if y <= default_max)
                    if invalid_new_years:
                        result.add_error(
                            f"ENR_CCI.csv new years must be later than the current default maximum ({default_max}). Invalid added year(s): "
                            + ", ".join(map(str, invalid_new_years))
                        )
            except Exception as exc:
                result.add_warning(f"Could not compare ENR years against defaults: {exc}")

        for city in enr_cities:
            values = pd.to_numeric(enr[city], errors="coerce")
            bad = values.isna() | (values <= 0)
            if bad.any():
                rows = (bad[bad].index + 2).tolist()[:8]
                result.add_error(
                    f"ENR_CCI.csv: city '{city}' must contain a positive number for every year. Check row(s): "
                    + ", ".join(map(str, rows))
                )
            enr[city] = values

    # ------------------------------------------------------------------
    # Cost_Database.csv
    # ------------------------------------------------------------------
    cost = frames["Cost_Database.csv"].copy()
    if list(cost.columns) != COST_COLUMNS:
        result.add_error("Cost_Database.csv must contain exactly: " + ", ".join(COST_COLUMNS))
    else:
        cost["BMP_Name"] = cost["BMP_Name"].map(_clean)
        if any(_case_key(name) in RESERVED_BMP_NAMES for name in cost["BMP_Name"]):
            result.add_error("Cost_Database.csv cannot contain the reserved internal option 'No BMP'.")
        dup = _duplicate_names(cost["BMP_Name"].tolist())
        if dup:
            result.add_error("Cost_Database.csv contains duplicate BMP name(s): " + ", ".join(dup))
        check_bmp_set(cost["BMP_Name"].tolist(), "Cost_Database.csv")
        cost["BMP_Name"] = [canonical_bmp.get(_case_key(v), _clean(v)) for v in cost["BMP_Name"]]
        rules = {
            "Construction-Cost_usdpercf": (0.0, False),
            "OM_Cost_usdpercf": (0.0, True),
            "Default_area_sqf": (0.0, False),
            "Default_depth_ft": (0.0, False),
        }
        for col, (minimum, allow_equal) in rules.items():
            vals = _numeric_series(cost[col])
            bad = vals.isna() | (vals < minimum if allow_equal else vals <= minimum)
            if bad.any():
                op = ">= 0" if allow_equal else "> 0"
                bad_names = cost.loc[bad, "BMP_Name"].astype(str).tolist()[:8]
                result.add_error(f"Cost_Database.csv {col} must be {op}. Check: " + ", ".join(bad_names))
            cost[col] = vals
        cost["Cost_reference_City"] = cost["Cost_reference_City"].map(_clean)
        valid_city_set = set(enr_cities)
        bad_city = ~cost["Cost_reference_City"].isin(valid_city_set)
        if bad_city.any():
            pairs = [f"{r.BMP_Name}: {r.Cost_reference_City}" for r in cost.loc[bad_city].itertuples()][:8]
            result.add_error("Cost_Database.csv reference city must exactly match an ENR_CCI.csv city. Check: " + "; ".join(pairs))
        year_vals = pd.to_numeric(cost["Cost_reference_Year"], errors="coerce")
        bad_year = year_vals.isna() | ((year_vals % 1) != 0) | (~year_vals.fillna(-1).astype(int).isin(enr_years))
        if bad_year.any():
            bad_names = cost.loc[bad_year, "BMP_Name"].astype(str).tolist()[:8]
            result.add_error("Cost_Database.csv reference year must be a whole year present in ENR_CCI.csv. Check: " + ", ".join(bad_names))
        cost["Cost_reference_Year"] = year_vals.astype("Int64")

    # ------------------------------------------------------------------
    # BMP_Efficiencies.csv
    # ------------------------------------------------------------------
    eff = frames["BMP_Efficiencies.csv"].copy()
    if not len(eff.columns) or eff.columns[0] != "Parameter":
        result.add_error("BMP_Efficiencies.csv column A must be named Parameter.")
    else:
        eff, eff_bmp_cols = canonicalize_bmp_columns(eff, ["Parameter"], "BMP_Efficiencies.csv")
        eff_params = eff["Parameter"].map(_clean).tolist()
        if len(eff_params) != len(SUPPORTED_PARAMETERS) or set(eff_params) != set(SUPPORTED_PARAMETERS):
            missing_p = [p for p in SUPPORTED_PARAMETERS if p not in eff_params]
            extra_p = [p for p in eff_params if p not in SUPPORTED_PARAMETERS]
            result.add_error(
                "BMP_Efficiencies.csv Parameter names are fixed to the 15 supported optimization variables. "
                + ("Missing: " + ", ".join(missing_p) + ". " if missing_p else "")
                + ("Invalid/extra: " + ", ".join(extra_p) + "." if extra_p else "")
            )
        if len(eff_params) != len(set(eff_params)):
            result.add_error("BMP_Efficiencies.csv contains duplicate Parameter rows.")
        check_bmp_set(eff_bmp_cols, "BMP_Efficiencies.csv")
        normalized_eff = eff.copy()
        for bmp in [c for c in eff.columns if c != "Parameter"]:
            parsed = eff[bmp].map(_parse_percent_cell)
            bad = parsed.isna() | (parsed < 0) | (parsed > 100)
            if bad.any():
                params = eff.loc[bad, "Parameter"].astype(str).tolist()[:8]
                result.add_error(f"BMP_Efficiencies.csv '{bmp}' must be complete percentages from 0 to 100. Check: " + ", ".join(params))
            normalized_eff[bmp] = parsed.map(lambda x: "" if pd.isna(x) else f"{float(x):g}%")
        if set(eff_params) == set(SUPPORTED_PARAMETERS) and len(eff_params) == len(SUPPORTED_PARAMETERS):
            normalized_eff["Parameter"] = pd.Categorical(normalized_eff["Parameter"], SUPPORTED_PARAMETERS, ordered=True)
            normalized_eff = normalized_eff.sort_values("Parameter").reset_index(drop=True)
            normalized_eff["Parameter"] = normalized_eff["Parameter"].astype(str)
        eff = normalized_eff

    # ------------------------------------------------------------------
    # BMP_Cobenefits.csv
    # ------------------------------------------------------------------
    cb = frames["BMP_Cobenefits.csv"].copy()
    if len(cb.columns) < 3 or list(cb.columns[:2]) != ["Co-benefit Name", "Weight Scale"]:
        result.add_error("BMP_Cobenefits.csv must start with columns Co-benefit Name, Weight Scale, followed by BMP columns.")
    else:
        cb, cb_bmp_cols = canonicalize_bmp_columns(cb, ["Co-benefit Name", "Weight Scale"], "BMP_Cobenefits.csv")
        check_bmp_set(cb_bmp_cols, "BMP_Cobenefits.csv")
        cb["Co-benefit Name"] = cb["Co-benefit Name"].map(_clean)
        if (cb["Co-benefit Name"] == "").any():
            result.add_error("BMP_Cobenefits.csv contains a blank Co-benefit Name.")
        dup = _duplicate_names(cb["Co-benefit Name"].tolist())
        if dup:
            result.add_error("BMP_Cobenefits.csv contains duplicate co-benefit name(s): " + ", ".join(dup))
        cb_id_collisions = _id_collisions(cb["Co-benefit Name"].tolist())
        if cb_id_collisions:
            result.add_error("BMP_Cobenefits.csv contains co-benefit names that would create the same internal UI id: " + "; ".join(cb_id_collisions))
        weights = _numeric_series(cb["Weight Scale"])
        bad_w = weights.isna() | (weights < 0) | (weights > 5)
        if bad_w.any():
            names = cb.loc[bad_w, "Co-benefit Name"].astype(str).tolist()[:8]
            result.add_error("BMP_Cobenefits.csv Weight Scale must be complete numeric values from 0 to 5. Check: " + ", ".join(names))
        cb["Weight Scale"] = weights
        for bmp in [c for c in cb.columns if c not in {"Co-benefit Name", "Weight Scale"}]:
            vals = _numeric_series(cb[bmp])
            bad = vals.isna() | (vals < 0) | (vals > 5)
            if bad.any():
                names = cb.loc[bad, "Co-benefit Name"].astype(str).tolist()[:8]
                result.add_error(f"BMP_Cobenefits.csv '{bmp}' must contain a 0-5 score for every co-benefit. Check: " + ", ".join(names))
            cb[bmp] = vals

    # ------------------------------------------------------------------
    # Infiltration-based-bmp.csv
    # ------------------------------------------------------------------
    infil = frames["Infiltration-based-bmp.csv"].copy()
    if list(infil.columns) != INFILTRATION_COLUMNS:
        result.add_error("Infiltration-based-bmp.csv must contain exactly: " + ", ".join(INFILTRATION_COLUMNS))
    else:
        infil["BMP_Name"] = infil["BMP_Name"].map(_clean)
        if any(_case_key(name) in RESERVED_BMP_NAMES for name in infil["BMP_Name"]):
            result.add_error("Infiltration-based-bmp.csv cannot contain the internal 'No BMP' option.")
        dup = _duplicate_names(infil["BMP_Name"].tolist())
        if dup:
            result.add_error("Infiltration-based-bmp.csv contains duplicate BMP name(s): " + ", ".join(dup))
        infil["BMP_Name"] = [canonical_bmp.get(_case_key(v), _clean(v)) for v in infil["BMP_Name"]]
        expected_infil = {name for name, typ in type_lookup.items() if typ == "Infiltration"}
        found_infil = set(infil["BMP_Name"])
        if expected_infil != found_infil:
            missing_i = sorted(expected_infil - found_infil)
            extra_i = sorted(found_infil - expected_infil)
            if missing_i:
                result.add_error("Infiltration-based-bmp.csv missing infiltration BMP(s): " + ", ".join(missing_i))
            if extra_i:
                result.add_error("Infiltration-based-bmp.csv contains BMP(s) not classified as Infiltration: " + ", ".join(extra_i))
        numeric_cols = INFILTRATION_COLUMNS[1:]
        for col in numeric_cols:
            infil[col] = _numeric_series(infil[col])
        bad_rate = infil["v_sqms"].isna() | (infil["v_sqms"] <= 0)
        if bad_rate.any():
            result.add_error("Infiltration-based-bmp.csv v_sqms (infiltration rate) must be > 0 for every BMP. Check: " + ", ".join(infil.loc[bad_rate, "BMP_Name"].astype(str).tolist()[:8]))
        for col in ["gravel moisture_in", "soil moisture"]:
            bad = infil[col].isna() | (infil[col] < 0) | (infil[col] > 1)
            if bad.any():
                result.add_error(f"Infiltration-based-bmp.csv {col} must be between 0 and 1. Check: " + ", ".join(infil.loc[bad, "BMP_Name"].astype(str).tolist()[:8]))
        for col in ["gravel depth_in", "soil depth_in", "pondind depth_in"]:
            bad = infil[col].isna() | (infil[col] < 0)
            if bad.any():
                result.add_error(f"Infiltration-based-bmp.csv {col} must be >= 0. Check: " + ", ".join(infil.loc[bad, "BMP_Name"].astype(str).tolist()[:8]))

    # ------------------------------------------------------------------
    # Storaged-based-bmp.csv
    # ------------------------------------------------------------------
    storage = frames["Storaged-based-bmp.csv"].copy()
    if list(storage.columns) != STORAGE_COLUMNS:
        result.add_error("Storaged-based-bmp.csv must contain exactly: " + ", ".join(STORAGE_COLUMNS))
    else:
        storage["BMP_Name"] = storage["BMP_Name"].map(_clean)
        if any(_case_key(name) in RESERVED_BMP_NAMES for name in storage["BMP_Name"]):
            result.add_error("Storaged-based-bmp.csv cannot contain the internal 'No BMP' option.")
        dup = _duplicate_names(storage["BMP_Name"].tolist())
        if dup:
            result.add_error("Storaged-based-bmp.csv contains duplicate BMP name(s): " + ", ".join(dup))
        storage["BMP_Name"] = [canonical_bmp.get(_case_key(v), _clean(v)) for v in storage["BMP_Name"]]
        expected_storage = {name for name, typ in type_lookup.items() if typ == "Storage"}
        found_storage = set(storage["BMP_Name"])
        if expected_storage != found_storage:
            missing_s = sorted(expected_storage - found_storage)
            extra_s = sorted(found_storage - expected_storage)
            if missing_s:
                result.add_error("Storaged-based-bmp.csv missing storage BMP(s): " + ", ".join(missing_s))
            if extra_s:
                result.add_error("Storaged-based-bmp.csv contains BMP(s) not classified as Storage: " + ", ".join(extra_s))
        for col in STORAGE_COLUMNS[1:]:
            storage[col] = _numeric_series(storage[col])
        rules = {"Dp_mm": lambda s: s > 0, "SG": lambda s: s > 1, "v_sqms": lambda s: s > 0}
        for col, fn in rules.items():
            valid = fn(storage[col].fillna(float("-inf")))
            bad = ~valid
            if bad.any():
                req = "> 1" if col == "SG" else "> 0"
                result.add_error(f"Storaged-based-bmp.csv {col} must be {req}. Check: " + ", ".join(storage.loc[bad, "BMP_Name"].astype(str).tolist()[:8]))

    # ------------------------------------------------------------------
    # decay_rates.csv
    # ------------------------------------------------------------------
    decay = frames["decay_rates.csv"].copy()
    if not len(decay.columns) or decay.columns[0] != "Parameter":
        result.add_error("decay_rates.csv column A must be named Parameter.")
    else:
        decay, decay_bmp_cols = canonicalize_bmp_columns(decay, ["Parameter"], "decay_rates.csv")
        decay_params = decay["Parameter"].map(_clean).tolist()
        if len(decay_params) != len(DECAY_PARAMETERS) or set(decay_params) != set(DECAY_PARAMETERS):
            missing_p = [p for p in DECAY_PARAMETERS if p not in decay_params]
            extra_p = [p for p in decay_params if p not in DECAY_PARAMETERS]
            result.add_error(
                "decay_rates.csv Parameter names are fixed to the 14 pollutant variables (Peak Flow is not a decay-rate row). "
                + ("Missing: " + ", ".join(missing_p) + ". " if missing_p else "")
                + ("Invalid/extra: " + ", ".join(extra_p) + "." if extra_p else "")
            )
        if len(decay_params) != len(set(decay_params)):
            result.add_error("decay_rates.csv contains duplicate Parameter rows.")
        check_bmp_set(decay_bmp_cols, "decay_rates.csv")
        storage_bmps = {name for name, typ in type_lookup.items() if typ == "Storage"}
        for bmp in [c for c in decay.columns if c != "Parameter"]:
            vals = _numeric_series(decay[bmp])
            for idx, param in enumerate(decay["Parameter"].map(_clean)):
                value = vals.iloc[idx]
                if param == "TSS" and bmp in storage_bmps:
                    # Storage BMP TSS uses the settling equation; blank is valid.
                    if pd.notna(value) and value <= 0:
                        result.add_error(f"decay_rates.csv: {bmp} / TSS must be blank or > 0.")
                else:
                    if pd.isna(value) or value <= 0:
                        result.add_error(f"decay_rates.csv: {bmp} / {param} must contain a positive decay rate.")
            decay[bmp] = vals
        if set(decay_params) == set(DECAY_PARAMETERS) and len(decay_params) == len(DECAY_PARAMETERS):
            decay["Parameter"] = pd.Categorical(decay["Parameter"], DECAY_PARAMETERS, ordered=True)
            decay = decay.sort_values("Parameter").reset_index(drop=True)
            decay["Parameter"] = decay["Parameter"].astype(str)

    # Cross-file BMP membership is already checked for each matrix/table above.
    # Provide a concise summary even when validation fails.
    result.summary = {
        "BMPs in registry": len(bmp_names),
        "Storage BMPs": sum(1 for v in type_lookup.values() if v == "Storage"),
        "Infiltration BMPs": sum(1 for v in type_lookup.values() if v == "Infiltration"),
        "Co-benefits": len(cb) if isinstance(cb, pd.DataFrame) else 0,
        "Supported optimization parameters": len(SUPPORTED_PARAMETERS),
        "ENR years": len(enr_years),
        "ENR cities": len(enr_cities),
    }

    if result.errors:
        return result

    # Final canonical column ordering by registry makes all matrices consistent.
    registry = bmp_names
    types = types[["BMP Name", "Type"]]
    cost = cost.set_index("BMP_Name").loc[registry].reset_index()
    eff = eff[["Parameter"] + registry]
    cb = cb[["Co-benefit Name", "Weight Scale"] + registry]
    decay = decay[["Parameter"] + registry]
    infil_registry = [name for name in registry if type_lookup[name] == "Infiltration"]
    storage_registry = [name for name in registry if type_lookup[name] == "Storage"]
    infil = infil.set_index("BMP_Name").loc[infil_registry].reset_index()
    storage = storage.set_index("BMP_Name").loc[storage_registry].reset_index()

    if out_dir is not None:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "BMP_types.csv": types,
            "Cost_Database.csv": cost,
            "BMP_Efficiencies.csv": eff,
            "BMP_Cobenefits.csv": cb,
            "decay_rates.csv": decay,
            "Infiltration-based-bmp.csv": infil,
            "Storaged-based-bmp.csv": storage,
            "ENR_CCI.csv": enr,
        }
        for filename, df in outputs.items():
            df.to_csv(out_dir / filename, index=False, encoding="utf-8")
        result.normalized_dir = out_dir

    result.ok = True
    return result

