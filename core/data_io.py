"""Load project data from data/sheets/ only.

Layout
------
data/sheets/
  ETF_List.csv
  Option_Book_Copy.csv
  Underlying_Market_Copy.csv
  holdings/<TICKER>.csv

Do not read or write data/Project10_ETF_List.xlsx from code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Union

import numpy as np
import pandas as pd

from .greek_engine import MarketData

PathLike = Union[str, Path]

ROOT = Path(__file__).resolve().parents[1]
SHEETS_DIR = ROOT / "data" / "sheets"
HOLDINGS_DIR = SHEETS_DIR / "holdings"

UNDERLYING_ALIASES = {
    "SPY US": "SPY",
    "QQQ US": "QQQ",
    "SPX Index": "SPX",
    "NDX Index": "NDX",
}


def normalize_underlying(name: str) -> str:
    if not isinstance(name, str):
        return name
    name = name.strip()
    if name in UNDERLYING_ALIASES:
        return UNDERLYING_ALIASES[name]
    if name.endswith(" US"):
        return name[:-3]
    return name


def _require_sheets_dir(path: PathLike = SHEETS_DIR) -> Path:
    path = Path(path)
    if not path.is_dir():
        raise FileNotFoundError(f"sheets directory not found: {path}")
    return path


def load_etf_list(sheets_dir: PathLike = SHEETS_DIR) -> pd.DataFrame:
    """Universe + AUM from ETF_List.csv."""
    sheets_dir = _require_sheets_dir(sheets_dir)
    raw = pd.read_csv(sheets_dir / "ETF_List.csv")
    return pd.DataFrame({
        "fund": raw["Bloomberg ID"].astype(str).str.strip(),
        "ticker": raw["Ticker"].astype(str).str.strip(),
        "name": raw["Fund Name"],
        "aum_usd": pd.to_numeric(raw["AUM (USD mn)"], errors="coerce") * 1_000_000.0,
        "bucket": raw["Strategy (preliminary)"],
        "underlying_note": raw["Underlying (preliminary)"],
        "aum_source": raw.get("Source / Status", "data/sheets/ETF_List.csv"),
    })


def _load_fund_positions(ticker: str, holdings_dir: Path) -> pd.DataFrame:
    path = holdings_dir / f"{ticker}.csv"
    if not path.exists():
        raise FileNotFoundError(f"holdings file missing: {path}")
    raw = pd.read_csv(path)
    raw = raw.rename(columns={
        "ID": "holding_id",
        "id().position.POSITION": "position",
        "SECURITY_TYP": "security_type",
    })
    is_option = raw["security_type"].astype(str).str.contains("Option", case=False, na=False)
    out = raw.loc[is_option, ["holding_id", "position"]].copy()
    out["position"] = pd.to_numeric(out["position"], errors="coerce").fillna(0.0)
    out["fund_ticker"] = ticker
    return out


def load_holdings(sheets_dir: PathLike = SHEETS_DIR, options_only: bool = True) -> pd.DataFrame:
    """Join Option_Book_Copy terms with holdings/<TICKER>.csv positions."""
    sheets_dir = _require_sheets_dir(sheets_dir)
    holdings_dir = sheets_dir / "holdings"
    etfs = load_etf_list(sheets_dir)
    ticker_to_fund = dict(zip(etfs["ticker"], etfs["fund"]))

    book = pd.read_csv(sheets_dir / "Option_Book_Copy.csv")
    book["ETF_Ticker"] = book["ETF_Ticker"].ffill()
    book = book.rename(columns={
        "ETF_Ticker": "ticker",
        "Option Security ID": "holding_id",
        "Underlying": "underlying",
        "Call / Put": "cp",
        "Strike": "strike",
        "Expiry": "expiry",
        "Contract Size": "multiplier",
        "IV_MID": "iv_pct",
    })

    position_frames = []
    for ticker in book["ticker"].dropna().unique():
        if ticker not in ticker_to_fund:
            continue
        path = holdings_dir / f"{ticker}.csv"
        if not path.exists():
            continue
        position_frames.append(_load_fund_positions(ticker, holdings_dir))

    if not position_frames:
        raise ValueError(f"no holdings CSVs found under {holdings_dir}")
    positions = pd.concat(position_frames, ignore_index=True)

    merged = book.merge(
        positions,
        left_on=["holding_id", "ticker"],
        right_on=["holding_id", "fund_ticker"],
        how="left",
    )
    merged["fund"] = merged["ticker"].map(ticker_to_fund)
    merged["position"] = merged["position"].fillna(0.0)
    merged["underlying"] = merged["underlying"].map(normalize_underlying)
    merged["cp"] = merged["cp"].astype(str).str.upper().str[0]
    merged["expiry"] = pd.to_datetime(merged["expiry"], errors="coerce")
    merged["strike"] = pd.to_numeric(merged["strike"], errors="coerce")
    merged["multiplier"] = pd.to_numeric(merged["multiplier"], errors="coerce").fillna(100.0)
    iv_pct = pd.to_numeric(merged["iv_pct"], errors="coerce")
    merged["iv"] = iv_pct / 100.0
    merged["units"] = "contracts"
    merged["source"] = "observed"
    merged["is_option"] = True

    cols = [
        "fund", "ticker", "holding_id", "underlying", "expiry", "cp", "strike",
        "position", "units", "multiplier", "iv", "source", "is_option",
    ]
    out = merged[cols].dropna(subset=["fund", "underlying", "expiry", "cp", "strike"])
    return out.reset_index(drop=True)


def load_market(
    sheets_dir: PathLike = SHEETS_DIR,
    rate: float = 0.04,
    default_vol: float = 0.20,
) -> MarketData:
    """Spots and dividend yields from Underlying_Market_Copy.csv."""
    sheets_dir = _require_sheets_dir(sheets_dir)
    raw = pd.read_csv(sheets_dir / "Underlying_Market_Copy.csv", header=None)
    header = [str(v).strip() if pd.notna(v) else "" for v in raw.iloc[0].tolist()]

    def col(name: str) -> int:
        if name not in header:
            raise KeyError(f"{name!r} not in Underlying_Market_Copy header: {header}")
        return header.index(name)

    valuation_date = pd.Timestamp("2026-11-20").date()
    if "Valuation Date" in header:
        date_col = col("Valuation Date") + 1
        if date_col < len(raw.columns):
            raw_date = raw.iloc[0, date_col]
            # CSV may hold an Excel serial (e.g. 46346) or an ISO string.
            serial = pd.to_numeric(raw_date, errors="coerce")
            if np.isfinite(serial) and serial > 20000:
                valuation_date = (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(serial))).date()
            else:
                valuation_date = pd.Timestamp(raw_date).date()

    underlying_col = col("Underlying")
    spot_col = col("PX_LAST (BDH)")
    div_col = col("Dividend Yield (to collect)") if "Dividend Yield (to collect)" in header else None

    spots = {}
    div_yields = {}
    for _, row in raw.iloc[1:].iterrows():
        name = normalize_underlying(str(row[underlying_col]))
        if name in ("", "nan", "None", "Underlying"):
            continue
        spot = pd.to_numeric(row[spot_col], errors="coerce")
        if not np.isfinite(spot):
            continue
        spots[name] = float(spot)
        if div_col is None:
            div_yields[name] = 0.0
        else:
            div = pd.to_numeric(row[div_col], errors="coerce")
            div_yields[name] = float(div) / 100.0 if np.isfinite(div) else 0.0

    return MarketData(
        valuation_date=valuation_date,
        spots=spots,
        surfaces={},
        rate=rate,
        div_yields=div_yields,
        vol_mode="sticky_strike",
        use_book_iv=True,
        default_vol=default_vol,
    )


def load_project(
    sheets_dir: PathLike = SHEETS_DIR,
    rate: float = 0.04,
    default_vol: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame, MarketData]:
    """Convenience: (etf_list, observed_option_book, market) from data/sheets/."""
    sheets_dir = _require_sheets_dir(sheets_dir)
    return (
        load_etf_list(sheets_dir),
        load_holdings(sheets_dir),
        load_market(sheets_dir, rate=rate, default_vol=default_vol),
    )


# Placeholder inference params — kept here so we can flip `rule` back after
# prospectus calibration. Engine in infer_holdings.py is unchanged.
_ELN_PLACEHOLDER = {
    "rule": "covered_call",
    "coverage": 0.20,
    "otm_pct": 0.02,
    "frequency": "weekly",
    "ladder": 4,
}
_BUFFER_FOF_PLACEHOLDER = {
    "rule": "scale_from",
    "scale_from": "FNOV US Equity",
}


def inference_rules_from_etf_list(etf_list: pd.DataFrame) -> pd.DataFrame:
    """Inference / exclude rules for funds with no observed options.

    Until prospectuses are calibrated, ELN and buffer FoF funds are *excluded*
    (no synthetic legs). Placeholders above document the intended rules.
    """
    rows = []
    for _, etf in etf_list.iterrows():
        strategy = str(etf.get("bucket", "")).lower()
        fund = etf["fund"]
        if "eln" in strategy:
            underlying = "NDX" if "nasdaq" in str(etf.get("underlying_note", "")).lower() else "SPX"
            # Placeholder kept; do not emit inferred legs until prospectus review.
            rows.append({
                "fund": fund,
                "rule": "exclude",
                "underlying": underlying,
                "strike_step": 25 if underlying == "NDX" else 5,
                **{k: v for k, v in _ELN_PLACEHOLDER.items() if k != "rule"},
                "note": (
                    "ELN overlay — awaiting prospectus "
                    f"(placeholder: {_ELN_PLACEHOLDER})"
                ),
            })
        elif "fund of funds" in strategy or "fund of buffer" in strategy or "deep-buffer" in strategy:
            rows.append({
                "fund": fund,
                "rule": "exclude",
                **{k: v for k, v in _BUFFER_FOF_PLACEHOLDER.items() if k != "rule"},
                "note": (
                    "buffer fund-of-funds — awaiting prospectus / look-through "
                    f"(placeholder: {_BUFFER_FOF_PLACEHOLDER})"
                ),
            })
        elif "autocallable" in strategy:
            rows.append({
                "fund": fund,
                "rule": "exclude",
                "note": "autocallable barrier — not modelled yet",
            })
    return pd.DataFrame(rows)


def fetch_holdings_bql(funds: Iterable[str]) -> pd.DataFrame:  # pragma: no cover
    """Pull holdings from Bloomberg inside BQuant. Needs a terminal; untested here."""
    import bql

    service = bql.Service()
    universe = service.univ.holdings(list(funds))
    request = bql.Request(
        universe,
        {"name": service.data.name(), "position": service.data.id().position()},
    )
    result = bql.combined_df(service.execute(request)).reset_index()
    result = result.rename(columns={"ORIG_IDS": "fund", "ID": "holding_id"})
    return result[["fund", "holding_id", "name", "position"]]
