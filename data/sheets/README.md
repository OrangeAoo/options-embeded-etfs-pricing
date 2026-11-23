# Working data (`data/sheets/`)

This folder is the **only** runtime data source for the project.

| File | Role |
|---|---|
| `ETF_List.csv` | Universe, AUM, strategy notes |
| `Option_Book_Copy.csv` | Option terms + IV_MID |
| `Underlying_Market_Copy.csv` | Spots, dividend yields, valuation date |
| `holdings/<TICKER>.csv` | BQL positions per fund |

## Rules

1. Code and notebooks read **only** from `data/sheets/`.
2. Do not point loaders at `data/Project10_ETF_List.xlsx`.
3. To refresh from Bloomberg, re-export into these CSVs (`scripts/export_sheets_from_xlsx.py` is read-only on the xlsx).
