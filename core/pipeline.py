"""Build one option book from observed holdings in data/sheets/.

Only funds with listed option holdings are priced. Funds that need prospectus
inference later are marked skipped in the coverage table (no synthetic legs).
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

import pandas as pd

from . import data_io
from .greek_engine import MarketData

PathLike = Union[str, Path]


def build_universe_book(
    etf_list: pd.DataFrame,
    holdings: Union[pd.DataFrame, PathLike],
    market: MarketData,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (option_book, coverage) using observed holdings only.

    status:
      observed — fund has option legs in Option_Book_Copy + holdings/
      skipped  — no holdings available yet (infer from prospectus later)
    """
    if isinstance(holdings, (str, Path)):
        observed = data_io.load_holdings(holdings)
    else:
        observed = holdings

    bucket_by_fund = (
        dict(zip(etf_list["fund"], etf_list["bucket"]))
        if "bucket" in etf_list.columns else {}
    )

    books = []
    coverage_rows = []

    for _, etf in etf_list.iterrows():
        fund = etf["fund"]
        fund_holdings = observed[observed["fund"] == fund]

        if fund_holdings.empty:
            coverage_rows.append((
                fund, "skipped", 0,
                "no observed option holdings — defer to prospectus inference",
            ))
            continue

        books.append(fund_holdings)
        coverage_rows.append((fund, "observed", len(fund_holdings), ""))

    coverage = pd.DataFrame(coverage_rows, columns=["fund", "status", "n_legs", "note"])
    coverage = coverage.merge(etf_list, on="fund", how="left")

    book = pd.concat(books, ignore_index=True) if books else pd.DataFrame()
    if not book.empty and bucket_by_fund:
        book = book.copy()
        book["bucket"] = book["fund"].map(bucket_by_fund)

    return book, coverage


def build_from_sheets(
    sheets_dir: PathLike = data_io.SHEETS_DIR,
    rate: float = 0.04,
    default_vol: float = 0.20,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, MarketData]:
    """One-shot: load data/sheets/ → (book, coverage, etf_list, market)."""
    etf_list, observed, market = data_io.load_project(
        sheets_dir, rate=rate, default_vol=default_vol
    )
    book, coverage = build_universe_book(etf_list, observed, market)
    return book, coverage, etf_list, market
