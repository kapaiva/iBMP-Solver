# iBMP Solver

Shiny for Python decision-support tool for cost-effective and co-benefit-informed BMP allocation in urban watersheds.

## Workflow

* **Welcome page** — overview of iBMP Solver and the optimization workflow.
* **Optional Setup — BMP Databases** — use bundled databases or validate a custom database ZIP.
1. **Input Data** — upload a manual CSV or SWMM `.rpt` + `.inp` files.
2. **BMP Preferences** — apply BMP/subbasin exclusions, pair restrictions, and implementation limits.
3. **Scenario Targets** — define event/storm durations and reduction targets.
4. **Cost \& Objective** — select ENR city/year, life-cycle assumptions, and optimization objective.
5. **Run Optimization** — solve the BMP allocation model and optionally inspect the solver audit.
6. **Results** — review tables/charts and download Excel or PDF results.

## Database architecture

Bundled databases live in `data/`. A custom ZIP is extracted to a temporary session folder, validated by `database\_validation.py`, and activated only when every file-level and cross-file check passes. User uploads never overwrite the bundled databases.

Required model databases:

* `BMP\_types.csv`
* `BMP\_Efficiencies.csv`
* `BMP\_Cobenefits.csv`
* `Cost\_Database.csv`
* `decay\_rates.csv`
* `ENR\_CCI.csv`
* `Infiltration-based-bmp.csv`
* `Storaged-based-bmp.csv`

`sample\_subbasins.csv` is only a Step 1 input template. See `DATABASE\_GUIDE.md` for the custom-database contract.

`No BMP` is generated internally by the optimizer and is not a user-editable database entry.

## Run locally

```powershell
pip install -r requirements.txt
shiny run --reload app.py
```

Then open `http://127.0.0.1:8000`.

## Main modules

* `app.py` — Shiny server/reactive workflow and application assembly
* `ui\_pages.py` — page layouts, workflow shell, cards, and CSS
* `ui\_helpers.py` — reusable Shiny UI builders and navigation constants
* `data\_processing.py` — SWMM/manual-input parsing, database loading, target calculations, and co-benefit scoring
* `database\_validation.py` — custom database validation and normalization
* `cost\_module.py` — ENR adjustment and BMP life-cycle unit costs
* `solver\_matrix.py` — engineering coefficients and optimization matrix preparation
* `optimizer.py` — PuLP allocation model
* `results.py` — result tables, charts, Excel export, and PDF report generation

The scientific/optimization logic is unchanged from the cleaned working build; Version 8 reorganizes presentation and parsing helpers so `app.py` is focused on Shiny reactive wiring.

