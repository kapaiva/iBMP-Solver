# iBMP Solver custom database package

Custom databases are optional. The bundled `data/` folder is the default source. A custom ZIP is extracted and validated in a temporary Shiny-session folder and becomes active only if **all** checks pass.

The ZIP must contain these eight CSV files (at the ZIP root or in a single `data/` folder):

* `BMP\_types.csv`
* `BMP\_Efficiencies.csv`
* `BMP\_Cobenefits.csv`
* `Cost\_Database.csv`
* `decay\_rates.csv`
* `ENR\_CCI.csv`
* `Infiltration-based-bmp.csv`
* `Storaged-based-bmp.csv`

## BMP\_types.csv

Exactly two columns: `BMP Name`, `Type`.

* BMP names must be nonblank and unique.
* Any BMP name is allowed except the reserved `No BMP` name.
* `Type` must be exactly `Storage` or `Infiltration`.
* This file is the BMP registry. Every real BMP listed here must appear consistently in all BMP-dependent databases.

## BMP\_Efficiencies.csv

Column A must be `Parameter`. The 15 parameter rows are fixed and cannot be renamed, removed, duplicated, or extended:

`Peak Flow, TSS, TP, TN, NO3, PO4, Zn, Cu, Pb, As, Cr, Ni, Fe, Fecal coliform, E. coli`.

Every remaining column is one BMP from `BMP\_types.csv`.

* Every BMP/parameter cell is required.
* Values are percentages from 0 to 100, including decimals.
* `95%` and `95` both mean 95%. A numeric `0.95` means 0.95% in the custom-database contract.

## BMP\_Cobenefits.csv

The first two columns must be `Co-benefit Name`, `Weight Scale`. Every remaining column is one BMP from `BMP\_types.csv`.

* Co-benefit names must be nonblank and unique.
* New co-benefit rows are allowed.
* `Weight Scale` must be numeric from 0 to 5.
* Every BMP score must be numeric from 0 to 5; decimals are allowed.
* No cells may be missing inside the active co-benefit × BMP score matrix.
* New BMP columns are allowed, but the BMP must also be defined in every other BMP-dependent database.

## Cost\_Database.csv

Exactly these columns:

`BMP\_Name, Construction-Cost\_usdpercf, OM\_Cost\_usdpercf, Default\_area\_sqf, Default\_depth\_ft, Cost\_reference\_City, Cost\_reference\_Year`

* Exactly one row for every real BMP in `BMP\_types.csv`.
* Construction cost > 0.
* O\&M cost >= 0.
* Default area > 0.
* Default depth > 0.
* Reference city must exactly match a city header in `ENR\_CCI.csv`.
* Reference year must be a whole year present in `ENR\_CCI.csv`.

## decay\_rates.csv

Column A must be `Parameter`. The 14 pollutant rows are fixed:

`TSS, TP, TN, NO3, PO4, Zn, Cu, Pb, As, Cr, Ni, Fe, Fecal coliform, E. coli`.

Peak Flow is intentionally absent because it does not use a decay-rate equation.

* Every real BMP is one column.
* Values must be positive.
* Exception: `TSS` may be blank for BMPs classified as `Storage`, because storage BMP TSS uses the settling equation instead of the decay-rate equation.

## ENR\_CCI.csv

Column A must be `Year`. Remaining columns are city names.

* Years must be unique whole numbers.
* Existing default years cannot be removed.
* Additional years must be later than the current maximum default year. The packaged database currently ends at 2026, so a new row starts at 2027 or later.
* New city columns are allowed.
* Every year × city cell must contain a positive number.

## Infiltration-based-bmp.csv

Exactly these columns:

`BMP\_Name, v\_sqms, gravel moisture\_in, gravel depth\_in, soil moisture, soil depth\_in, pondind depth\_in`

Only BMPs classified as `Infiltration` in `BMP\_types.csv` belong here.

* `v\_sqms` is the database field used for the infiltration-rate input and must be > 0.
* Gravel and soil moisture must be between 0 and 1.
* Gravel depth, soil depth, and ponding depth must be >= 0.
* Every infiltration BMP must appear exactly once.

## Storaged-based-bmp.csv

Exactly these columns:

`BMP\_Name, Dp\_mm, SG, v\_sqms`

Only BMPs classified as `Storage` in `BMP\_types.csv` belong here.

* `Dp\_mm` > 0.
* `SG` > 1.
* `v\_sqms` > 0.
* Every storage BMP must appear exactly once.

## Cross-file rule

Let the BMP names in `BMP\_types.csv` be the registry. That exact real-BMP set must also be represented in:

* `Cost\_Database.csv`
* BMP columns of `BMP\_Efficiencies.csv`
* BMP columns of `BMP\_Cobenefits.csv`
* BMP columns of `decay\_rates.csv`

The registry must also equal the union of storage and infiltration property-table names, with no BMP appearing in both property tables.

