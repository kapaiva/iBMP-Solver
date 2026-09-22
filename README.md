# iBMP Solver

Shiny for Python decision-support tool for cost-effective and co-benefit-informed BMP allocation in urban watersheds.

## Workflow

* **Welcome page** — overview of iBMP Solver and the optimization workflow.
* **Optional Setup — BMP Databases** — use bundled databases or validate a custom database ZIP.
1. **Input Data** — upload a manual CSV or SWMM `.rpt` + `.inp` files.
2. **BMP Preferences** — apply BMP/subbasin exclusions, pair restrictions, and implementation limits.
3. **Scenario Targets** — define event/storm durations and reduction targets.
4. **Cost \& Objective** — select ENR city/year, life-cycle assumptions, and optimization objective.
5. **Run Optimization** — solve the BMP allocation model.
6. **Results** — review tables/charts and download PDF results.

## Database architecture

Bundled databases live in `data/`. A custom ZIP is extracted to a temporary session folder, validated by `database\_validation.py`, and activated only when every file-level and cross-file check passes. User uploads never overwrite the bundled databases.

Required model databases:

* `BMP_types.csv`
* `BMP_Efficiencies.csv`
* `BMP_Cobenefits.csv`
* `Cost_Database.csv`
* `decay_rates.csv`
* `ENR_CCI.csv`
* `Infiltration-based-bmp.csv`
* `Storaged-based-bmp.csv`

## Run locally

```powershell
pip install -r requirements.txt
shiny run --reload app.py
```

Then open `http://127.0.0.1:8000`.

## Main modules

* `app.py` — Shiny server/reactive workflow and application assembly
* `ui_pages.py` — page layouts, workflow shell, cards, and CSS
* `ui_helpers.py` — reusable Shiny UI builders and navigation constants
* `data_processing.py` — SWMM/manual-input parsing, database loading, target calculations, and co-benefit scoring
* `database_validation.py` — custom database validation and normalization
* `cost_module.py` — ENR adjustment and BMP life-cycle unit costs
* `solver_matrix.py` — engineering coefficients and optimization matrix preparation
* `optimizer.py` — PuLP allocation model
* `results.py` — result tables, charts, Excel export, and PDF report generation
