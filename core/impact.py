"""Impact stub (workflow D): shock SPX / NDX, report MM-side dollar gamma and vega.

Street side = -ETF. OI/ADV normalization is a separate hook once liquidity data lands.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd

from .greek_engine import MarketData, aggregate, price_book

# Underlyings that move with an SPX shock vs an NDX shock.
SPX_UNDERLYINGS = frozenset({"SPX", "SPY", "XSP", "ES"})
NDX_UNDERLYINGS = frozenset({"NDX", "NDXP", "NQX", "QQQ", "NQ"})


def index_family(underlying: str) -> str:
    """Map an underlying ticker to 'SPX', 'NDX', or 'OTHER'."""
    name = underlying.upper()
    if name in SPX_UNDERLYINGS:
        return "SPX"
    if name in NDX_UNDERLYINGS:
        return "NDX"
    return "OTHER"


def shock_market(
    market: MarketData,
    spx_shock: float = 0.0,
    ndx_shock: float = 0.0,
    other_shock: float = 0.0,
    days_forward: float = 0.0,
) -> MarketData:
    """Return a copy of market with family-specific spot shocks applied."""
    shocked_spots = {}
    for underlying, spot in market.spots.items():
        family = index_family(underlying)
        if family == "SPX":
            shock = spx_shock
        elif family == "NDX":
            shock = ndx_shock
        else:
            shock = other_shock
        shocked_spots[underlying] = spot * (1.0 + shock)

    new_date = pd.Timestamp(market.valuation_date) + pd.Timedelta(days=days_forward)
    return replace(market, spots=shocked_spots, valuation_date=new_date.date())


def index_shock_scenario(
    book: pd.DataFrame,
    market: MarketData,
    spx_shock: float = 0.0,
    ndx_shock: float = 0.0,
    other_shock: float = 0.0,
    vol_shift: float = 0.0,
    vol_mode: Optional[str] = None,
    by: Union[str, Sequence[str], None] = "bucket",
) -> pd.DataFrame:
    """Reprice the book under one SPX/NDX spot shock (and optional parallel vol shift)."""
    pricing_market = replace(
        market,
        vol_mode=vol_mode or market.vol_mode,
        use_book_iv=False,
    )
    shocked = shock_market(
        pricing_market,
        spx_shock=spx_shock,
        ndx_shock=ndx_shock,
        other_shock=other_shock,
    )
    priced = price_book(book, shocked, vol_shift=vol_shift)

    if by is None:
        summary = aggregate(priced)
    else:
        summary = aggregate(priced, by=by).reset_index()

    summary.insert(0, "spx_shock", round(float(spx_shock), 6))
    summary.insert(1, "ndx_shock", round(float(ndx_shock), 6))
    summary.insert(2, "vol_shift", round(float(vol_shift), 6))
    return summary


def index_shock_ladder(
    book: pd.DataFrame,
    market: MarketData,
    shocks: Iterable[float] = np.linspace(-0.10, 0.10, 21),
    mode: str = "parallel",
    vol_mode: Optional[str] = "sticky_delta",
    by: Union[str, Sequence[str], None] = "bucket",
    vol_shift: float = 0.0,
) -> pd.DataFrame:
    """Ladder of MM dollar gamma / vega under equity shocks.

    mode:
      parallel — same shock on SPX and NDX
      spx_only — shock SPX-family only
      ndx_only — shock NDX-family only
    """
    rows = []
    for shock in shocks:
        shock = float(shock)
        if mode == "parallel":
            spx_shock, ndx_shock = shock, shock
        elif mode == "spx_only":
            spx_shock, ndx_shock = shock, 0.0
        elif mode == "ndx_only":
            spx_shock, ndx_shock = 0.0, shock
        else:
            raise ValueError(f"mode must be parallel, spx_only, or ndx_only; got {mode!r}")

        rows.append(
            index_shock_scenario(
                book,
                market,
                spx_shock=spx_shock,
                ndx_shock=ndx_shock,
                vol_shift=vol_shift,
                vol_mode=vol_mode,
                by=by,
            )
        )
    return pd.concat(rows, ignore_index=True)


def liquidity_normalize(priced: pd.DataFrame, oi_adv: pd.DataFrame) -> pd.DataFrame:
    """Express MM dollar gamma / vega relative to ADV and OI notional.

    oi_adv columns: underlying, oi_contracts, adv_contracts
    Join is on underlying only (coarse until contract-level data lands).
    """
    liquidity = oi_adv.groupby("underlying", as_index=False).agg(
        oi_contracts=("oi_contracts", "sum"),
        adv_contracts=("adv_contracts", "sum"),
    )
    out = priced.merge(liquidity, on="underlying", how="left")
    multiplier = out["multiplier"] if "multiplier" in out else 100.0
    spot = out["spot"]
    adv_notional = out["adv_contracts"] * multiplier * spot
    oi_notional = out["oi_contracts"] * multiplier * spot
    out["gamma_vs_adv"] = out["mm_dollar_gamma_1pct"] / adv_notional.replace(0, np.nan)
    out["vega_vs_oi"] = out["mm_dollar_vega_1vol"] / oi_notional.replace(0, np.nan)
    return out


def story_outline() -> dict:
    """Short outline for the vol-impact slide."""
    return {
        "hook": "After a spot shock, how much $gamma / $vega does the street sit on.",
        "method": "Price the book under sticky-delta SPX/NDX ladders; report mm_* columns.",
        "mm_convention": "Street side = -ETF holdings (upper bound on dealer hedge flow).",
        "extensions": "Roll-date event study; vega vs call skew; net vs leveraged-ETF gamma.",
        "open_data": "Need oi_adv.csv and real OVDV surfaces before quoting impact numbers.",
    }
