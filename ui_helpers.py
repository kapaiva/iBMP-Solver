"""Reusable Shiny UI helpers"""
from typing import Any

from shiny import ui

from data_processing import (
    COBENEFIT_CATEGORIES,
    cobenefit_score_input_id,
    cobenefit_weight_input_id,
)

NAV_SCROLL_ONCLICK = "window.scrollTo({top: 0, left: 0, behavior: 'auto'});"

def cobenefit_score_table_ui(
    criteria: list[str],
    bmp_names: list[str],
    default_weights: dict[str, float],
    default_scores: dict[str, dict[str, float]],
) -> Any:
    """Create the compact editable Step 4 co-benefit matrix.

    The matrix is generated from the *active* database source.  This makes both
    co-benefit rows and BMP columns dynamic after a validated custom database
    package is activated for the current Shiny session.
    """

    header_cells = [
        ui.tags.th("Category"),
        ui.tags.th("Co-benefit name"),
        ui.tags.th("Weight"),
    ]
    header_cells.extend(ui.tags.th(name, class_="bmp-score-header") for name in bmp_names)

    body_rows = []
    for criterion in criteria:
        category = COBENEFIT_CATEGORIES.get(criterion, "User-defined")
        weight_input = ui.input_numeric(
            cobenefit_weight_input_id(criterion),
            label="",
            value=float(default_weights.get(criterion, 1.0)),
            min=0,
            max=5,
            step=0.1,
        )

        row_cells = [
            ui.tags.td(category, class_="category-name"),
            ui.tags.td(criterion, class_="criterion-name"),
            ui.tags.td(weight_input, class_="weight-cell"),
        ]

        for bmp_name in bmp_names:
            default_score = float(default_scores.get(criterion, {}).get(bmp_name, 0.0))
            score_input = ui.input_numeric(
                cobenefit_score_input_id(criterion, bmp_name),
                label="",
                value=default_score,
                min=0,
                max=5,
                step=0.1,
            )
            row_cells.append(ui.tags.td(score_input, class_="score-cell"))

        body_rows.append(ui.tags.tr(*row_cells))

    return ui.div(
        ui.tags.table(
            ui.tags.thead(ui.tags.tr(*header_cells)),
            ui.tags.tbody(*body_rows),
            class_="cobenefit-score-table",
        ),
        class_="cobenefit-score-table-wrapper",
    )
