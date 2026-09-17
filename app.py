"""iBMP Solver Shiny application.

Page composition lives in ``ui_pages.py`` and reusable UI builders in
``ui_helpers.py``. Data parsing and model calculations live in their domain
modules; this file owns the Shiny reactive workflow and server wiring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import shutil
import tempfile
import zipfile

import matplotlib.pyplot as plt
import pandas as pd
from shiny import App, reactive, render, ui

from ui_pages import app_ui
from ui_helpers import NAV_SCROLL_ONCLICK, cobenefit_score_table_ui
from data_processing import (
    DATABASE_FILES,
    _clean_uploaded_header_name,
    _safe_uploaded_path,
    active_target_definitions,
    calculate_bmp_cobenefit_scores,
    calculate_target_table,
    cobenefit_defaults_from_database,
    cobenefit_score_input_id,
    cobenefit_weight_input_id,
    combine_rpt_and_inp,
    display_subbasins_for_preview,
    empty_subbasin_template,
    load_bmp_types_table,
    normalize_bmp_dst_columns,
    parse_swmm_inp,
    parse_swmm_rpt,
    target_input_id,
    validate_manual_subbasin_csv,
)
from cost_module import available_enr_cities, available_enr_years, calculate_bmp_unit_cost_table
from database_validation import validate_database_package
from optimizer import OptimizationConfig, solve_bmp_placement_from_coefficients
from solver_matrix import build_constraint_matrix_from_audit, build_optimization_data, build_solver_matrix_audit
from results import (
    allocation_cost_summary,
    all_parameter_subbasin_reduction_summary,
    build_optistorm_pdf_report,
    cost_effectiveness_summary,
    export_solution_excel,
    footprint_summary,
    international_reduction_summary,
    make_cost_by_subbasin_chart,
    make_subbasin_before_after_chart,
    round_numeric_columns,
    subbasin_reduction_summary,
    treatment_performance_summary,
    watershed_performance_summary,
)

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"


def server(input: Any, output: Any, session: Any) -> None:
    """Server logic for the Shiny application."""

    # =========================================================================
    # WORKFLOW NAVIGATION + LOCKED STEP SIDEBAR
    # =========================================================================
    # ``current_workflow_page`` controls the highlighted sidebar item.
    # ``max_unlocked_step`` records how far the user has successfully progressed.
    # Users can jump backward to any unlocked page without losing their entries,
    # while future pages remain disabled until their prerequisites are completed.
    current_workflow_page = reactive.value("welcome")
    # Keep the first landing screen uncluttered. After Start is selected, the
    # Welcome page becomes a normal workflow destination in the left sidebar.
    workflow_started = reactive.value(False)
    max_unlocked_step = reactive.value(0)

    # Results page access is tracked separately from general workflow
    # This flag is reset at the start of every
    # optimization run and enabled only after the latest solve is Optimal.
    results_unlocked = reactive.value(False)

    # =========================================================================
    # ACTIVE DATABASE SOURCE — DEFAULT OR SESSION-SPECIFIC CUSTOM PACKAGE
    # =========================================================================
    # The bundled data/ directory is immutable during a user session. A custom
    # ZIP is extracted, validated, normalized, and activated in a temporary
    # session folder only after all checks pass.
    session_database_temp = tempfile.TemporaryDirectory(prefix="optistorm_db_session_")
    session_database_root = Path(session_database_temp.name)
    active_database_dir_value = reactive.value(str(DATA_DIR))
    active_database_source = reactive.value("Default databases")
    # The database files are available internally from startup, but the user
    # must explicitly choose defaults or successfully activate a custom package
    # before Step 1 is unlocked. This makes the optional setup choice clear.
    database_setup_ready = reactive.value(False)
    database_validation_state = reactive.value("not_selected")
    database_validation_messages = reactive.value([])
    database_validation_warnings = reactive.value([])
    database_validation_summary = reactive.value(pd.DataFrame())

    @reactive.calc
    def active_data_dir() -> Path:
        """Return the database folder used by all downstream calculations."""
        # Keep the TemporaryDirectory object alive for the lifetime of this
        # session; it cleans itself up when the session closures are released.
        _ = session_database_temp
        return Path(str(active_database_dir_value()))

    def _unlock_through(step_number: int) -> None:
        max_unlocked_step.set(max(int(max_unlocked_step() or 0), int(step_number)))

    def _navigate(page: str) -> None:
        current_workflow_page.set(page)
        ui.update_navset("optistorm_workflow", selected=page, session=session)

    @output
    @render.ui
    def workflow_sidebar() -> Any:
        """Left navigation for the complete iBMP Solver workflow.

        The initial landing screen remains uncluttered until Start is selected.
        After that point, Welcome is retained as the first workflow destination,
        followed by Initial Setup and numbered Steps 1–6. Step 1 stays locked
        until defaults are selected or a custom database package passes validation.
        """

        current = str(current_workflow_page() or "welcome")
        if not bool(workflow_started()):
            return ui.div()

        unlocked = int(max_unlocked_step() or 0)
        steps = [
            (1, "input_data", "Input Data"),
            (2, "bmp_preferences", "BMP Preferences"),
            (3, "scenario_targets", "Scenario Targets"),
            (4, "cost_objective", "Cost & Objective"),
            (5, "solver_review", "Run Optimization"),
            (6, "results", "Results"),
        ]

        controls: list[Any] = [ui.div("iBMP Solver workflow", class_="workflow-sidebar-title")]

        # Welcome remains available after the workflow has started so users can
        # return to the introductory page without resetting their scenario.
        controls.append(
            ui.input_action_button(
                "sidebar_welcome",
                "Welcome",
                onclick=NAV_SCROLL_ONCLICK,
                class_="sidebar-current" if current == "welcome" else "",
            )
        )

        # Initial Setup is always available so users can return later to switch
        # between default and validated custom databases.
        controls.append(
            ui.input_action_button(
                "sidebar_database_setup",
                "Initial setup",
                onclick=NAV_SCROLL_ONCLICK,
                class_="sidebar-current" if current == "database_setup" else "",
            )
        )

        for number, page, label in steps:
            button = ui.input_action_button(
                f"sidebar_step_{number}",
                f"{number}. {label}",
                onclick=NAV_SCROLL_ONCLICK,
                class_="sidebar-current" if page == current else "",
            )

            # Step 6 has a stricter rule than the other pages: it is available
            # only when the latest solver run is Optimal. ``max_unlocked_step``
            # alone is intentionally not enough, because a prior optimal solve
            # may have unlocked Results before a later infeasible run.
            step_is_locked = number > unlocked
            if number == 6 and not bool(results_unlocked()):
                step_is_locked = True

            if step_is_locked:
                button = ui.tags.fieldset({"disabled": "disabled"}, button)
            controls.append(button)
        return ui.div(*controls, class_="workflow-sidebar")

    @reactive.effect
    @reactive.event(input.start_optistorm)
    def _start_optistorm() -> None:
        # Start enters the full workflow. The sidebar is visible immediately,
        # with Step 1 still locked until Initial Setup is selected/validated.
        workflow_started.set(True)
        _navigate("database_setup")

    @reactive.effect
    @reactive.event(input.sidebar_welcome)
    def _sidebar_welcome() -> None:
        _navigate("welcome")

    @reactive.effect
    @reactive.event(input.sidebar_database_setup)
    def _sidebar_database_setup() -> None:
        _navigate("database_setup")

    @reactive.effect
    @reactive.event(input.back_to_database_setup_from_input)
    def _back_to_database_setup_from_input() -> None:
        _navigate("database_setup")

    @reactive.effect
    @reactive.event(input.next_to_bmp_preferences)
    def _next_to_bmp_preferences() -> None:
        if subbasins_display().empty:
            step1_process_state.set("error")
            step1_process_message.set(
                "Process a valid watershed dataset before continuing to BMP preferences."
            )
            return
        _unlock_through(2)
        _navigate("bmp_preferences")

    @reactive.effect
    @reactive.event(input.back_to_input_data)
    def _back_to_input_data() -> None:
        _navigate("input_data")

    @reactive.effect
    @reactive.event(input.next_to_scenario_targets)
    def _next_to_scenario_targets() -> None:
        _unlock_through(3)
        _navigate("scenario_targets")

    @reactive.effect
    @reactive.event(input.back_to_bmp_preferences_from_targets)
    def _back_to_bmp_preferences_from_targets() -> None:
        _navigate("bmp_preferences")

    @reactive.effect
    @reactive.event(input.next_to_cost_objective)
    def _next_to_cost_objective() -> None:
        if event_duration_seconds() <= 0:
            ui.notification_show("Enter a positive runoff/event loading duration before continuing.", type="error")
            return
        if active_storm_duration_hours() <= 0:
            ui.notification_show("Enter a positive storm duration before continuing.", type="error")
            return
        if target_criteria().empty:
            ui.notification_show("Scenario targets could not be calculated from the current inputs.", type="error")
            return
        _unlock_through(4)
        _navigate("cost_objective")

    @reactive.effect
    @reactive.event(input.back_to_scenario_targets)
    def _back_to_scenario_targets() -> None:
        _navigate("scenario_targets")

    @reactive.effect
    @reactive.event(input.next_to_solver_review)
    def _next_to_solver_review() -> None:
        if not cost_inputs_are_processed():
            ui.notification_show("Process the current cost assumptions before continuing.", type="error")
            return
        if input.objective() == "Cost + Co-benefits":
            weights = cobenefit_weight_values()
            if not weights or sum(max(float(v or 0.0), 0.0) for v in weights.values()) <= 0:
                ui.notification_show(
                    "At least one co-benefit importance weight must be greater than zero.",
                    type="error",
                )
                return
        _unlock_through(5)
        _navigate("solver_review")

    @reactive.effect
    @reactive.event(input.back_to_cost_objective)
    def _back_to_cost_objective() -> None:
        _navigate("cost_objective")

    @reactive.effect
    @reactive.event(input.check_results)
    def _check_results() -> None:
        sol = solution()
        status = str(sol.get("status", "Not run yet") or "Not run yet")
        if status != "Optimal" or not bool(results_unlocked()):
            ui.notification_show(
                "Results are available only for the latest Optimal solution.",
                type="error",
            )
            return
        _unlock_through(6)
        _navigate("results")

    @reactive.effect
    @reactive.event(input.back_to_solver_review)
    def _back_to_solver_review() -> None:
        _navigate("solver_review")

    # Sidebar jump actions. Locked buttons are disabled in the UI; these checks
    # are an additional server-side guard.
    def _sidebar_jump(step: int, page: str) -> None:
        # Server-side guard mirrors the disabled sidebar UI. In particular,
        # Step 6 cannot be opened from a stale earlier solution.
        if step == 6 and not bool(results_unlocked()):
            ui.notification_show(
                "Results are available only for the latest Optimal solution.",
                type="warning",
            )
            return
        if int(max_unlocked_step() or 0) >= step:
            _navigate(page)

    @reactive.effect
    @reactive.event(input.sidebar_step_1)
    def _sidebar_step_1() -> None:
        _sidebar_jump(1, "input_data")

    @reactive.effect
    @reactive.event(input.sidebar_step_2)
    def _sidebar_step_2() -> None:
        _sidebar_jump(2, "bmp_preferences")

    @reactive.effect
    @reactive.event(input.sidebar_step_3)
    def _sidebar_step_3() -> None:
        _sidebar_jump(3, "scenario_targets")

    @reactive.effect
    @reactive.event(input.sidebar_step_4)
    def _sidebar_step_4() -> None:
        _sidebar_jump(4, "cost_objective")

    @reactive.effect
    @reactive.event(input.sidebar_step_5)
    def _sidebar_step_5() -> None:
        _sidebar_jump(5, "solver_review")

    @reactive.effect
    @reactive.event(input.sidebar_step_6)
    def _sidebar_step_6() -> None:
        _sidebar_jump(6, "results")

    # -------------------------------------------------------------------------
    # Optional database setup (before Step 1)
    # -------------------------------------------------------------------------
    def _reset_after_database_change() -> None:
        """Invalidate state that depends on BMP/database definitions."""
        try:
            pair_bmp_exclusions.set({})
            subbasin_taf_cap_overrides.set({})
            processed_cost_signature.set(None)
            cost_process_state.set("idle")
            solver_solution.set(None)
            results_unlocked.set(False)
            if int(max_unlocked_step() or 0) > 4:
                max_unlocked_step.set(4)
        except Exception:
            # During initial server setup some state objects are declared later.
            # They will start in their normal empty state before a user can click.
            pass

    def _safe_extract_database_zip(zip_path: str | Path, destination: Path) -> None:
        """Extract an uploaded ZIP without allowing path traversal."""
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            if len(members) > 100:
                raise ValueError("Database ZIP contains too many files.")
            root = destination.resolve()
            for member in members:
                member_path = (destination / member.filename).resolve()
                if root not in member_path.parents and member_path != root:
                    raise ValueError("Database ZIP contains an unsafe file path.")
            archive.extractall(destination)

    @reactive.effect
    @reactive.event(input.back_from_database_setup)
    def _back_from_database_setup() -> None:
        _navigate("welcome")

    @reactive.effect
    @reactive.event(input.continue_from_database_setup)
    def _continue_from_database_setup() -> None:
        if not bool(database_setup_ready()):
            ui.notification_show(
                "Choose Use default databases or upload a customized database package before continuing.",
                type="error",
            )
            return
        _unlock_through(1)
        _navigate("input_data")

    @reactive.effect
    @reactive.event(input.use_default_databases)
    def _use_default_databases() -> None:
        # Restore/select the immutable packaged defaults. This is the normal
        # path and requires no validation upload from the user.
        active_database_dir_value.set(str(DATA_DIR))
        active_database_source.set("Default databases")
        database_setup_ready.set(True)
        database_validation_state.set("default")
        database_validation_messages.set([])
        database_validation_warnings.set([])
        database_validation_summary.set(pd.DataFrame())
        _reset_after_database_change()
        ui.notification_show("Default databases selected.", type="message")

    @reactive.effect
    @reactive.event(input.validate_custom_databases)
    def _validate_custom_databases() -> None:
        upload = input.custom_database_zip()
        zip_path = _safe_uploaded_path(upload)
        if not zip_path:
            database_validation_state.set("error")
            database_validation_messages.set(["Upload a .zip database package before validation."])
            database_validation_warnings.set([])
            database_validation_summary.set(pd.DataFrame())
            return

        validation_run = int(input.validate_custom_databases() or 0)
        extract_dir = session_database_root / f"uploaded_package_{validation_run}"
        normalized_dir = session_database_root / f"active_custom_data_{validation_run}"
        database_validation_state.set("checking")
        database_validation_messages.set([])
        database_validation_warnings.set([])

        try:
            with ui.Progress(min=0, max=1) as progress:
                progress.set(0.15, message="Validating database package", detail="Extracting uploaded ZIP...")
                _safe_extract_database_zip(zip_path, extract_dir)
                progress.set(0.45, detail="Checking file structure and numeric ranges...")
                validation = validate_database_package(
                    extract_dir,
                    normalized_dir=normalized_dir,
                    default_data_dir=DATA_DIR,
                )
                progress.set(0.85, detail="Checking BMP consistency across databases...")
                database_validation_summary.set(validation.summary_frame())
                database_validation_warnings.set(list(validation.warnings))

                if not validation.ok or validation.normalized_dir is None:
                    database_validation_state.set("error")
                    database_validation_messages.set(list(validation.errors))
                    progress.set(1.0, detail="Database package needs corrections.")
                    return

                # Activate only after every check has passed. The packaged data/
                # directory is not edited or overwritten.
                active_database_dir_value.set(str(validation.normalized_dir))
                active_database_source.set("Validated custom package")
                database_setup_ready.set(True)
                database_validation_state.set("success")
                database_validation_messages.set([])
                _reset_after_database_change()
                progress.set(1.0, detail="Custom databases activated for this session.")
        except Exception as exc:
            database_validation_state.set("error")
            database_validation_messages.set([f"Database package could not be validated: {exc}"])
            database_validation_warnings.set([])
            database_validation_summary.set(pd.DataFrame())

    @output
    @render.ui
    def database_validation_status() -> Any:
        state = str(database_validation_state() or "not_selected")
        source = str(active_database_source() or "Default databases")
        if state == "checking":
            return ui.div("Checking database package...", class_="alert alert-info")
        if state == "success":
            return ui.div(
                f"Customized databases successfully validated and activated for this session. Active source: {source}.",
                class_="alert alert-success",
            )
        if state == "error":
            if bool(database_setup_ready()):
                return ui.div(
                    f"The uploaded custom package was not activated. The previous valid source remains active: {source}.",
                    class_="alert alert-danger",
                )
            return ui.div(
                "Error: Correct the validation errors or select Use default databases.",
                class_="alert alert-danger",
            )
        if state == "default" and bool(database_setup_ready()):
            return ui.div(
                "Default databases selected. You can continue to Input Data",
                class_="alert alert-success",
            )
        return ui.div(
            "Choose a database source before continuing. For most users, select Use default databases.",
            class_="alert alert-info",
        )

    @output
    @render.ui
    def database_validation_details() -> Any:
        errors = list(database_validation_messages() or [])
        warnings = list(database_validation_warnings() or [])
        summary = database_validation_summary()
        blocks: list[Any] = []
        if errors:
            blocks.append(
                ui.div(
                    ui.div("Validation errors", class_="section-title"),
                    ui.tags.ul(*[ui.tags.li(message) for message in errors]),
                    class_="clean-card",
                )
            )
        if warnings:
            blocks.append(
                ui.div(
                    ui.div("Validation warnings", class_="section-title"),
                    ui.tags.ul(*[ui.tags.li(message) for message in warnings]),
                    class_="clean-card",
                )
            )
        if summary is not None and not summary.empty:
            blocks.append(
                ui.div(
                    ui.div("Validated database summary", class_="section-title"),
                    ui.output_data_frame("database_validation_summary_table"),
                    class_="clean-card",
                )
            )
        return ui.div(*blocks)

    @output
    @render.data_frame
    def database_validation_summary_table() -> render.DataGrid:
        return render.DataGrid(database_validation_summary(), filters=False, height="300px")

    @output
    @render.download(filename="database.zip")
    def download_database_package():
        """Download a clean editable copy of the packaged default databases."""
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "database.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename in DATABASE_FILES:
                    path = DATA_DIR / filename
                    if path.exists():
                        archive.write(path, arcname=filename)
                guide_path = APP_DIR / "DATABASE_GUIDE.md"
                if guide_path.exists():
                    archive.write(guide_path, arcname="DATABASE_GUIDE.md")
            yield archive_path.read_bytes()

    # Step 1. The table should be built from one source:
    # either SWMM files or a manually prepared CSV. This keeps manual and SWMM inputs EXCLUSIVE.
    # STEP 1 STATE — manual CSV is the default visible path. Uploaded files
    # do not become active optimization data until the user clicks Process data.
    step1_input_source = reactive.value("manual")
    processed_subbasin_data = reactive.value(empty_subbasin_template(0))
    step1_process_state = reactive.value("idle")
    step1_process_message = reactive.value(
        "Upload a completed CSV or switch to SWMM, then select Process data."
    )
    about_optistorm_visible = reactive.value(False)

    @reactive.effect
    @reactive.event(input.toggle_about_optistorm)
    def _toggle_about_optistorm() -> None:
        about_optistorm_visible.set(not bool(about_optistorm_visible()))

    @output
    @render.ui
    def about_optistorm_panel() -> Any:
        if not about_optistorm_visible():
            return ui.div()
        return ui.div(
            ui.p(
                "iBMP is a planning-level optimization tool for identifying cost-effective BMP allocations while allowing users to define watershed targets, spatial restrictions, implementation limits, and optional co-benefit priorities."
            ),
            class_="source-box",
        )

    # Explicit BMP–subbasin exclusions entered in Step 2. The mapping is
    # stored as {subbasin: tuple(excluded BMP names)}. Keeping only exceptions
    # makes the default state compact: an empty dictionary means every globally
    # enabled BMP is allowed in every eligible subbasin.
    pair_bmp_exclusions = reactive.value({})

    # Optional per-subbasin overrides for the real-BMP TAF cap. An empty
    # dictionary means every subbasin uses the Step 2 default cap unless overridden.
    subbasin_taf_cap_overrides = reactive.value({})

    # STEP 4 cost-processing checkpoint. The saved signature records the exact
    # cost assumptions the user processed. If any cost input later changes, the
    # signature no longer matches and Step 4 requires processing again before the
    # objective/solver workflow can continue.
    processed_cost_signature = reactive.value(None)
    cost_process_state = reactive.value("idle")
    cost_process_message = reactive.value(
        "Complete the cost assumptions and select Process cost assumptions."
    )

    @reactive.effect
    @reactive.event(input.use_swmm_inputs)
    def _activate_swmm_inputs() -> None:
        """Use uploaded SWMM .rpt/.inp files as the active Step 1 source."""

        step1_input_source.set("swmm")
        processed_subbasin_data.set(empty_subbasin_template(0))
        step1_process_state.set("idle")
        step1_process_message.set("Upload both SWMM files, then select Process data.")

    @reactive.effect
    @reactive.event(input.use_manual_inputs)
    def _activate_manual_inputs() -> None:
        """Use an uploaded manual CSV as the active Step 1 source."""

        step1_input_source.set("manual")
        processed_subbasin_data.set(empty_subbasin_template(0))
        step1_process_state.set("idle")
        step1_process_message.set("Upload the completed CSV template, then select Process data.")

    @output
    @render.ui
    def step1_active_input_box() -> Any:
        """STEP 1 upload controls for the currently selected data source."""

        if step1_input_source() == "swmm":
            return ui.div(
                ui.h5("Parse data from SWMM"),
                ui.p("SWMM files must use U.S. customary units with flow units set to CFS. The parser expects subcatchment area in acres, runoff depth in inches, runoff volume in 10⁶ gal, and flow in cfs."
                     ),
                ui.input_file(
                    "rpt_file",
                    "SWMM report file (.rpt)",
                    accept=[".rpt", ".txt"],
                ),
                ui.input_file(
                    "inp_file",
                    "SWMM input file (.inp)",
                    accept=[".inp", ".txt"],
                ),
                ui.div(
                    "Prefer the formatted CSV instead? ",
                    ui.input_action_button(
                        "use_manual_inputs",
                        "Use CSV input",
                        class_="text-link-btn",
                    ),
                ),
                class_="source-box",
            )

        return ui.div(
            ui.h5("Upload subbasin input data (.csv)"),
            ui.input_file(
                "manual_csv",
                "", #empty label
                accept=[".csv"],
            ),
            class_="source-box",
        )

    @reactive.calc
    def parsed_inp() -> tuple[pd.DataFrame, dict[str, Any]]:
        """Reactive parser for uploaded SWMM .inp file.

        The parser runs only while SWMM mode is active.
        """

        if step1_input_source() != "swmm":
            return pd.DataFrame(), {}

        path = _safe_uploaded_path(input.inp_file())
        if not path:
            return pd.DataFrame(), {}
        return parse_swmm_inp(path)

    @reactive.calc
    def parsed_rpt() -> tuple[pd.DataFrame, dict[str, Any]]:
        """Reactive parser for uploaded SWMM .rpt file.

        The parser runs only while SWMM mode is active.
        """

        if step1_input_source() != "swmm":
            return pd.DataFrame(), {}

        path = _safe_uploaded_path(input.rpt_file())
        if not path:
            return pd.DataFrame(), {}
        return parse_swmm_rpt(path)

    @reactive.calc
    def manual_csv_validation() -> tuple[pd.DataFrame, str]:
        """Read and validate the manually uploaded subbasin CSV.

        Returns an empty DataFrame and a red-message string when the uploaded
        file does not match the required subbasin input template. This prevents random
        CSV files or misspelled headers from becoming optimization inputs.
        """

        if step1_input_source() != "manual":
            return empty_subbasin_template(0), ""

        manual_path = _safe_uploaded_path(input.manual_csv())
        if not manual_path:
            return empty_subbasin_template(0), ""

        try:
            # Keep the raw headers first so we can validate them before any
            # standardization or numeric conversion occurs.
            manual_df = pd.read_csv(manual_path)
        except Exception as exc:  # pragma: no cover - user-facing safety net
            return empty_subbasin_template(0), f"Manual CSV upload failed. The file could not be read as a CSV: {exc}"

        # Clean only invisible/accidental characters in headers. Do not rename
        # or guess columns here; the manual template is intentionally strict.
        manual_df.columns = [_clean_uploaded_header_name(col) for col in manual_df.columns]

        is_valid, message = validate_manual_subbasin_csv(manual_df)
        if not is_valid:
            return empty_subbasin_template(0), message

        return normalize_bmp_dst_columns(manual_df), ""

    @reactive.effect
    @reactive.event(input.process_step1_inputs)
    def _process_step1_inputs() -> None:
        """STEP 1 validate staged files with visible processing feedback.

        The progress indicator is UI-only. Once validation succeeds, the exact
        same normalized subbasin table is passed to downstream calculations.
        """

        with ui.Progress(min=0, max=1) as progress:
            progress.set(0.10, message="Processing watershed data", detail="Checking uploaded files...")

            if step1_input_source() == "manual":
                progress.set(0.35, detail="Validating CSV headers and values...")
                manual_df, manual_error = manual_csv_validation()
                if manual_error:
                    processed_subbasin_data.set(empty_subbasin_template(0))
                    step1_process_state.set("error")
                    step1_process_message.set(manual_error)
                    return
                if manual_df.empty:
                    processed_subbasin_data.set(empty_subbasin_template(0))
                    step1_process_state.set("error")
                    step1_process_message.set(
                        "No CSV has been processed. Upload the completed sample_subbasins.csv file first."
                    )
                    return
                progress.set(0.82, detail="Preparing the subbasin input table...")
                processed_subbasin_data.set(manual_df.copy())
                step1_process_state.set("success")
                step1_process_message.set(f"Successfully loaded {len(manual_df)} subbasins.")
                progress.set(1.0, detail="Data ready.")
                return

            # SWMM mode intentionally requires both files in this guided workflow.
            rpt_path = _safe_uploaded_path(input.rpt_file())
            inp_path = _safe_uploaded_path(input.inp_file())
            if not rpt_path or not inp_path:
                processed_subbasin_data.set(empty_subbasin_template(0))
                step1_process_state.set("error")
                step1_process_message.set(
                    "SWMM processing requires both a valid .rpt file and its corresponding .inp file."
                )
                return

            progress.set(0.30, detail="Parsing SWMM runoff, washoff, and watershed characteristics...")
            rpt_df, _ = parsed_rpt()
            inp_df, _ = parsed_inp()
            if rpt_df.empty or inp_df.empty:
                processed_subbasin_data.set(empty_subbasin_template(0))
                step1_process_state.set("error")
                step1_process_message.set(
                    "SWMM import failed because the required subbasin information could not be parsed from the uploaded .rpt and .inp files."
                )
                return

            progress.set(0.62, detail="Matching SWMM subbasins and building the input table...")
            combined = combine_rpt_and_inp(rpt_df, inp_df)
            if combined.empty:
                processed_subbasin_data.set(empty_subbasin_template(0))
                step1_process_state.set("error")
                step1_process_message.set("SWMM import failed because no matching subbasins were found.")
                return

            # Area is a fundamental sizing input; pollutant columns may legitimately
            # contain zeros when a constituent is not part of the user's scenario.
            area_values = pd.to_numeric(combined.get("area (ac)", 0.0), errors="coerce").fillna(0.0)
            if (area_values <= 0).any():
                processed_subbasin_data.set(empty_subbasin_template(0))
                step1_process_state.set("error")
                step1_process_message.set(
                    "SWMM files were parsed, but one or more subbasins have missing or zero area. Check the .inp [SUBCATCHMENTS] data before continuing."
                )
                return

            progress.set(0.88, detail="Finalizing watershed inputs...")
            processed_subbasin_data.set(combined.copy())
            step1_process_state.set("success")
            step1_process_message.set(f"Successfully loaded {len(combined)} subbasins from SWMM.")
            progress.set(1.0, detail="Data ready.")

    @reactive.calc
    def subbasins_display() -> pd.DataFrame:
        """Return only the dataset explicitly activated by Process data."""

        current = processed_subbasin_data()
        if current is None or current.empty:
            return empty_subbasin_template(0)
        return current.copy()

    @reactive.calc
    def bmp_types_table_data() -> pd.DataFrame:
        """BMP names and sizing-family classifications used in Step 2."""

        return load_bmp_types_table(active_data_dir())

    @reactive.calc
    def active_cobenefit_defaults() -> tuple[dict[str, float], dict[str, dict[str, float]], list[str]]:
        """Load co-benefit rows/BMP columns from the active database source."""

        return cobenefit_defaults_from_database(active_data_dir())

    @reactive.calc
    def active_cobenefit_criteria() -> list[str]:
        weights, _, _ = active_cobenefit_defaults()
        return list(weights.keys())

    @output
    @render.ui
    def cost_database_selectors() -> Any:
        """Render ENR city/year choices from the active database package."""

        try:
            cities = available_enr_cities(active_data_dir())
            years = [str(year) for year in available_enr_years(active_data_dir())]
        except Exception as exc:
            return ui.div(f"Could not load ENR choices: {exc}", class_="alert alert-danger")

        if not cities or not years:
            return ui.div("The active ENR database does not contain usable city/year choices.", class_="alert alert-danger")

        # Preserve familiar defaults when present; otherwise use the first/most
        # recent valid choice from the active custom database.
        selected_city = "Denver, CO" if "Denver, CO" in cities else cities[0]
        selected_year = str(max(int(y) for y in years))
        return ui.div(
            ui.input_select("cost_city", "Selected ENR city", choices=cities, selected=selected_city, width="100%"),
            ui.input_select("cost_year", "Selected ENR year", choices=years, selected=selected_year, width="100%"),
        )

    @reactive.calc
    def excluded_bmp_names() -> list[str]:
        """STEP 2 global BMP exclusions selected in the multi-select dropdown."""

        available = bmp_types_table_data()["BMP Name"].astype(str).tolist()
        try:
            excluded = {str(x) for x in (input.excluded_bmps() or [])}
        except Exception:
            excluded = set()
        return [name for name in available if name in excluded]

    @reactive.calc
    def selected_bmp_names() -> list[str]:
        """BMPs still available after global exclusions are applied."""

        excluded = set(excluded_bmp_names())
        return [
            name
            for name in bmp_types_table_data()["BMP Name"].astype(str).tolist()
            if name not in excluded
        ]

    @reactive.calc
    def excluded_subbasin_names() -> list[str]:
        """STEP 2 subbasins globally excluded from real-BMP placement."""

        available = subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
        try:
            excluded = {str(x) for x in (input.excluded_subbasins() or [])}
        except Exception:
            excluded = set()
        return [name for name in available if name in excluded]

    @reactive.calc
    def eligible_subbasin_names() -> list[str]:
        """Subbasins where real BMP placement remains permitted."""

        excluded = set(excluded_subbasin_names())
        return [
            name
            for name in subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
            if name not in excluded
        ]

    def _restriction_bmp_input_id(subbasin: str) -> str:
        """Return a stable Shiny input id for one subbasin's BMP checklist."""

        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(subbasin)).strip("_") or "subbasin"
        return f"excluded_bmps_for_{safe}"

    @reactive.calc
    def pair_exclusion_map() -> dict[str, set[str]]:
        """Return stored BMP–subbasin exclusions as sets for easy lookup."""

        raw = pair_bmp_exclusions() or {}
        return {str(sub): {str(bmp) for bmp in bmps} for sub, bmps in dict(raw).items()}

    @reactive.calc
    def active_pair_exclusions() -> set[tuple[str, str]]:
        """Return valid explicit restrictions for the current BMP/subbasin inputs."""

        valid_subs = set(subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist())
        # A stored restriction for a globally disabled BMP is preserved in the
        # state but is not active until that BMP is enabled again.
        valid_bmps = set(selected_bmp_names())
        pairs: set[tuple[str, str]] = set()
        for sub, bmps in pair_exclusion_map().items():
            if sub not in valid_subs:
                continue
            for bmp in bmps:
                if bmp in valid_bmps:
                    pairs.add((sub, bmp))
        return pairs

    def _availability_for_pair(subbasin: str, bmp_name: str) -> tuple[bool, str, float]:
        """Return allowed flag, audit reason, and current F_ij upper bound."""

        if str(bmp_name) == "No BMP":
            return True, "Untreated bypass option", 1.0
        if str(subbasin) not in set(eligible_subbasin_names()):
            return False, "Subbasin excluded from BMP placement", 0.0
        if (str(subbasin), str(bmp_name)) in active_pair_exclusions():
            return False, "Subbasin-specific BMP restriction", 0.0
        return True, "", 1.0

    def _annotate_availability(df: pd.DataFrame) -> pd.DataFrame:
        """Add availability fields used by the audit and PuLP upper bounds."""

        if df is None or df.empty:
            return pd.DataFrame() if df is None else df.copy()
        out = df.copy()
        allowed_values: list[bool] = []
        reasons: list[str] = []
        upper_bounds: list[float] = []
        for _, row in out.iterrows():
            allowed, reason, upper_bound = _availability_for_pair(
                str(row.get("Subbasin", "")), str(row.get("BMP", ""))
            )
            allowed_values.append(allowed)
            reasons.append(reason)
            upper_bounds.append(upper_bound)
        out["Allowed"] = allowed_values
        out["Exclusion reason"] = reasons
        out["Decision upper bound"] = upper_bounds
        front = [
            c
            for c in [
                "Subbasin",
                "BMP",
                "Allowed",
                "Exclusion reason",
                "Decision upper bound",
            ]
            if c in out.columns
        ]
        rest = [c for c in out.columns if c not in front]
        return out[front + rest]

    @reactive.calc
    def event_duration_seconds() -> float:
        """Return event/loading duration used to convert total loads to rates.

        This is separate from SWMM simulation duration. Use the duration over
        which the event pollutant loads should be converted to rates.
        """

        try:
            hours = float(input.event_duration_hours() or 0.0)
        except Exception:
            hours = 0.0
        return max(hours * 3600.0, 0.0)

    @reactive.calc
    def active_storm_duration_hours() -> float:
        """Return editable rainfall duration used only for infiltration sizing.

        Step 3 pre-fills this field from an internal SWMM rainfall time series
        when available. Manual CSV scenarios intentionally have no hidden 2-hour
        fallback: the user must provide the applicable storm duration.
        """

        try:
            hours = float(input.storm_duration_hours() or 0.0)
            return max(hours, 0.0)
        except Exception:
            return 0.0

    @reactive.calc
    def scenario_target_definitions() -> list[dict[str, Any]]:
        """Dynamic target definitions for the variables actually imported."""

        _, inp_meta = parsed_inp()
        _, rpt_meta = parsed_rpt()
        return active_target_definitions(subbasins_display(), inp_meta, rpt_meta)

    @reactive.calc
    def target_reduction_percentages() -> dict[str, float]:
        """Collect Step 3 reduction percentages from the dynamic UI inputs."""

        reductions: dict[str, float] = {}
        for definition in scenario_target_definitions():
            parameter = str(definition["parameter"])
            input_id = target_input_id(parameter)
            try:
                reductions[parameter] = float(getattr(input, input_id)() or 0.0)
            except Exception:
                reductions[parameter] = 0.0
        return reductions

    @reactive.calc
    def target_criteria() -> pd.DataFrame:
        """Calculate the Step 3 target table used by the optimizer."""

        return calculate_target_table(
            subbasins=subbasins_display(),
            event_duration_seconds=event_duration_seconds(),
            reduction_percent=target_reduction_percentages(),
            target_definitions=scenario_target_definitions(),
        )

    @reactive.calc
    def bmp_unit_costs_table_data() -> pd.DataFrame:
        """Calculate BMP unit costs used in the optimization objective."""

        return calculate_bmp_unit_cost_table(
            data_dir=active_data_dir(),
            city=str(input.cost_city()),
            year=int(input.cost_year()),
            life_cycle_years=float(input.life_cycle_years() or 0.0),
            interest_rate_pct=float(input.interest_rate_pct() or 0.0),
            land_cost_per_acre=float(input.land_cost_per_acre() or 0.0),
        )

    @reactive.calc
    def selected_bmp_unit_costs_table_data() -> pd.DataFrame:
        """Step 4 cost table filtered to BMPs enabled in Step 2."""

        df = bmp_unit_costs_table_data()
        if df is None or df.empty:
            return pd.DataFrame()
        return df[df["bmp_name"].astype(str).isin(selected_bmp_names())].copy()

    def current_cost_signature() -> tuple[object, ...] | None:
        """Return the current Step 4 cost inputs as a comparison tuple."""

        try:
            return (
                str(input.cost_city()),
                int(input.cost_year()),
                float(input.life_cycle_years()),
                float(input.interest_rate_pct()),
                float(input.land_cost_per_acre()),
            )
        except Exception:
            return None

    def validate_cost_inputs() -> tuple[bool, str]:
        """Validate user-facing cost assumptions before generating unit costs."""

        sig = current_cost_signature()
        if sig is None:
            return False, "Complete all cost input fields before processing."
        _, _, life_cycle, interest, land_cost = sig
        if life_cycle <= 0:
            return False, "BMP life cycle must be greater than zero so O&M present value can be calculated."
        if interest < 0:
            return False, "Interest rate cannot be negative."
        if land_cost < 0:
            return False, "Land cost cannot be negative."
        return True, ""

    @reactive.calc
    def cost_inputs_are_processed() -> bool:
        """True only when the current Step 4 inputs match the processed inputs."""

        current = current_cost_signature()
        saved = processed_cost_signature()
        return current is not None and saved is not None and tuple(current) == tuple(saved)

    @reactive.effect
    @reactive.event(input.process_cost_inputs)
    def _process_cost_inputs() -> None:
        """STEP 4 validate and explicitly process current life-cycle costs."""

        valid, message = validate_cost_inputs()
        if not valid:
            processed_cost_signature.set(None)
            cost_process_state.set("error")
            cost_process_message.set(message)
            return

        try:
            with ui.Progress(min=0, max=1) as progress:
                progress.set(0.15, message="Processing cost assumptions", detail="Reading cost and ENR databases...")
                progress.set(0.55, detail="Calculating BMP unit costs...")
                unit_costs = bmp_unit_costs_table_data()
                if unit_costs is None or unit_costs.empty:
                    raise ValueError("No BMP unit-cost rows were generated.")
                progress.set(1.0, detail="Cost assumptions ready.")
        except Exception as exc:
            processed_cost_signature.set(None)
            cost_process_state.set("error")
            cost_process_message.set(f"Cost inputs need review: {exc}")
            return

        processed_cost_signature.set(current_cost_signature())
        cost_process_state.set("success")
        cost_process_message.set(
            "Cost assumptions are ready. Continue to objective selection and co-benefit settings if needed."
        )

    @reactive.calc
    def cobenefit_weight_values() -> dict[str, float]:
        """Collect Step 4 co-benefit importance weights from the UI.

        Weights are constrained to 0-5 in the interface. A value of 0 means
        that the criterion has no importance and is excluded from the weighted
        average. Defensive fallback values are included so the app can still
        calculate defaults before the Step 4 controls are rendered.
        """

        default_weights, _, _ = active_cobenefit_defaults()
        values: dict[str, float] = {}
        for criterion in active_cobenefit_criteria():
            input_id = cobenefit_weight_input_id(criterion)
            default_value = float(default_weights.get(criterion, 1.0))
            try:
                raw = getattr(input, input_id)()
                values[criterion] = float(raw if raw is not None else default_value)
            except Exception:
                values[criterion] = default_value
        return values

    @reactive.calc
    def cobenefit_score_values() -> dict[str, dict[str, float]]:
        """Collect Step 4 BMP contribution scores from the UI.

        The nested dictionary structure is:
        ``{criterion: {bmp_name: score_0_to_5}}``.
        """

        _, default_scores, bmp_names = active_cobenefit_defaults()
        values: dict[str, dict[str, float]] = {}
        for criterion in active_cobenefit_criteria():
            values[criterion] = {}
            for bmp_name in bmp_names:
                input_id = cobenefit_score_input_id(criterion, bmp_name)
                default_score = float(default_scores.get(criterion, {}).get(bmp_name, 0.0))
                try:
                    raw_value = getattr(input, input_id)()
                    values[criterion][bmp_name] = float(raw_value if raw_value is not None else default_score)
                except Exception:
                    values[criterion][bmp_name] = default_score
        return values

    @reactive.calc
    def bmp_cobenefit_scores_table_data() -> pd.DataFrame:
        """Calculate one final normalized co-benefit score for each BMP."""

        return calculate_bmp_cobenefit_scores(
            weights=cobenefit_weight_values(),
            scores=cobenefit_score_values(),
        )

    # -------------------------------------------------------------------------
    # Internal solver matrix and optimization trigger
    # -------------------------------------------------------------------------
    solver_solution = reactive.value(None)

    @reactive.calc
    def solver_matrix_audit() -> pd.DataFrame:
        """Build the internal long-format solver matrix for development review."""

        audit = build_solver_matrix_audit(
            subbasins_display=subbasins_display(),
            bmps=selected_bmp_unit_costs_table_data(),
            targets=target_criteria(),
            data_dir=active_data_dir(),
            event_duration_seconds=event_duration_seconds(),
            storm_duration_hours=active_storm_duration_hours(),
        )
        if audit is None or audit.empty:
            return pd.DataFrame()
        # Keep excluded combinations in the audit with a decision upper bound of zero.
        return _annotate_availability(audit)

    @reactive.calc
    def solver_constraint_audit() -> pd.DataFrame:
        """Build a constraint-oriented audit table from the internal matrix."""

        return build_constraint_matrix_from_audit(solver_matrix_audit())

    @reactive.calc
    def optimization_coefficients() -> pd.DataFrame:
        """Build the PuLP-ready coefficient matrix from audit and cost tables."""

        coef = build_optimization_data(
            subbasins_display=subbasins_display(),
            bmps=selected_bmp_unit_costs_table_data(),
            targets=target_criteria(),
            audit_matrix=solver_matrix_audit(),
            event_duration_seconds=event_duration_seconds(),
            include_no_bmp=True,
        )
        if coef is None or coef.empty:
            return pd.DataFrame()
        # Do not delete prohibited combinations. Assigning an upper bound of
        # zero lets PuLP keep a complete auditable matrix while making the
        # prohibited decision mathematically impossible. No BMP always remains
        # available so untreated flow/load can bypass treatment.
        out = _annotate_availability(coef).reset_index(drop=True)

        # Add the H234:Hn-style subbasin cap and the current objective
        # coefficients to the developer audit before PuLP is run. These columns
        # are diagnostic only; optimizer.py independently enforces the same
        # values when constructing the LP.
        caps = subbasin_taf_caps()
        out["Subbasin TAF cap"] = out["Subbasin"].astype(str).map(caps).fillna(default_taf_cap_value())

        cb_table = bmp_cobenefit_scores_table_data()
        cb_lookup = (
            {str(row["BMP"]): float(row["Final score"]) for _, row in cb_table.iterrows()}
            if cb_table is not None and not cb_table.empty
            else {}
        )
        try:
            lambda_value = float(input.lambda_cb() or 0.0) if input.objective() == "Cost + Co-benefits" else 0.0
        except Exception:
            lambda_value = 0.0
        lambda_value = min(1.0, max(0.0, lambda_value))
        out["Co-benefit score"] = [0.0 if str(bmp) == "No BMP" else float(cb_lookup.get(str(bmp), 0.0)) for bmp in out["BMP"]]
        out["Objective multiplier"] = 1.0 - lambda_value * out["Co-benefit score"]
        out["Adjusted cost coefficient"] = out["Cost coefficient"] * out["Objective multiplier"]
        return out

    @reactive.effect
    @reactive.event(input.run_solver)
    def _run_solver_after_review() -> None:
        """Run the PuLP optimization with user-visible progress feedback.

        A new solve invalidates previous results immediately. Step 6 is only
        re-enabled if this new solve returns ``Optimal``. This is workflow/state
        protection only; it does not change the optimization formulation.
        """

        # ------------------------------------------------------------------
        # Invalidate any previous result before a new optimization begins.
        # ------------------------------------------------------------------
        results_unlocked.set(False)
        if int(max_unlocked_step() or 0) > 5:
            max_unlocked_step.set(5)

        # Clear previous placement/performance tables immediately so a prior Optimal
        # solution can never be mistaken for the current scenario while CBC is
        # running or after an infeasible solve.
        solver_solution.set(
            {
                "status": "Running",
                "objective_value": None,
                "placement": pd.DataFrame(),
                "performance": pd.DataFrame(),
            }
        )

        cobenefit_weights_valid = True
        if input.objective() == "Cost + Co-benefits":
            weights = cobenefit_weight_values()
            cobenefit_weights_valid = bool(weights) and sum(max(float(v or 0.0), 0.0) for v in weights.values()) > 0

        if (
            subbasins_display().empty
            or event_duration_seconds() <= 0
            or active_storm_duration_hours() <= 0
            or not cost_inputs_are_processed()
            or not cobenefit_weights_valid
        ):
            solver_solution.set(
                {
                    "status": "Missing required inputs",
                    "objective_value": None,
                    "placement": pd.DataFrame(),
                    "performance": pd.DataFrame(),
                }
            )
            return

        with reactive.isolate():
            cb_table = bmp_cobenefit_scores_table_data()
            cb_scores = (
                {str(row["BMP"]): float(row["Final score"]) for _, row in cb_table.iterrows()}
                if cb_table is not None and not cb_table.empty
                else {}
            )
            config = OptimizationConfig(
                objective=input.objective(),
                lambda_cobenefit=float(input.lambda_cb() or 0.0) if input.objective() == "Cost + Co-benefits" else 0.0,
                cobenefit_scores=cb_scores,
                subbasin_taf_caps=subbasin_taf_caps(),
            )
            coeffs = optimization_coefficients()
            targets = target_criteria()

        # ui.Progress provides interface feedback while the model solves.
        with ui.Progress(min=0, max=1) as progress:
            progress.set(0.15, message="Running optimization", detail="Preparing optimization model...")
            progress.set(0.45, detail="Solving BMP allocation...")
            solved = solve_bmp_placement_from_coefficients(coeffs, targets, config)
            progress.set(1.0, detail="Optimization complete.")

        solver_solution.set(solved)

        latest_status = str(solved.get("status", "") or "")
        if latest_status == "Optimal":
            results_unlocked.set(True)
            _unlock_through(6)
        else:
            # Infeasible, unbounded, failed, or any other non-optimal status
            # keeps Results unavailable. The user can revise inputs and rerun.
            results_unlocked.set(False)
            if int(max_unlocked_step() or 0) > 5:
                max_unlocked_step.set(5)

    def solution() -> dict[str, Any]:
        """Return the latest solver result, or a safe empty result before running."""

        return solver_solution() or {
            "status": "Not run yet",
            "objective_value": None,
            "placement": pd.DataFrame(),
            "performance": pd.DataFrame(),
        }

    @output
    @render.ui
    def subbasin_status_message() -> Any:
        """STEP 1 processing result shown directly above the active input table."""

        state = str(step1_process_state() or "idle")
        message = str(step1_process_message() or "")
        if state == "success":
            return ui.div(message, class_="alert alert-success")
        if state == "error":
            return ui.div(message, class_="alert alert-danger")
        return ui.div(message, class_="alert alert-info")

    @output
    @render.ui
    def step1_navigation_message() -> Any:
        """Reserved for future step-validation hints without changing layout."""
        return ui.div()

    @output
    @render.data_frame
    def subbasins_preview() -> render.DataGrid:
        df = display_subbasins_for_preview(subbasins_display())
        height = "260px" if df.empty else "520px"
        return render.DataGrid(round_numeric_columns(df), filters=False, height=height)

    @output
    @render.ui
    def bmp_selection_controls() -> Any:
        """STEP 2 compact dropdown listing BMPs to exclude globally."""

        names = bmp_types_table_data()["BMP Name"].astype(str).tolist()
        if not names:
            return ui.div("No BMPs were found in BMP_types.csv.", class_="alert alert-danger")
        return ui.input_selectize(
            "excluded_bmps",
            "BMPs to exclude",
            choices=names,
            selected=[],
            multiple=True,
            width="100%",
            options={"placeholder": "None — all BMPs are currently allowed"},
        )

    @output
    @render.ui
    def subbasin_selection_controls() -> Any:
        """STEP 2 compact dropdown listing subbasins to exclude globally."""

        names = subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
        if not names:
            return ui.div(
                "Load and process watershed data first.",
                class_="alert alert-info",
            )
        return ui.input_selectize(
            "excluded_subbasins",
            "Subbasins to exclude from BMP placement",
            choices=names,
            selected=[],
            multiple=True,
            width="100%",
            options={"placeholder": "None — all subbasins are currently eligible"},
        )

    @output
    @render.ui
    def pair_restriction_subbasin_selector() -> Any:
        """Choose one subbasin whose BMP-specific restrictions will be edited."""

        names = eligible_subbasin_names()
        if not names:
            return ui.div("No eligible subbasins are currently available.", class_="alert alert-info")
        return ui.input_selectize(
            "restriction_subbasin",
            "Subbasin",
            choices=names,
            selected=names[0],
            width="100%",
        )

    @output
    @render.ui
    def pair_restriction_bmp_controls() -> Any:
        """STEP 2 select BMPs to exclude only from the selected subbasin."""

        try:
            sub = str(input.restriction_subbasin() or "")
        except Exception:
            sub = ""
        if not sub:
            return ui.div("Select a subbasin above.", class_="alert alert-info")

        visible_bmps = selected_bmp_names()
        if not visible_bmps:
            return ui.div(
                "No globally enabled BMPs are available to restrict.",
                class_="alert alert-warning",
            )

        excluded = sorted(pair_exclusion_map().get(sub, set()) & set(visible_bmps))
        return ui.input_selectize(
            _restriction_bmp_input_id(sub),
            f"BMPs to exclude from {sub}",
            choices=visible_bmps,
            selected=excluded,
            multiple=True,
            width="100%",
            options={"placeholder": "None — all globally enabled BMPs are allowed here"},
        )

    @reactive.effect
    @reactive.event(input.save_subbasin_bmp_restrictions)
    def _save_subbasin_bmp_restrictions() -> None:
        """Persist explicit BMP exclusions for the selected subbasin."""

        try:
            sub = str(input.restriction_subbasin() or "")
        except Exception:
            sub = ""
        if not sub:
            return

        visible_bmps = selected_bmp_names()
        input_id = _restriction_bmp_input_id(sub)
        try:
            excluded_now = {str(x) for x in (getattr(input, input_id)() or [])}
        except Exception:
            excluded_now = set()

        current = {k: set(v) for k, v in pair_exclusion_map().items()}
        old = current.get(sub, set())
        # Preserve restrictions for temporarily globally excluded BMPs; edit only
        # the BMPs visible in this subbasin-specific dropdown.
        new_excluded = (old - set(visible_bmps)) | (excluded_now & set(visible_bmps))
        if new_excluded:
            current[sub] = new_excluded
        else:
            current.pop(sub, None)
        pair_bmp_exclusions.set({k: tuple(sorted(v)) for k, v in current.items()})

    @reactive.effect
    @reactive.event(input.reset_selected_subbasin_restrictions)
    def _reset_selected_subbasin_restrictions() -> None:
        """Remove all explicit BMP restrictions for the selected subbasin."""

        try:
            sub = str(input.restriction_subbasin() or "")
        except Exception:
            sub = ""
        if not sub:
            return
        current = {k: set(v) for k, v in pair_exclusion_map().items()}
        current.pop(sub, None)
        pair_bmp_exclusions.set({k: tuple(sorted(v)) for k, v in current.items()})

    @reactive.effect
    @reactive.event(input.clear_all_pair_restrictions)
    def _clear_all_pair_restrictions() -> None:
        """Restore the default: every enabled BMP allowed in every subbasin."""

        pair_bmp_exclusions.set({})

    @reactive.calc
    def default_taf_cap_value() -> float:
        """Return the default subbasin cap on the sum of real-BMP TAF values."""

        try:
            value = float(input.default_taf_cap() or 0.0)
        except Exception:
            value = 1.0
        return min(1.0, max(0.0, value))

    @reactive.calc
    def subbasin_taf_caps() -> dict[str, float]:
        """Return the effective real-BMP TAF cap for every subbasin."""

        overrides = {str(k): float(v) for k, v in dict(subbasin_taf_cap_overrides() or {}).items()}
        default_cap = default_taf_cap_value()
        names = subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
        return {
            sub: min(1.0, max(0.0, float(overrides.get(sub, default_cap))))
            for sub in names
        }

    @output
    @render.ui
    def taf_cap_subbasin_selector() -> Any:
        """Choose one subbasin for an optional cap override."""

        names = subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
        if not names:
            return ui.div("Load Step 1 subbasin inputs first.", class_="alert alert-info")
        return ui.input_select(
            "taf_cap_subbasin",
            "Subbasin override",
            choices=names,
            selected=names[0],
        )

    @output
    @render.ui
    def taf_cap_value_control() -> Any:
        """Show the current default/override cap for the selected subbasin."""

        try:
            sub = str(input.taf_cap_subbasin() or "")
        except Exception:
            sub = ""
        if not sub:
            return ui.div("Select a subbasin above.", class_="alert alert-info")

        overrides = dict(subbasin_taf_cap_overrides() or {})
        current_value = float(overrides.get(sub, default_taf_cap_value()))
        return ui.input_numeric(
            "selected_subbasin_taf_cap",
            f"Maximum real-BMP TAF in {sub}",
            value=current_value,
            min=0.0,
            max=1.0,
            step=0.05,
        )

    @reactive.effect
    @reactive.event(input.save_selected_taf_cap)
    def _save_selected_taf_cap() -> None:
        """Save one explicit H-column-style cap override for a subbasin."""

        try:
            sub = str(input.taf_cap_subbasin() or "")
            value = float(input.selected_subbasin_taf_cap() or 0.0)
        except Exception:
            return
        if not sub:
            return
        value = min(1.0, max(0.0, value))
        current = dict(subbasin_taf_cap_overrides() or {})
        if abs(value - default_taf_cap_value()) <= 1e-12:
            current.pop(sub, None)
        else:
            current[sub] = value
        subbasin_taf_cap_overrides.set(current)

    @reactive.effect
    @reactive.event(input.reset_selected_taf_cap)
    def _reset_selected_taf_cap() -> None:
        """Return the selected subbasin to the current default cap."""

        try:
            sub = str(input.taf_cap_subbasin() or "")
        except Exception:
            sub = ""
        if not sub:
            return
        current = dict(subbasin_taf_cap_overrides() or {})
        current.pop(sub, None)
        subbasin_taf_cap_overrides.set(current)

    @reactive.effect
    @reactive.event(input.clear_all_taf_cap_overrides)
    def _clear_all_taf_cap_overrides() -> None:
        """Restore every subbasin to the current default TAF cap."""

        subbasin_taf_cap_overrides.set({})

    @output
    @render.data_frame
    def taf_cap_summary_table() -> render.DataGrid:
        """Show the effective cap that will be passed to PuLP for each subbasin."""

        caps = subbasin_taf_caps()
        overrides = {str(k) for k in dict(subbasin_taf_cap_overrides() or {})}
        eligible = set(eligible_subbasin_names())
        rows = [
            {
                "Subbasin": sub,
                "BMP implementation limit (H)": cap,
                "Source": "Override" if sub in overrides else "Default",
                #"BMP placement eligible": sub in eligible,
            }
            for sub, cap in caps.items()
        ]
        if not rows:
            rows = [{"Subbasin": "—", "Real-BMP TAF cap": default_taf_cap_value(), "Source": "Default", "BMP placement eligible": False}]
        return render.DataGrid(round_numeric_columns(pd.DataFrame(rows)), filters=False, height="320px")

    @output
    @render.data_frame
    def restriction_summary_table() -> render.DataGrid:
        """STEP 2 combined summary of every exclusion layer."""

        rows: list[dict[str, Any]] = []

        global_bmps = excluded_bmp_names()
        if global_bmps:
            rows.append(
                {
                    "Restriction type": "Global BMP exclusion",
                    "Applies to": "All subbasins",
                    "Excluded": ", ".join(global_bmps),
                }
            )

        global_subs = excluded_subbasin_names()
        if global_subs:
            rows.append(
                {
                    "Restriction type": "Subbasin exclusion",
                    "Applies to": "All real BMPs",
                    "Excluded": ", ".join(global_subs),
                }
            )

        selected_global = set(selected_bmp_names())
        for sub in eligible_subbasin_names():
            excluded = sorted(
                bmp
                for s, bmp in active_pair_exclusions()
                if s == sub and bmp in selected_global
            )
            if excluded:
                rows.append(
                    {
                        "Restriction type": "Subbasin-specific BMP exclusion",
                        "Applies to": sub,
                        "Excluded": ", ".join(excluded),
                    }
                )

        if not rows:
            rows = [
                {
                    "Restriction type": "None",
                    "Applies to": "—",
                    "Excluded": "No BMP or subbasin restrictions have been added.",
                }
            ]

        return render.DataGrid(pd.DataFrame(rows), filters=False, height="300px")

    @output
    @render.ui
    def bmp_preferences_status_message() -> Any:
        """Summarize Step 2 restrictions without changing watershed targets."""

        available_bmps = bmp_types_table_data()["BMP Name"].astype(str).tolist()
        available_subs = subbasins_display().get("Sub", pd.Series(dtype=str)).astype(str).tolist()
        if not available_subs:
            return ui.div("Waiting for Step 1 subbasin inputs.", class_="alert alert-info")

        selected_bmps = selected_bmp_names()
        selected_subs = eligible_subbasin_names()
        if not selected_bmps:
            return ui.div(
                "No real BMPs are selected. Positive reduction targets will be infeasible until at least one BMP is enabled.",
                class_="alert alert-warning",
            )
        pair_count = sum(
            1
            for sub, bmp in active_pair_exclusions()
            if sub in set(selected_subs) and bmp in set(selected_bmps)
        )
        return ui.div(
            f"{len(selected_bmps)} of {len(available_bmps)} BMPs enabled; "
            f"{len(selected_subs)} of {len(available_subs)} subbasins eligible for placement; "
            f"{pair_count} BMP–subbasin pair restriction(s).",
            class_="alert alert-success",
        )

    @output
    @render.ui
    def scenario_duration_inputs() -> Any:
        """STEP 3 editable duration fields in a compact label/value layout.

        SWMM mode seeds storm duration from the rain-gage TIMESERIES when that
        value is available. The pollutant loading duration preserves the prior
        24-hour working default for SWMM scenarios. Both controls remain
        editable. Manual CSV scenarios start blank so no SWMM-specific duration
        is assumed.
        """

        event_value = ""
        storm_value = ""

        if step1_input_source() == "swmm":
            _, inp_meta = parsed_inp()

            # This is the event-load-to-rate assumption, not the total SWMM
            # simulation window. Keep the existing 24-hour starting value for
            # SWMM workflows while allowing the user to edit it.
            event_value = "24"

            detected_storm = float(inp_meta.get("rainfall_duration_hours_detected", 0.0) or 0.0)
            if detected_storm > 0:
                storm_value = f"{detected_storm:.6g}"

        def duration_row(label: str, control: Any) -> Any:
            return ui.div(
                ui.div(label, class_="duration-input-label"),
                ui.div(control, class_="duration-input-control"),
                class_="duration-input-row",
            )

        return ui.div(
            duration_row(
                "Event duration for pollutant rate conversion (hours)",
                ui.input_text(
                    "event_duration_hours",
                    "", #label is already in the row
                    value=event_value,
                    placeholder="Enter hours",
                ),
            ),
            duration_row(
                "Storm duration (hours)",
                ui.input_text(
                    "storm_duration_hours",
                    "Storm duration (hours)",
                    value=storm_value,
                    placeholder="Enter hours",
                ),
            ),
        )

    @output
    @render.ui
    def scenario_targets_status_message() -> Any:
        """Show whether Step 2 has enough information to calculate targets."""

        if subbasins_display().empty:
            return ui.div(
                "Scenario Targets is waiting for a valid Step 1 subbasin input table.",
                class_="alert alert-info",
            )

        if event_duration_seconds() <= 0:
            return ui.div(
                "Event/loading duration is missing. Enter the event duration used to convert total loads to rates.",
                class_="alert alert-danger",
            )

        if active_storm_duration_hours() <= 0:
            return ui.div(
                "Storm duration is missing. Enter a positive rainfall duration for infiltration-based BMP sizing.",
                class_="alert alert-danger",
            )

        if not scenario_target_definitions():
            return ui.div(
                "No peak-flow or pollutant variables with positive values were detected in Step 1.",
                class_="alert alert-warning",
            )

        return ui.div(
            "Target criteria are ready. Review or edit the percent reductions below.",
            class_="alert alert-success",
        )

    @output
    @render.ui
    def target_reduction_inputs() -> Any:
        """Render one percent-reduction input for each target parameter."""

        controls = []
        for definition in scenario_target_definitions():
            parameter = str(definition["parameter"])
            group = str(definition["group"])
            unit = str(definition.get("unit", ""))
            label_unit = f" [{unit}]" if unit else ""
            controls.append(
                ui.input_numeric(
                    target_input_id(parameter),
                    f"{group} — {parameter} reduction %",
                    value=0,
                    min=0,
                    max=100,
                    step=1,
                )
            )
        if not controls:
            return ui.div(
                "No target variables detected yet. Upload a valid SWMM .rpt/.inp or manual table first.",
                class_="alert alert-info",
            )
        return ui.div(*controls)

    @output
    @render.data_frame
    def target_criteria_table() -> render.DataGrid:
        """STEP 3 user-facing target table.

        The solver still receives Internal input/Internal Target/Internal Unit
        through target_criteria(). Those columns are hidden here because they are
        implementation details and can confuse users who define targets in event
        loads rather than internal rates.
        """

        df = target_criteria().copy()
        display_columns = [
            c for c in ["Group", "Parameter", "Input value", "% reduction", "Target", "Unit"]
            if c in df.columns
        ]
        return render.DataGrid(
            round_numeric_columns(df[display_columns] if display_columns else df),
            filters=False,
            height="520px",
        )

    @output
    @render.ui
    def cost_process_status_message() -> Any:
        """STEP 4 processing/checkpoint status for the current cost inputs."""

        if cost_inputs_are_processed():
            return ui.div(
                "Cost assumptions are ready. Continue to objective selection and co-benefit settings if needed.",
                class_="alert alert-success mt-3",
            )

        # A previous successful process becomes stale as soon as any cost input
        # changes. This tells the user to process the new assumptions again.
        if processed_cost_signature() is not None:
            return ui.div(
                "Cost inputs changed. Select Process cost assumptions again before continuing.",
                class_="alert alert-warning mt-3",
            )

        state = str(cost_process_state() or "idle")
        message = str(cost_process_message() or "")
        if state == "error":
            return ui.div(message, class_="alert alert-danger mt-3")
        return ui.div(message, class_="alert alert-info mt-3")

    @output
    @render.ui
    def processed_cost_calculations_panel() -> Any:
        """Show the BMP unit-cost calculation table only after processing."""

        if not cost_inputs_are_processed():
            return ui.div()
        return ui.div(
            ui.div("BMP unit-cost calculations", class_="section-title"),
            ui.p(
                "These processed unit costs are automatically calculated and used as the life-cycle cost coefficients in the optimization objective. No user input is required.",
                class_="section-help",
            ),
            ui.output_data_frame("bmp_unit_costs_table"),
            class_="clean-card",
        )

    @output
    @render.ui
    def objective_and_cobenefit_panel() -> Any:
        """STEP 4 objective selection plus conditional co-benefit settings."""

        if not cost_inputs_are_processed():
            return ui.div()

        return ui.div(
            ui.div(
                ui.div("Choose optimization objective", class_="section-title"),
                ui.input_radio_buttons(
                    "objective",
                    "Optimization objective",
                    choices={
                        "Cost Only": "Minimize cost",
                        "Cost + Co-benefits": "Minimize cost with co-benefit-informed prioritization",
                    },
                    selected="Cost Only",
                ),
                ui.output_ui("objective_status_message"),
                class_="clean-card",
            ),
            ui.div(
                ui.output_ui("step4_cobenefit_panel"),
                class_="cobenefit-dynamic-shell",
            ),
        )

    @output
    @render.data_frame
    def bmp_unit_costs_table() -> render.DataGrid:
        """Display BMP unit-cost calculations used by the optimization objective."""

        cols = [
            "bmp_name",
            "construction_cost_per_ft3",
            "om_present_cost_per_ft3",
            "land_cost_per_ft3",
            "cost_reference_city",
            "cost_reference_year",
            "reference_enr_index",
            "cost_coefficient_enr",
            "unit_cost_per_ft3",
            "area_ft2",
            "depth_ft",
            "unit_total_cost",
        ]
        df = bmp_unit_costs_table_data()
        keep = [c for c in cols if c in df.columns]
        return render.DataGrid(round_numeric_columns(df[keep]), filters=False, height="420px")

    @output
    @render.ui
    def objective_status_message() -> Any:
        """Explain how the selected objective affects the workflow."""

        if input.objective() == "Cost + Co-benefits":
            return ui.div("Co-benefit-informed objective selected. Review the co-benefit settings below before continuing.", class_="alert alert-info")

        return ui.div("Cost-only objective selected. No co-benefit inputs are required.", class_="alert alert-success")

    @output
    @render.ui
    def step4_cobenefit_panel() -> Any:
        """Render co-benefit controls only when the co-benefit objective is active."""

        if input.objective() != "Cost + Co-benefits":
            return ui.div()

        controls = [
            ui.input_slider(
                "lambda_cb",
                "Co-benefit preference coefficient λ",
                min=0,
                max=1,
                value=1,
                step=0.1,
            ),
            ui.p(
                "Enter two evaluations: (1) criterion importance weight from 0 to 5, "
                "and (2) BMP contribution score from 0 to 5. "
                "Importance meaning: 0 = no importance, 1 = very low, 2 = low, "
                "3 = moderate, 4 = high, and 5 = very high importance. "
                "BMP score meaning: 0 = no contribution, 1 = very low, 2 = low, "
                "3 = moderate, 4 = high, and 5 = very high contribution."
            ),
            ui.p(
                "Default weights and BMP scores are loaded from data/BMP_Cobenefits.csv. "
                "You can modify any value before running the solver.",
                class_="text-muted",
            ),
            ui.output_ui("cobenefit_score_matrix"),
            ui.h5("Final BMP co-benefit scores"),
            ui.p(
                "When the co-benefit objective is selected, each BMP cost coefficient is adjusted as "
                "Cost × (1 − λ × S), where S is the final normalized BMP score below."
            ),
            ui.output_data_frame("bmp_cobenefit_scores_table"),
            ui.output_plot("bmp_cobenefit_scores_plot"),
        ]
        return ui.div(
            ui.div("Co-benefit settings", class_="section-title"),
            *controls,
            class_="clean-card",
        )

    @output
    @render.ui
    def cobenefit_score_matrix() -> Any:
        default_weights, default_scores, bmp_names = active_cobenefit_defaults()
        return cobenefit_score_table_ui(
            criteria=active_cobenefit_criteria(),
            bmp_names=bmp_names,
            default_weights=default_weights,
            default_scores=default_scores,
        )

    @output
    @render.data_frame
    def bmp_cobenefit_scores_table() -> render.DataGrid:
        """Display only the final BMP-level co-benefit scores used later by the optimizer."""

        df = bmp_cobenefit_scores_table_data().copy()
        display_df = df[["BMP", "Final score"]].copy()
        display_df["Final score"] = display_df["Final score"].round(2)
        return render.DataGrid(round_numeric_columns(display_df), filters=False, height="420px")

    @output
    @render.plot
    def bmp_cobenefit_scores_plot():
        """Create a ranked bar plot of final BMP co-benefit scores with value labels."""

        df = bmp_cobenefit_scores_table_data().sort_values("Final score", ascending=True).copy()
        df["Final score"] = df["Final score"].round(2)

        fig, ax = plt.subplots(figsize=(9, 5.5))
        bars = ax.barh(df["BMP"], df["Final score"])

        ax.set_xlabel("Final co-benefit score (0–1)")
        ax.set_ylabel("BMP")
        ax.set_title("Final BMP co-benefit scores")
        ax.set_xlim(0, 1)

        # Add readable labels to the end of each bar. This makes the figure
        # useful even when small differences are difficult to see visually.
        ax.bar_label(
            bars,
            labels=[f"{value:.2f}" for value in df["Final score"]],
            padding=3,
        )

        fig.tight_layout()
        return fig

    @output
    @render.ui
    def solver_matrix_audit_panel() -> Any:
        """Optionally show internal solver-matrix audit tables for development."""

        if not input.show_solver_matrix_audit():
            return ui.div("Internal solver matrix is hidden.", class_="alert alert-secondary")

        return ui.div(
            ui.h5("Solver matrix audit"),
            ui.output_data_frame("solver_matrix_audit_table"),
            ui.h5("Constraint coefficient audit"),
            ui.output_data_frame("solver_constraint_audit_table"),
            ui.h5("Optimization coefficient matrix"),
            ui.output_data_frame("optimization_coefficient_table"),
            ui.h5("Subbasin real-BMP TAF cap audit"),
            ui.output_data_frame("solver_taf_cap_audit_table"),
        )

    @output
    @render.data_frame
    def solver_matrix_audit_table() -> render.DataGrid:
        """Display the full long-format matrix used for development checking."""

        return render.DataGrid(round_numeric_columns(solver_matrix_audit()), filters=False, height="520px")

    @output
    @render.data_frame
    def solver_constraint_audit_table() -> render.DataGrid:
        """Display the constraint-oriented audit table derived from the matrix."""

        return render.DataGrid(round_numeric_columns(solver_constraint_audit()), filters=False, height="420px")

    @output
    @render.data_frame
    def optimization_coefficient_table() -> render.DataGrid:
        """Display the PuLP-ready coefficient matrix for development checking."""

        return render.DataGrid(round_numeric_columns(optimization_coefficients()), filters=False, height="520px")

    @output
    @render.data_frame
    def solver_taf_cap_audit_table() -> render.DataGrid:
        """Show configured and solved real-BMP TAF caps."""

        cap_audit = solution().get("allocation_caps", pd.DataFrame())
        if cap_audit is None or cap_audit.empty:
            caps = subbasin_taf_caps()
            cap_audit = pd.DataFrame(
                [
                    {
                        "Subbasin": sub,
                        "TAF cap": cap,
                        "Real BMP TAF sum": None,
                        "Remaining No BMP fraction": None,
                        "Cap binding": None,
                    }
                    for sub, cap in caps.items()
                ]
            )
        return render.DataGrid(round_numeric_columns(cap_audit), filters=False, height="420px")

    @reactive.calc
    def subbasin_reduction_data() -> pd.DataFrame:
        """Before/after table for treated subbasins."""

        return subbasin_reduction_summary(
            solution(),
            scenario_target_definitions(),
        )

    @reactive.calc
    def before_after_plot_data() -> pd.DataFrame:
        """Return treated-subbasin Before/After values in user-facing units.

        Plot reporting uses every parameter available in the Step 1 engineering
        data, not only parameters with positive optimization targets. This lets
        the Results page show the intrinsic TSS reduction produced by a solution
        even when TSS was not used as a constraint. Pollutant coefficients are
        stored internally as rates, so they are converted back to event loads
        using the configured event/loading duration. Peak Flow remains in cfs.
        """

        data = all_parameter_reduction_data().copy()
        if data.empty:
            return data

        seconds = max(float(event_duration_seconds() or 0.0), 0.0)
        definitions = {
            str(definition.get("parameter", "")): definition
            for definition in scenario_target_definitions()
        }

        for parameter, definition in definitions.items():
            mask = data["Parameter"].astype(str) == parameter
            if not mask.any():
                continue

            method = str(definition.get("method", ""))
            display_unit = str(definition.get("unit", ""))
            if method == "load_rate":
                # lb/s (or other internal load rate) -> event load.
                for column in ["Before", "After", "Reduction"]:
                    data.loc[mask, column] = (
                        pd.to_numeric(data.loc[mask, column], errors="coerce").fillna(0.0)
                        * seconds
                    )
            if display_unit:
                data.loc[mask, "Unit"] = display_unit

        return data

    @reactive.calc
    def watershed_performance_data() -> pd.DataFrame:
        return watershed_performance_summary(
            solution(),
            target_criteria(),
            scenario_target_definitions(),
            event_duration_seconds(),
        )

    @reactive.calc
    def allocation_cost_data() -> pd.DataFrame:
        return allocation_cost_summary(solution(), selected_bmp_unit_costs_table_data())

    @reactive.calc
    def all_parameter_reduction_data() -> pd.DataFrame:
        """Report incidental reductions for every parameter present in Step 1.

        Only positive user-selected targets constrain the optimization. This
        table additionally evaluates the solved BMP allocation against the
        engineering coefficients for non-target pollutants.
        """
        return all_parameter_subbasin_reduction_summary(
            solution(),
            solver_matrix_audit(),
            scenario_target_definitions(),
        )

    @reactive.calc
    def si_reduction_data() -> pd.DataFrame:
        return international_reduction_summary(
            all_parameter_reduction_data(),
            event_duration_seconds(),
            scenario_target_definitions(),
        )

    @reactive.calc
    def treatment_performance_data() -> pd.DataFrame:
        return treatment_performance_summary(solution(), scenario_target_definitions())

    @reactive.calc
    def footprint_data() -> pd.DataFrame:
        return footprint_summary(
            solution(),
            subbasins_display(),
            selected_bmp_unit_costs_table_data(),
        )

    @reactive.calc
    def cost_effectiveness_data() -> pd.DataFrame:
        return cost_effectiveness_summary(solution(), subbasin_reduction_data())

    # ---------------------------------------------------------------------
    # STEP 6 report-style scenario summary
    # ---------------------------------------------------------------------
    @reactive.calc
    def results_input_summary_groups() -> list[tuple[str, list[tuple[str, object]]]]:
        """Return the key scenario inputs shown at the top of Results and PDF.

        Optional exclusions and TAF caps are included only when they were
        actually applied, keeping the normal results page concise.
        """

        source_label = "SWMM .rpt + .inp files" if step1_input_source() == "swmm" else "CSV file"
        watershed_rows: list[tuple[str, object]] = [
            ("Input option", source_label),
            ("Number of subbasins", len(subbasins_display())),
            ("Runoff/event loading duration", f"{event_duration_seconds() / 3600.0:.4g} hr"),
            ("Storm duration", f"{active_storm_duration_hours():.4g} hr"),
        ]

        objective_value = str(input.objective() or "Cost Only")
        objective_label = (
            "Cost + co-benefits" if objective_value == "Cost + Co-benefits" else "Cost only"
        )
        optimization_rows: list[tuple[str, object]] = [
            ("Database source", str(active_database_source() or "Default databases")),
            ("Objective function", objective_label),
        ]
        if objective_value == "Cost + Co-benefits":
            optimization_rows.append(("Co-benefit weight λ", f"{float(input.lambda_cb() or 0.0):.3g}"))

        global_bmp = sorted(excluded_bmp_names())
        if global_bmp:
            optimization_rows.append(("Globally excluded BMPs", ", ".join(global_bmp)))

        global_subs = sorted(excluded_subbasin_names())
        if global_subs:
            optimization_rows.append(("Subbasins excluded from BMP placement", ", ".join(global_subs)))

        pair_map: dict[str, list[str]] = {}
        for sub, bmp in sorted(active_pair_exclusions()):
            pair_map.setdefault(str(sub), []).append(str(bmp))
        if pair_map:
            pair_text = "; ".join(
                f"{sub}: {', '.join(sorted(bmps))}" for sub, bmps in pair_map.items()
            )
            optimization_rows.append(("Subbasin-specific BMP restrictions", pair_text))

        default_cap = default_taf_cap_value()
        overrides = {str(k): float(v) for k, v in dict(subbasin_taf_cap_overrides() or {}).items()}
        if abs(default_cap - 1.0) > 1e-9:
            optimization_rows.append(("Default real-BMP TAF cap", f"{default_cap:.3g}"))
        changed_overrides = {k: v for k, v in overrides.items() if abs(v - default_cap) > 1e-9}
        if changed_overrides:
            cap_text = "; ".join(f"{sub}: {cap:.3g}" for sub, cap in sorted(changed_overrides.items()))
            optimization_rows.append(("Subbasin TAF cap overrides", cap_text))

        target_rows: list[tuple[str, object]] = []
        criteria = target_criteria().copy()
        if not criteria.empty and "% reduction" in criteria.columns:
            criteria["% reduction"] = pd.to_numeric(criteria["% reduction"], errors="coerce").fillna(0.0)
            criteria = criteria[criteria["% reduction"] > 1e-9]
            for _, row in criteria.iterrows():
                group = str(row.get("Group", ""))
                parameter = str(row.get("Parameter", ""))
                unit = str(row.get("Unit", "")).strip()
                label = f"{group} — {parameter}" + (f" [{unit}]" if unit else "")
                value = (
                    f"{float(row.get('% reduction', 0.0)):.4g}% reduction; "
                    f"{float(row.get('Input value', 0.0)):,.5g} → {float(row.get('Target', 0.0)):,.5g}"
                )
                target_rows.append((label, value))

        cost_rows: list[tuple[str, object]] = [
            ("ENR city", str(input.cost_city())),
            ("ENR year", str(input.cost_year())),
            ("BMP life cycle", f"{float(input.life_cycle_years() or 0.0):.4g} years"),
            ("Interest rate", f"{float(input.interest_rate_pct() or 0.0):.4g}%"),
            ("Land cost", f"${float(input.land_cost_per_acre() or 0.0):,.2f}/acre"),
        ]

        groups: list[tuple[str, list[tuple[str, object]]]] = [
            ("Watershed characteristics", watershed_rows),
            ("Optimization configuration", optimization_rows),
        ]
        if target_rows:
            groups.append(("Required reductions", target_rows))
        groups.append(("Life-cycle cost assumptions", cost_rows))
        return groups

    @output
    @render.ui
    def results_input_summary() -> Any:
        """Compact first card on the Results page."""

        group_cards: list[Any] = []
        for title, rows in results_input_summary_groups():
            row_ui = [
                ui.div(
                    ui.div(str(label), class_="results-summary-label"),
                    ui.div(str(value), class_="results-summary-value"),
                    class_="results-summary-row",
                )
                for label, value in rows
            ]
            group_cards.append(
                ui.div(ui.h5(title), *row_ui, class_="results-summary-group")
            )
        return ui.div(*group_cards, class_="results-summary-grid")

    @output
    @render.text
    def run_solver_status() -> str:
        """STEP 5 solver status without exposing the objective value."""

        sol = solution()
        return f"Solver status: {sol.get('status', 'Not run yet')}"

    @output
    @render.ui
    def solver_navigation_controls() -> Any:
        """STEP 5 Back and conditional Check Results controls.

        Navigation order is consistent throughout the workflow: Back first,
        then the forward action. Both actions return the viewport to the top of
        the destination page.
        """

        status = str(solution().get("status", "Not run yet") or "Not run yet")
        controls: list[Any] = [
            ui.input_action_button(
                "back_to_cost_objective",
                "Back",
                onclick=NAV_SCROLL_ONCLICK,
                class_="btn-outline-secondary",
            )
        ]
        if status == "Optimal" and bool(results_unlocked()):
            controls.append(
                ui.input_action_button(
                    "check_results",
                    "Check Results",
                    onclick=NAV_SCROLL_ONCLICK,
                    class_="btn-primary next-step-btn",
                )
            )
        return ui.div(*controls, class_="workflow-button-row")

    def _clipboard_payload_ui(data: pd.DataFrame, payload_id: str, decimals: int = 2) -> Any:
        """Return a hidden TSV copy of the same DataFrame shown in a Results DataGrid."""

        display = round_numeric_columns(data, decimals)
        tsv = display.to_csv(sep="\t", index=False, lineterminator="\n")
        return ui.tags.textarea(
            tsv,
            id=payload_id,
            class_="clipboard-payload",
            tabindex="-1",
        )

    @output
    @render.ui
    def watershed_performance_clipboard() -> Any:
        return _clipboard_payload_ui(
            watershed_performance_data(),
            "watershed_performance_clipboard_payload",
            2,
        )

    @output
    @render.data_frame
    def watershed_performance_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(watershed_performance_data()), filters=False, height="360px")

    @output
    @render.ui
    def allocation_cost_clipboard() -> Any:
        return _clipboard_payload_ui(
            allocation_cost_data(),
            "allocation_cost_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def allocation_cost_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(allocation_cost_data(), 4), filters=False, height="470px")

    @output
    @render.ui
    def subbasin_reduction_clipboard() -> Any:
        return _clipboard_payload_ui(
            subbasin_reduction_data(),
            "subbasin_reduction_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def subbasin_reduction_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(subbasin_reduction_data(), 4), filters=False, height="520px")

    def _before_after_plot_height(data: pd.DataFrame, parameter: str) -> int:
        """Browser height for one treated-subbasin Before/After plot."""

        if data is None or data.empty:
            count = 0
        else:
            count = min(
                int((data["Parameter"].astype(str) == str(parameter)).sum()),
                15,
            )
        return max(285, min(760, 175 + 34 * count))

    @output
    @render.ui
    def result_peak_flow_plot_container() -> Any:
        """Peak-flow before/after plot shown for treated subbasins."""

        data = before_after_plot_data()
        available = set(data["Parameter"].astype(str)) if not data.empty else set()
        if "Peak Flow" not in available:
            return ui.div(
                "Peak Flow results are not available for the current input data.",
                class_="alert alert-info",
            )
        return ui.div(
            ui.output_plot(
                "result_peak_flow_plot",
                height=f"{_before_after_plot_height(data, 'Peak Flow')}px",
            ),
            class_="result-plot-compact",
        )

    @output
    @render.plot
    def result_peak_flow_plot():
        return make_subbasin_before_after_chart(before_after_plot_data(), "Peak Flow")

    @output
    @render.ui
    def result_tss_plot_container() -> Any:
        """TSS before/after plot, including incidental reductions."""

        data = before_after_plot_data()
        available = set(data["Parameter"].astype(str)) if not data.empty else set()
        if "TSS" not in available:
            return ui.div(
                "TSS results are not available for the current input data.",
                class_="alert alert-info",
            )
        return ui.div(
            ui.output_plot(
                "result_tss_plot",
                height=f"{_before_after_plot_height(data, 'TSS')}px",
            ),
            class_="result-plot-compact",
        )

    @output
    @render.plot
    def result_tss_plot():
        return make_subbasin_before_after_chart(before_after_plot_data(), "TSS")

    @output
    @render.ui
    def si_reduction_clipboard() -> Any:
        return _clipboard_payload_ui(
            si_reduction_data(),
            "si_reduction_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def si_reduction_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(si_reduction_data(), 4), filters=False, height="430px")

    @output
    @render.ui
    def treatment_performance_clipboard() -> Any:
        return _clipboard_payload_ui(
            treatment_performance_data(),
            "treatment_performance_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def treatment_performance_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(treatment_performance_data(), 4), filters=False, height="520px")

    @output
    @render.ui
    def footprint_clipboard() -> Any:
        return _clipboard_payload_ui(
            footprint_data(),
            "footprint_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def footprint_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(footprint_data(), 4), filters=False, height="470px")

    @output
    @render.ui
    def cost_by_subbasin_plot_container() -> Any:
        """Scale the browser plot height to the treated-subbasin cost rows."""

        data = cost_effectiveness_data()
        count = 0 if data is None or data.empty else min(len(data), 15)
        height_px = max(300, min(740, 175 + 34 * count))
        return ui.div(
            ui.output_plot("cost_by_subbasin_plot", height=f"{height_px}px"),
            class_="result-plot-compact",
        )

    @output
    @render.plot
    def cost_by_subbasin_plot():
        return make_cost_by_subbasin_chart(cost_effectiveness_data())

    @output
    @render.ui
    def cost_effectiveness_clipboard() -> Any:
        return _clipboard_payload_ui(
            cost_effectiveness_data(),
            "cost_effectiveness_clipboard_payload",
            4,
        )

    @output
    @render.data_frame
    def cost_effectiveness_table() -> render.DataGrid:
        return render.DataGrid(round_numeric_columns(cost_effectiveness_data(), 4), filters=False, height="470px")

    @render.download(filename="sample_subbasins.csv")
    def download_template():
        """Download the packaged example/template shown in Step 1."""
        yield (DATA_DIR / "sample_subbasins.csv").read_bytes()

    @render.download(filename="iBMP_Solver_report.pdf")
    def download_report_pdf():
        """Download a report-style PDF containing the visible Results sections.

        The report receives already-calculated result tables and chart helpers.
        It does not rerun or alter the optimization model.
        """

        figures: list[tuple[str, Any]] = []
        try:
            # Keep the report consistent with the Results page: Peak Flow and
            # TSS are shown whenever they are present in the input/reporting
            # data, even when TSS was not selected as an optimization target.
            plot_data = before_after_plot_data()
            available_parameters = (
                set(plot_data["Parameter"].astype(str))
                if plot_data is not None and not plot_data.empty
                else set()
            )
            for parameter in ["Peak Flow", "TSS"]:
                if parameter in available_parameters:
                    figures.append(
                        (
                            f"Before vs after by treated subbasin - {parameter}",
                            make_subbasin_before_after_chart(plot_data, parameter),
                        )
                    )

            figures.append(("Cost by treated subbasin", make_cost_by_subbasin_chart(cost_effectiveness_data())))

            report_tables = [
                ("Watershed target performance", round_numeric_columns(watershed_performance_data(), 4)),
                ("BMP allocation and cost summary", round_numeric_columns(allocation_cost_data(), 4)),
                ("Subbasin reduction summary", round_numeric_columns(subbasin_reduction_data(), 4)),
                ("International-unit summary", round_numeric_columns(si_reduction_data(), 4)),
                ("BMP treatment performance", round_numeric_columns(treatment_performance_data(), 4)),
                ("BMP footprint", round_numeric_columns(footprint_data(), 4)),
            ]

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                build_optistorm_pdf_report(
                    tmp.name,
                    summary_groups=results_input_summary_groups(),
                    tables=report_tables,
                    figures=figures,
                )
                yield Path(tmp.name).read_bytes()
        finally:
            for _, fig in figures:
                try:
                    plt.close(fig)
                except Exception:
                    pass

    @render.download(filename="iBMP_Solver_results.xlsx")
    def download_results():
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            report_tables = {
                "Allocation_Cost": allocation_cost_data(),
                "Watershed_Performance": watershed_performance_data(),
                "Subbasin_Reductions": subbasin_reduction_data(),
                "SI_Reductions": si_reduction_data(),
                "Treatment_Performance": treatment_performance_data(),
                "BMP_Footprint": footprint_data(),
                "Cost_Effectiveness": cost_effectiveness_data(),
            }
            export_solution_excel(solution(), tmp.name, report_tables=report_tables)
            yield Path(tmp.name).read_bytes()

app = App(app_ui, server)
