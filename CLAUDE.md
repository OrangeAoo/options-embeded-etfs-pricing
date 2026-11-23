# Project memory (Claude / Cursor)

## Data: only `data/sheets/`

- Runtime inputs live under `data/sheets/`:
  - `ETF_List.csv`
  - `Option_Book_Copy.csv`
  - `Underlying_Market_Copy.csv`
  - `holdings/<TICKER>.csv`
- Loaders: `core/data_io.py`, entry point `pipeline.build_from_sheets()`.
- Do **not** read or write `data/Project10_ETF_List.xlsx` from code. It is a frozen export only.
- Demos: `examples/pipeline_demo.ipynb`, `examples/universe_overview.ipynb`.

## Stack

- A = observed data (`data_io`, `pipeline`) — funds without holdings are `skipped`
- C = prospectus inference (`infer_holdings`) — deferred; not wired into the book yet
- B = Greeks / BSM (`greek_engine`, `vol_surface`) — prefer `greek_engine.price_book` etc.
- D = impact stub (`impact`) — SPX/NDX shock → MM $gamma / $vega
