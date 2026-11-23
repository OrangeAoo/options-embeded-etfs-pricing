"""One-way export: read Project10_ETF_List.xlsx → write data/sheets/*.csv.

The xlsx is opened read-only and never written. Run only when refreshing
Bloomberg snapshots into the working CSV copies.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "data" / "Project10_ETF_List.xlsx"
OUT = ROOT / "data" / "sheets"
HOLDINGS = OUT / "holdings"

# Bloomberg-formula sheets — `#NAME?` outside a terminal; engine uses *_Copy only.
SKIP_SHEETS = frozenset({"Option_Book", "Underlying_Market"})


def main() -> None:
    if not XLSX.exists():
        raise FileNotFoundError(XLSX)

    OUT.mkdir(parents=True, exist_ok=True)
    HOLDINGS.mkdir(exist_ok=True)

    etf = pd.read_excel(XLSX, sheet_name="ETF_List")
    fund_tickers = set(etf["Ticker"].astype(str).str.strip())

    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    exported, skipped = [], []

    for name in wb.sheetnames:
        if name in SKIP_SHEETS:
            skipped.append(name)
            continue
        rows = list(wb[name].iter_rows(values_only=True))
        nonempty = [r for r in rows if any(c is not None and str(c).strip() != "" for c in r)]
        if len(nonempty) <= 1 and (name.isdigit() or name.startswith("Sheet")):
            skipped.append(name)
            continue

        df = pd.DataFrame(rows).dropna(axis=0, how="all").dropna(axis=1, how="all")
        path = (HOLDINGS / f"{name}.csv") if name in fund_tickers else (OUT / f"{name.replace('/', '-')}.csv")
        df.to_csv(path, index=False, header=False)
        exported.append(path.name)

    wb.close()
    print(f"exported {len(exported)} sheets → {OUT}")
    print("skipped placeholders:", skipped)


if __name__ == "__main__":
    main()
