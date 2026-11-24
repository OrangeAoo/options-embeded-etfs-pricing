# Options-Embedded ETFs: Aggregate Dollar Gamma & Vega

Derivatives final project (Columbia MSFE, Fall 2026), with practitioner **Max Grinacoff (UBS)**.

> Compute aggregate dollar gamma and dollar vega for the largest option-embedded ETFs, and what that does to the equity vol environment.

📄 **Project doc:** [Derivatives – Project 10 (Google Doc)](https://docs.google.com/document/d/1XphPv-kb6I0xemkoTza6mu3xkfeFQ33LmR3KySt7UcE/edit)

---

## Data (only `data/sheets/`)

```
data/sheets/
  ETF_List.csv
  Option_Book_Copy.csv
  Underlying_Market_Copy.csv
  holdings/<TICKER>.csv
```

Code never reads `data/Project10_ETF_List.xlsx`. Refresh CSVs with:

```bash
python scripts/export_sheets_from_xlsx.py   # read-only from the frozen xlsx
```

## Quick start

```bash
pip install -r requirements.txt
pytest
jupyter lab examples/
```

```python
from core import pipeline, greek_engine
import numpy as np

book, coverage, etfs, market = pipeline.build_from_sheets()  # observed holdings only
priced = greek_engine.price_book(book, market)
greek_engine.aggregate(priced, by="fund")
ladder = greek_engine.spot_ladder(book, market, np.linspace(-0.1, 0.1, 21), by="bucket")
```

Funds without holdings are `skipped` in `coverage` (prospectus inference later).

## Layout

```text
./
├── data/       # universe, holdings, ...
├── core/       # BS pricing, Greeks, prospectus -> holdings, SPX/NDX shocks, IV/Greeks impact
├── examples/   # demos & visualizations (universe, pipeline, shock analysis)
└── README.md   # project & pipeline overview
```

## Conventions

| Quantity | Definition |
|---|---|
| contracts | `position / multiplier` if `units == "shares"`, else `position` |
| $Gamma per 1% | contracts × m × Γ × S² × 1% |
| $Vega per vol pt | contracts × m × Vega × 1% |
| `mm_*` | street / MM side = −ETF |
