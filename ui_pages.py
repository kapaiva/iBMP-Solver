"""Static Shiny page layout for iBMP Solver.

This module contains only page composition and styling. Reactive behavior lives in
``app.py``.
"""
from shiny import ui

from ui_helpers import NAV_SCROLL_ONCLICK

app_ui = ui.page_fluid(
    # -------------------------------------------------------------------------
    # GLOBAL PAGE STYLING
    # -------------------------------------------------------------------------
    # Keep visual styling here so the user can learn where to change widths,
    # spacing, typography, buttons, and card appearance without touching the
    # optimization logic below.
    ui.tags.head(
        ui.tags.script(
            """
            async function optiCopyResultsTable(payloadId, buttonId) {
                const payload = document.getElementById(payloadId);
                const button = document.getElementById(buttonId);

                if (!payload) {
                    console.error("Clipboard payload not found:", payloadId);
                    return;
                }

                const text = (payload.value !== undefined) ? payload.value : payload.textContent;

                async function copyWithFallback(value) {
                    if (navigator.clipboard && window.isSecureContext) {
                        await navigator.clipboard.writeText(value);
                        return;
                    }

                    const temp = document.createElement("textarea");
                    temp.value = value;
                    temp.setAttribute("readonly", "");
                    temp.style.position = "fixed";
                    temp.style.opacity = "0";
                    document.body.appendChild(temp);
                    temp.select();
                    const copied = document.execCommand("copy");
                    document.body.removeChild(temp);
                    if (!copied) {
                        throw new Error("Browser copy command failed.");
                    }
                }

                try {
                    await copyWithFallback(text || "");
                    if (button) {
                        if (!button.dataset.originalLabel) {
                            button.dataset.originalLabel = button.textContent;
                        }
                        button.textContent = "Copied!";
                        button.classList.add("result-copy-success");
                        window.setTimeout(() => {
                            button.textContent = button.dataset.originalLabel || "Copy table";
                            button.classList.remove("result-copy-success");
                        }, 1600);
                    }
                } catch (error) {
                    console.error("Could not copy results table:", error);
                    if (button) {
                        if (!button.dataset.originalLabel) {
                            button.dataset.originalLabel = button.textContent;
                        }
                        button.textContent = "Copy failed";
                        window.setTimeout(() => {
                            button.textContent = button.dataset.originalLabel || "Copy table";
                        }, 1800);
                    }
                }
            }
            """
        ),
        ui.tags.style(
            """
            body {
                background: #f6f8fa;
            }

            .optistorm-page {
                max-width: 980px;
                margin: 0 auto;
                padding: 42px 22px 56px 22px;
            }

            .optistorm-wide-page {
                max-width: 1500px;
                margin: 0 auto;
                padding: 28px 22px 48px 22px;
            }

            .optistorm-results-page {
                max-width: 1180px;
                margin: 0 auto;
                padding: 42px 22px 56px 22px;
            }

            .duration-input-row {
                display: grid;
                grid-template-columns: minmax(0, 1fr) 190px;
                gap: 22px;
                align-items: center;
                padding: 10px 0;
                border-bottom: 1px solid #eef2f7;
            }

            .duration-input-row:last-child {
                border-bottom: none;
            }

            .duration-input-label {
                font-weight: 600;
                color: #334155;
                line-height: 1.4;
            }

            .duration-input-control .form-group {
                margin-bottom: 0 !important;
            }

            .duration-input-control label {
                display: none !important;
            }

            .solver-status-box {
                margin-top: 18px;
                padding: 12px 14px;
                border-radius: 10px;
                background: #f8fafc;
                border: 1px solid #e5e7eb;
            }

            @media (max-width: 700px) {
                .duration-input-row {
                    grid-template-columns: 1fr;
                    gap: 8px;
                }
            }

            .welcome-shell {
                max-width: 850px;
                margin: 9vh auto 0 auto;
                padding: 52px 46px;
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 18px;
                box-shadow: 0 10px 35px rgba(15, 23, 42, 0.07);
            }

            .welcome-kicker,
            .step-kicker {
                font-size: 0.78rem;
                font-weight: 700;
                letter-spacing: 0.10em;
                text-transform: uppercase;
                color: #64748b;
                margin-bottom: 8px;
            }

            .welcome-title {
                font-size: clamp(1.9rem, 4vw, 3.6rem);
                font-weight: 750;
                letter-spacing: -0.045em;
                margin-bottom: 12px;
                color: #172033;
                white-space: nowrap;
            }

            .welcome-subtitle {
                font-size: 1.2rem;
                line-height: 1.65;
                color: #475569;
                margin-bottom: 30px;
                max-width: 720px;
            }

            .welcome-secondary {
                margin-top: 24px;
                color: #64748b;
                line-height: 1.7;
            }

            .optistorm-page-title {
                font-size: 2rem;
                font-weight: 720;
                letter-spacing: -0.025em;
                color: #172033;
                margin-bottom: 8px;
            }

            .section-title {
                font-size: 1.25rem;
                font-weight: 700;
                color: #1f2937;
                margin-top: 28px;
                margin-bottom: 8px;
            }

            .section-help {
                color: #64748b;
                line-height: 1.65;
                max-width: 820px;
                margin-bottom: 18px;
            }

            .clean-card {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 14px;
                padding: 24px;
                margin-bottom: 20px;
                box-shadow: 0 3px 14px rgba(15, 23, 42, 0.035);
            }

            .workflow-button-row {
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
                margin-top: 24px;
            }

            .next-step-btn {
                min-width: 150px;
                font-weight: 650 !important;
            }

            .text-link-btn {
                background: transparent !important;
                border: none !important;
                color: #0d6efd !important;
                padding: 0 3px !important;
                box-shadow: none !important;
                vertical-align: baseline;
                text-decoration: underline;
            }

            .source-box {
                margin-top: 16px;
                padding: 20px;
                background: #fbfcfe;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
            }

            .restriction-summary-note {
                color: #64748b;
                font-size: 0.92rem;
            }

            /* Compact editable co-benefit matrix. */
            .cobenefit-score-table-wrapper {
                overflow-x: auto;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                padding: 8px;
                background: #ffffff;
            }

            .cobenefit-score-table {
                border-collapse: collapse;
                min-width: 1260px;
                font-size: 0.76rem;
                table-layout: fixed;
            }

            .cobenefit-score-table th,
            .cobenefit-score-table td {
                border: 1px solid #dee2e6;
                padding: 2px 3px;
                text-align: center;
                vertical-align: middle;
            }

            .cobenefit-score-table th {
                background: #f8f9fa;
                font-weight: 700;
                line-height: 1.05;
                white-space: normal;
            }

            .cobenefit-score-table th.bmp-score-header {
                width: 72px;
                min-width: 72px;
                max-width: 72px;
            }

            .cobenefit-score-table td.criterion-name {
                text-align: left;
                width: 185px;
                min-width: 185px;
            }

            .cobenefit-score-table td.category-name {
                text-align: left;
                width: 88px;
                min-width: 88px;
            }

            .cobenefit-score-table td.weight-cell,
            .cobenefit-score-table td.score-cell {
                width: 62px;
                min-width: 62px;
            }

            .cobenefit-score-table .form-group {
                margin-bottom: 0 !important;
            }

            .cobenefit-score-table input {
                width: 54px !important;
                min-width: 54px !important;
                padding: 2px 3px;
                font-size: 0.76rem;
            }

            /* -------------------------------------------------------------
               LEFT WORKFLOW SIDEBAR
               Appears only after the Welcome page. Steps unlock in sequence
               as the user successfully completes the required prior page.
               ------------------------------------------------------------- */
            .workflow-layout {
                display: flex;
                align-items: flex-start;
                width: 100%;
            }

            .workflow-main {
                flex: 1 1 auto;
                min-width: 0;
            }

            .workflow-sidebar {
                position: sticky;
                top: 0;
                width: 225px;
                min-width: 225px;
                min-height: 100vh;
                padding: 34px 16px 24px 16px;
                background: #ffffff;
                border-right: 1px solid #e5e7eb;
            }

            .workflow-sidebar-title {
                font-size: 0.76rem;
                text-transform: uppercase;
                letter-spacing: 0.09em;
                font-weight: 750;
                color: #64748b;
                margin: 0 8px 14px 8px;
            }

            .workflow-sidebar .btn {
                width: 100%;
                text-align: left;
                border: 0;
                border-radius: 9px;
                margin-bottom: 5px;
                padding: 10px 11px;
                box-shadow: none !important;
                background: transparent;
                color: #475569;
                font-size: 0.92rem;
            }

            .workflow-sidebar .btn:hover {
                background: #f1f5f9;
            }

            .workflow-sidebar .sidebar-current {
                background: #eaf2ff !important;
                color: #0d5bd7 !important;
                font-weight: 700;
            }

            .workflow-sidebar fieldset {
                padding: 0;
                margin: 0;
                border: 0;
            }

            .workflow-sidebar fieldset[disabled] .btn {
                color: #a1a8b3 !important;
                cursor: not-allowed;
                background: transparent !important;
            }

            .results-summary-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 18px;
            }

            .results-summary-group {
                border: 1px solid #e5e7eb;
                border-radius: 11px;
                padding: 16px;
                background: #fbfcfe;
            }

            .results-summary-group h5 {
                font-size: 1rem;
                font-weight: 700;
                margin: 0 0 10px 0;
                color: #1f2937;
            }

            .results-summary-row {
                display: grid;
                grid-template-columns: minmax(160px, 0.8fr) minmax(0, 1.2fr);
                gap: 12px;
                padding: 7px 0;
                border-bottom: 1px solid #edf0f4;
            }

            .results-summary-row:last-child {
                border-bottom: none;
            }

            .results-summary-label {
                font-weight: 650;
                color: #475569;
            }

            .results-summary-value {
                color: #1f2937;
                overflow-wrap: anywhere;
            }

            .results-download-row {
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                margin: 6px 0 20px 0;
            }

            .result-table-title-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
                margin-bottom: 2px;
            }

            .result-table-title-row .section-title {
                margin-bottom: 0;
            }

            .result-copy-btn {
                flex: 0 0 auto;
                white-space: nowrap;
                padding: 5px 10px;
                font-size: 0.84rem;
            }

            .result-copy-success {
                font-weight: 600;
            }

            .clipboard-payload {
                display: none !important;
            }

            .result-plot-compact {
                max-width: 860px;
                margin: 0 auto;
            }

            @media (max-width: 900px) {
                .workflow-layout {
                    display: block;
                }
                .workflow-sidebar {
                    position: static;
                    width: 100%;
                    min-width: 0;
                    min-height: 0;
                    border-right: 0;
                    border-bottom: 1px solid #e5e7eb;
                    padding: 12px 18px;
                }
                .workflow-sidebar .btn {
                    width: auto;
                    display: inline-block;
                    margin-right: 4px;
                }
                .results-summary-grid {
                    grid-template-columns: 1fr;
                }
            }
            """
        )
    ),

    # =========================================================================
    # QUALTRICS-STYLE HIDDEN WORKFLOW CONTAINER
    # =========================================================================
    # Only the selected nav_panel is rendered to the user. There are no visible
    # tabs for the workflow pages. After Start, the left workflow sidebar shows
    # Welcome, the unnumbered Initial Setup page, and numbered Steps 1–6.
    # Next/Back and unlocked sidebar buttons control movement.
    ui.div(
        ui.output_ui("workflow_sidebar"),
        ui.div(
            ui.navset_hidden(
        # ---------------------------------------------------------------------
        # WELCOME PAGE
        # ---------------------------------------------------------------------
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Decision-support tool", class_="welcome-kicker"),
                ui.div("Welcome to iBMP Solver", class_="welcome-title"),
                ui.p(
                    "An interactive decision support tool for BMP allocation in urban watersheds.",
                    class_="welcome-subtitle",
                ),
                ui.p(
                    "If you want to start using iBMP Solver, select Start below.",
                ),
                ui.input_action_button(
                    "start_optistorm",
                    "Start",
                    onclick=NAV_SCROLL_ONCLICK,
                    class_="btn-primary btn-lg next-step-btn",
                ),
                #ui.div(
                #    "If you want to learn more about iBMP Solver, ",
                #    ui.input_action_button(
                #        "toggle_about_optistorm",
                #        "click here",
                #        class_="text-link-btn",
                #    ),
                #    ".",
                #    class_="welcome-secondary",
                #),
                ui.output_ui("about_optistorm_panel"),
                ui.p(
                    "Source code: ",
                    ui.a(
                        "GitHub repository",
                        href="https://github.com/kapaiva/OptiStorm", 
                        onclick="return false;",
                    ),
                    class_="welcome-secondary",
                ),
                class_="welcome-shell",
            ),
            value="welcome",
        ),

        # ---------------------------------------------------------------------
        # OPTIONAL SETUP  (BEFORE STEP 1)
        # ---------------------------------------------------------------------
        # Every user passes through this unnumbered setup screen before Step 1.
        # Most users simply select the packaged defaults. Advanced
        # users can instead download, edit, validate, and activate a custom ZIP.
        # Custom packages live only in a session-specific temporary folder; the
        # bundled data/ directory is never overwritten.
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Initial Setup", class_="step-kicker"),
                ui.div("BMP Databases", class_="optistorm-page-title"),
                ui.p(
                    "iBMP Solver includes default databases for BMP properties such as efficiencies, costs, sizing, co-benefits scores, and cost indices. If you need to add a new BMP or modify database assumptions, download the editable CSV package and upload a custom package. Otherwise, select Use default databases to continue to Step 1.",
                    class_="section-help",
                ),

                # 1) Downloadable reference/template package.
                ui.div(
                    ui.div("Database CSV format package", class_="section-title"),
                    ui.p(
                        "Download the current database package to review the required CSV formats or use it as the starting point for a customized database set.",
                        class_="section-help",
                    ),
                    ui.download_button(
                        "download_database_package",
                        "Download database CSV format package (.zip)",
                        class_="btn-outline-primary",
                    ),
                    class_="clean-card",
                ),

                # 2) Default path — intentionally presented first and most
                # prominently because it is the recommended choice.
                ui.div(
                    ui.div("Use default databases", class_="section-title"),
                    ui.p(
                        "Use the databases included with iBMP Solver. Select this option and you can continue to the next step.",
                        class_="section-help",
                    ),
                    ui.input_action_button(
                        "use_default_databases",
                        "Use default databases",
                        class_="btn-primary",
                    ),
                    class_="clean-card",
                ),

                # 3) Advanced alternative — custom packages are not activated
                # until every file-level and cross-database validation passes.
                ui.div(
                    ui.div("Upload customized database package", class_="section-title"),
                    ui.p(
                        "Upload a complete .zip package created from the CSV formats. Make sure to include all required files",
                        class_="section-help",
                    ),
                    ui.input_file(
                        "custom_database_zip",
                        "Customized database package (.zip)",
                        accept=[".zip"],
                        multiple=False,
                    ),
                    ui.input_action_button(
                        "validate_custom_databases",
                        "Use custom databases",
                        class_="btn-primary",
                    ),
                    class_="clean-card",
                ),

                # Active-source status and detailed validation messages.
                ui.output_ui("database_validation_status"),
                ui.output_ui("database_validation_details"),

                ui.div(
                    ui.input_action_button(
                        "back_from_database_setup",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    ui.input_action_button(
                        "continue_from_database_setup",
                        "Continue to Input Data",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-primary next-step-btn",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-page",
            ),
            value="database_setup",
        ),

        # ---------------------------------------------------------------------
        # STEP 1 — INPUT DATA AND USER SETTINGS
        # ---------------------------------------------------------------------
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 1", class_="step-kicker"),
                ui.div("Input Data ", class_="optistorm-page-title"),
                ui.p(
                    "Provide the watershed information to formulate the optimization problem.",
                    class_="section-help",
                ),

                ui.div(
                    ui.div("Watershed Characteristics", class_="section-title"),
                    ui.p(
                        "To start the optimization, provide subbasin characteristics and the hydrologic and water-quality parameters for your urban watershed. "
                        "Download the formatted CSV below and replace the sample values with your own data. Keep all required column headers exactly as provided. Use 0 for pollutant variables that are not available for your scenario.",
                        class_="section-help",
                    ),
                    ui.download_button(
                        "download_template",
                        "Download subbasin input template (.csv)",
                        class_="btn-outline-primary",
                    ),
                    ui.div(
                        "If you prefer to parse the information directly from SWMM .rpt and .inp files, ",
                        ui.input_action_button(
                            "use_swmm_inputs",
                            "click here",
                            class_="text-link-btn",
                        ),
                        ".",
                        class_="section-help",
                    ),
                    # The upload controls switch between manual CSV and SWMM,
                    # while the rest of Step 1 stays fixed and uncluttered.
                    ui.output_ui("step1_active_input_box"),
                    ui.input_action_button(
                        "process_step1_inputs",
                        "Process data",
                        class_="btn-primary mt-2",
                    ),
                    class_="clean-card",
                ),

                ui.div(
                    ui.div("Current subbasin input table", class_="section-title"),
                    ui.p("This table shows the subbasin characteristics and hydrologic/water-quality parameters that will be used in the optimization. If you need to make changes, return to the previous section and re-upload your data.",),
                    ui.output_ui("subbasin_status_message"),
                    ui.output_data_frame("subbasins_preview"),
                    class_="clean-card",
                ),

                # Step 1 navigation. The button remains at the lower-left of the
                # one-column page as requested.
                ui.output_ui("step1_navigation_message"),
                ui.div(
                    ui.input_action_button(
                        "back_to_database_setup_from_input",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    ui.input_action_button(
                        "next_to_bmp_preferences",
                        "Next Step",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-primary next-step-btn",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-page",
            ),
            value="input_data",
        ),

        # ---------------------------------------------------------------------
        # STEP 2 — BMP PREFERENCES AND SPATIAL CONSTRAINTS
        # ---------------------------------------------------------------------
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 2", class_="step-kicker"),
                ui.div("BMP Preferences and Spatial Constraints", class_="optistorm-page-title"),
                ui.p(
                    "All BMPs within the databases and all imported subbasins in Step 1 are eligible by default. Use next 3 sections to specify exclusions or restrictions that apply to your scenario.",
                    class_="section-help",
                ),
                ui.output_ui("bmp_preferences_status_message"),

                ui.div(
                    ui.div("BMPs not allowed in the solution", class_="section-title"),
                    ui.p(
                        "Select BMPs only when you want to exclude them from the entire optimization. Leave the field empty to allow all BMPs.",
                        class_="section-help",
                    ),
                    ui.output_ui("bmp_selection_controls"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.div("Subbasins eligible for BMP placement", class_="section-title"),
                    ui.p(
                        "Select subbasins only when you want to prevent all BMP placement there. Leave the field empty to keep all subbasins eligible for BMP placement.",
                        class_="section-help",
                    ),
                    ui.output_ui("subbasin_selection_controls"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.div("Advanced: subbasin-specific BMP restrictions", class_="section-title"),
                    ui.p(
                        "Use this only when a BMP is allowed globally but should not be available in a particular subbasin.",
                        class_="section-help",
                    ),
                    ui.output_ui("pair_restriction_subbasin_selector"),
                    ui.output_ui("pair_restriction_bmp_controls"),
                    ui.div(
                        ui.input_action_button(
                            "save_subbasin_bmp_restrictions",
                            "Save restrictions for this subbasin",
                            class_="btn-primary",
                        ),
                        ui.input_action_button(
                            "reset_selected_subbasin_restrictions",
                            "Reset selected subbasin",
                            class_="btn-outline-secondary",
                        ),
                        ui.input_action_button(
                            "clear_all_pair_restrictions",
                            "Clear all advanced restrictions",
                            class_="btn-outline-danger",
                        ),
                        class_="workflow-button-row",
                    ),
                    ui.hr(),
                    ui.h5("Restriction summary"),
                    ui.p(
                        "Global BMP exclusions, globally ineligible subbasins, and subbasin-specific BMP exclusions are summarized together below.",
                        class_="restriction-summary-note",
                    ),
                    ui.output_data_frame("restriction_summary_table"),
                    class_="clean-card",
                ),

                # Subbasin-wide cap on the sum of real-BMP allocation fractions.
                ui.div(
                    ui.div("Subbasin BMP implementation limit (H) ", class_="section-title"),
                    ui.p(
                        "The BMP implementation limit sets the maximum combined implementation fraction of BMPs allowed within each subbasin. Values range from 0 to 1, where 1 allows unrestricted allocation and lower values progressively limit BMP implementation.",
                        class_="section-help",
                    ),
                    ui.layout_columns(
                        ui.card(
                            ui.card_header("Optional: set a subbasin BMP limit"),
                            ui.input_numeric(
                                "default_taf_cap",
                                "Default H = 1.0 (no limit)",
                                value=1.0,
                                min=0.0,
                                max=1.0,
                                step=0.1,
                            ),
                            ui.output_ui("taf_cap_subbasin_selector"),
                            ui.output_ui("taf_cap_value_control"),
                            ui.layout_columns(
                                ui.input_action_button(
                                    "save_selected_taf_cap",
                                    "Save limit value",
                                    class_="btn-primary",
                                ),
                                ui.input_action_button(
                                    "reset_selected_taf_cap",
                                    "Reset to default",
                                    class_="btn-outline-secondary",
                                ),
                                col_widths=[6, 6],
                            ),
                            ui.input_action_button(
                                "clear_all_taf_cap_overrides",
                                "Clear all overrides",
                                class_="btn-outline-danger mt-2",
                            ),
                        ),
                        ui.card(
                            ui.card_header("Summary"),
                            ui.output_data_frame("taf_cap_summary_table"),
                        ),
                        col_widths=[5, 7],
                    ),
                    class_="clean-card",
                ),

                ui.div(
                    ui.input_action_button(
                        "back_to_input_data",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    ui.input_action_button(
                        "next_to_scenario_targets",
                        "Next Step",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-primary next-step-btn",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-page",
            ),
            value="bmp_preferences",
        ),

        # ---------------------------------------------------------------------
        # STEP 3 — SCENARIO TARGETS
        # ---------------------------------------------------------------------
        # Show the user-facing durations, reduction targets, and calculated targets.
        # Internal rate-conversion columns remain available to the solver but are
        # intentionally hidden from the normal interface.
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 3", class_="step-kicker"),
                ui.div("Scenario Targets", class_="optistorm-page-title"),
                ui.p(
                    "Define the event and rainfall durations for the watershed input data. Then define the watershed-scale reduction targets for the current scenario.",
                    class_="section-help",
                ),

                ui.div(
                    ui.div("Event durations", class_="section-title"),
                    ui.p(
                        "For SWMM inputs, the rainfall storm duration is automatically detected from the rainfall time series in the .inp file when available. The event duration is initially set to 24 hours and should be edited as needed to represent the input data. For CSV inputs, both values must be entered manually.",
                        class_="section-help",
                    ),
                    ui.output_ui("scenario_duration_inputs"),
                    ui.output_ui("scenario_targets_status_message"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.div("Reduction targets (%)", class_="section-title"),
                    ui.p(
                        "Enter the desired watershed-scale reduction for each available parameter. Keep a target at 0% when that parameter should not constrain the current optimization.",
                        class_="section-help",
                    ),
                    ui.output_ui("target_reduction_inputs"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.div("Calculated target table", class_="section-title"),
                    ui.p(
                        "This table shows the watershed input value and the corresponding target after applying the target reduction.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("target_criteria_table"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.input_action_button(
                        "back_to_bmp_preferences_from_targets",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    ui.input_action_button(
                        "next_to_cost_objective",
                        "Next Step",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-primary next-step-btn",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-page",
            ),
            value="scenario_targets",
        ),

        # ---------------------------------------------------------------------
        # STEP 4 — COST SETTINGS, OBJECTIVE, AND OPTIONAL CO-BENEFITS
        # ---------------------------------------------------------------------
        # Cost assumptions are processed before objective selection is
        # shown. This gives users a clear checkpoint and prevents an incomplete
        # life-cycle assumption from silently propagating into O&M calculations.
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 4", class_="step-kicker"),
                ui.div("Cost Settings and Optimization Objective", class_="optistorm-page-title"),
                ui.p(
                    "Define the cost assumptions used to calculate BMP life-cycle costs, process them, and then choose the optimization objective.",
                    class_="section-help",
                ),

                ui.div(
                    ui.div("Cost input options", class_="section-title"),
                    ui.p(
                        "BMP life cycle is required to control the present-value O&M calculation.",
                        class_="section-help",
                    ),
                    # City/year choices come from the active default or validated
                    # custom ENR database and therefore must be rendered reactively.
                    ui.output_ui("cost_database_selectors"),
                    ui.input_numeric("life_cycle_years", "BMP life cycle (years)", value=20, min=1, step=1, width="100%"),
                    ui.input_numeric("interest_rate_pct", "Interest rate (%)", value=5.0, min=0.0, step=0.25, width="100%"),
                    ui.input_numeric("land_cost_per_acre", "Land cost ($/acre)", value=0.0, min=0.0, step=1000, width="100%"),
                    ui.input_action_button(
                        "process_cost_inputs",
                        "Process cost assumptions",
                        class_="btn-primary mt-2",
                    ),
                    ui.output_ui("cost_process_status_message"),
                    class_="clean-card",
                ),

                # Generated only after the current cost assumptions have passed
                # validation and the user has clicked Process cost assumptions.
                ui.output_ui("processed_cost_calculations_panel"),

                # Objective selection is hidden until the current cost settings
                # have been processed successfully. Co-benefits appear in the
                # same page only when that objective is selected.
                ui.output_ui("objective_and_cobenefit_panel"),

                ui.div(
                    ui.input_action_button(
                        "back_to_scenario_targets",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    ui.input_action_button(
                        "next_to_solver_review",
                        "Next Step",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-primary next-step-btn",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-page",
            ),
            value="cost_objective",
        ),

        # ---------------------------------------------------------------------
        # STEP 5 — RUN OPTIMIZATION
        # ---------------------------------------------------------------------
        # Normal users see only the run control and solver status. Developer-audit
        # controls are retained in the source but hidden from deployment.
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 5", class_="step-kicker"),
                ui.div("Run Optimization", class_="optistorm-page-title"),
                ui.p(
                    "Run the configured scenario.",
                    class_="section-help",
                ),

                ui.div(
                    ui.input_action_button(
                        "run_solver",
                        "Run solver",
                        class_="btn-primary btn-lg next-step-btn",
                    ),
                    ui.div(ui.output_text("run_solver_status"), class_="solver-status-box"),
                    class_="clean-card",
                ),

                # ui.div(
                #    ui.div("Developer audit: internal solver matrix", class_="section-title"),
                #    ui.p(
                #        "This section is retained for internal model verification.",
                #        class_="section-help",
                #    ),
                #    ui.input_checkbox(
                #        "show_solver_matrix_audit",
                #        "Show internal solver matrix audit table",
                #        value=False,
                #    ),
                #    ui.output_ui("solver_matrix_audit_panel"),
                #    class_="clean-card",
                # ),

                ui.output_ui("solver_navigation_controls"),
                class_="optistorm-page",
            ),
            value="solver_review",
        ),

        # ---------------------------------------------------------------------
        # STEP 6 — RESULTS
        # ---------------------------------------------------------------------
        # Results are organized like a concise report: scenario summary,
        # watershed performance, implementation details, and downloads.
        ui.nav_panel(
            None,
            ui.div(
                ui.div("Step 6", class_="step-kicker"),
                ui.div("Check Results", class_="optistorm-page-title"),
                ui.p(
                    "Review the scenario inputs and the optimized BMP allocation, watershed performance, implementation footprint, and cost results.",
                    class_="section-help",
                ),

                ui.div(
                    ui.div("Scenario summary", class_="section-title"),
                    ui.output_ui("results_input_summary"),
                    class_="clean-card",
                ),

                ui.div(
                    ui.download_button("download_report_pdf", "Download report", class_="btn-primary"),
                    # Results workbook download is intentionally hidden for deployment.
                    # ui.download_button("download_results", "Download results workbook", class_="btn-outline-primary"),
                    class_="results-download-row",
                ),

                ui.div(
                    ui.div(
                        ui.div("Watershed target performance", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_watershed_performance_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('watershed_performance_clipboard_payload', 'copy_watershed_performance_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("watershed_performance_clipboard"),
                    ui.p(
                        "Achieved reduction report.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("watershed_performance_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div(
                        ui.div("BMP allocation and cost summary", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_allocation_cost_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('allocation_cost_clipboard_payload', 'copy_allocation_cost_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("allocation_cost_clipboard"),
                    ui.p(
                        "Maximum equivalent units, implementation factors, implemented units, and actual life-cycle cost for each selected BMP allocation.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("allocation_cost_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div(
                        ui.div("Subbasin reduction summary", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_subbasin_reduction_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('subbasin_reduction_clipboard_payload', 'copy_subbasin_reduction_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("subbasin_reduction_clipboard"),
                    ui.p(
                        "Only treated subbasins are shown. Before and after values are reconstructed by summing the solved real-BMP and No-BMP fractions within each subbasin.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("subbasin_reduction_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div("Before vs after by treated subbasin", class_="section-title"),
                    ui.p(
                        "Peak Flow and TSS reduction produced by the selected BMP allocation are shown.",
                        class_="section-help",
                    ),
                    # Each plot scales to the number of treated subbasins shown.
                    ui.output_ui("result_peak_flow_plot_container"),
                    ui.output_ui("result_tss_plot_container"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div(
                        ui.div("International-unit summary", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_si_reduction_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('si_reduction_clipboard_payload', 'copy_si_reduction_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("si_reduction_clipboard"),
                    ui.p(
                        "Peak flow is reported in m³/s. Mass pollutants are reported in 10³ kg/event; "
                        "biological pollutants retain their corresponding event-load units. All parameters "
                        "present in the Step 1 input data are reported, even when they didn't constraint the BMP allocation.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("si_reduction_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div(
                        ui.div("BMP treatment performance", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_treatment_performance_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('treatment_performance_clipboard_payload', 'copy_treatment_performance_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("treatment_performance_clipboard"),
                    ui.p(
                        "For each implemented BMP: inflow = untreated subbasin value × implementation fraction; "
                        "outflow = remaining coefficient × implementation fraction.",
                        class_="section-help",
                    ),
                    ui.output_data_frame("treatment_performance_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div(
                        ui.div("BMP footprint", class_="section-title"),
                        ui.tags.button(
                            "Copy table",
                            id="copy_footprint_btn",
                            type="button",
                            class_="btn btn-sm btn-outline-secondary result-copy-btn",
                            onclick="optiCopyResultsTable('footprint_clipboard_payload', 'copy_footprint_btn')",
                        ),
                        class_="result-table-title-row",
                    ),
                    ui.output_ui("footprint_clipboard"),
                    ui.p("Summarizes the estimated land area occupied by the selected BMPs.",class_="section-help"),
                    ui.output_data_frame("footprint_table"),
                    class_="clean-card",
                ),
                ui.div(
                    ui.div("Cost by treated subbasin", class_="section-title"),
                    # Dynamic container height prevents a three-subbasin result
                    # from occupying the same vertical space as a 15-subbasin result.
                    ui.p("Summarizes total BMP implementation costs by treated subbasin, including construction, operation and maintenance (O&M), and land costs.",class_="section-help"),
                    ui.output_ui("cost_by_subbasin_plot_container"),
                    class_="clean-card",
                ),
                # Cost-effectiveness table hidden for deployment.
                # The underlying calculation remains available for the cost-by-subbasin plot.
                # ui.div(
                #     ui.div(
                #         ui.div("Cost-effectiveness by subbasin", class_="section-title"),
                #         ui.tags.button(
                #             "Copy table",
                #             id="copy_cost_effectiveness_btn",
                #             type="button",
                #             class_="btn btn-sm btn-outline-secondary result-copy-btn",
                #             onclick="optiCopyResultsTable('cost_effectiveness_clipboard_payload', 'copy_cost_effectiveness_btn')",
                #         ),
                #         class_="result-table-title-row",
                #     ),
                #     ui.output_ui("cost_effectiveness_clipboard"),
                #     ui.output_data_frame("cost_effectiveness_table"),
                #     class_="clean-card",
                # ),
                ui.div(
                    ui.input_action_button(
                        "back_to_solver_review",
                        "Back",
                        onclick=NAV_SCROLL_ONCLICK,
                        class_="btn-outline-secondary",
                    ),
                    class_="workflow-button-row",
                ),
                class_="optistorm-results-page",
            ),
            value="results",
        ),

        id="optistorm_workflow",
        selected="welcome",
            ),
            class_="workflow-main",
        ),
        class_="workflow-layout",
    ),
)
