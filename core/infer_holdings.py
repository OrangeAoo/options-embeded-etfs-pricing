"""Infer option books for funds whose holdings aren't observable.

Use for ELN/swap overlays (JEPI/JEPQ), missing holdings files, or scaling a
representative buffer tranche up to its family. Rules turn the prospectus into
synthetic legs sized from AUM.

Every inferred leg has source="inferred" and an inference_note.

Rules (used by data_io.inference_rules_from_etf_list / infer_fund)
------------------------------------------------------------------
covered_call : short calls on coverage x AUM notional
buffer       : FT Vest / Innovator tranche (synthetic stock + put spread + short call)
collar       : hedged-equity put-spread collar, quarterly ladder
scale_from   : copy an observed fund's legs scaled by AUM ratio
exclude      : documented skip

Caveat: laddered books are struck off today's spot (we don't know historical
ref levels). Calibrate against an observed fund before trusting numbers.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

BOOK_COLS = [
    "fund", "holding_id", "underlying", "expiry", "cp", "strike",
    "position", "units", "multiplier", "source", "inference_note",
]


def third_friday(year: int, month: int) -> date:
    day = date(year, month, 15)
    return day + timedelta(days=(4 - day.weekday()) % 7)


def next_monthly_expiries(valuation_date: date, count: int) -> List[date]:
    expiries = []
    year, month = valuation_date.year, valuation_date.month
    while len(expiries) < count:
        expiry = third_friday(year, month)
        if expiry > valuation_date:
            expiries.append(expiry)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return expiries


def next_weekly_expiries(valuation_date: date, count: int) -> List[date]:
    days_to_friday = (4 - valuation_date.weekday()) % 7 or 7
    first = valuation_date + timedelta(days=days_to_friday)
    return [first + timedelta(weeks=i) for i in range(count)]


def next_quarterly_expiries(valuation_date: date, count: int) -> List[date]:
    monthly = next_monthly_expiries(valuation_date, 3 * count + 3)
    quarterly = [d for d in monthly if d.month in (3, 6, 9, 12)]
    return quarterly[:count]


def round_to_step(value: float, step: float) -> float:
    if not step or step <= 0:
        return float(value)
    return float(np.round(value / step) * step)


def make_leg(fund, underlying, expiry, cp, strike, contracts, note, multiplier=100.0):
    """One synthetic option leg as a dict matching BOOK_COLS."""
    return {
        "fund": fund,
        "holding_id": f"{underlying} {expiry:%m/%d/%y} {cp}{strike:g} (inferred)",
        "underlying": underlying,
        "expiry": pd.Timestamp(expiry),
        "cp": cp,
        "strike": float(strike),
        "position": float(contracts),
        "units": "contracts",
        "multiplier": multiplier,
        "source": "inferred",
        "inference_note": note,
    }


def covered_call(
    fund: str,
    underlying: str,
    aum: float,
    spot: float,
    valuation_date: date,
    coverage: float = 1.0,
    otm_pct: float = 0.0,
    frequency: str = "monthly",
    ladder: int = 1,
    strike_step: float = 5.0,
    multiplier: float = 100.0,
) -> pd.DataFrame:
    if frequency == "weekly":
        expiries = next_weekly_expiries(valuation_date, ladder)
    elif frequency == "daily":
        expiries = [valuation_date]
    else:
        expiries = next_monthly_expiries(valuation_date, ladder)

    contracts = -coverage * aum / (spot * multiplier) / len(expiries)
    strike = round_to_step(spot * (1 + otm_pct), strike_step)
    note = f"covered_call cov={coverage:.0%} otm={otm_pct:+.1%} {frequency} x{len(expiries)}"
    legs = [
        make_leg(fund, underlying, expiry, "C", strike, contracts, note, multiplier)
        for expiry in expiries
    ]
    return pd.DataFrame(legs, columns=BOOK_COLS)


def buffer(
    fund: str,
    underlying: str,
    aum: float,
    ref_spot: float,
    expiry: date,
    buffer_pct: float = 0.10,
    cap_pct: float = 0.15,
    multiplier: float = 100.0,
    spot: Optional[float] = None,
    invested_pct: float = 1.0,
) -> pd.DataFrame:
    """Size off today's spot: contracts = invested_pct x AUM / (spot x multiplier).

    Sizing off ref_spot overstates the book after a rally; prefer today's spot.
    """
    sizing_spot = spot if spot is not None else ref_spot
    contracts = invested_pct * aum / (sizing_spot * multiplier)
    note = f"buffer {buffer_pct:.0%} cap {cap_pct:+.1%} ref {ref_spot:g}"
    legs = [
        make_leg(fund, underlying, expiry, "C", round(ref_spot * 0.01, 2), +contracts, note, multiplier),
        make_leg(fund, underlying, expiry, "P", round(ref_spot, 2), +contracts, note, multiplier),
        make_leg(fund, underlying, expiry, "P", round(ref_spot * (1 - buffer_pct), 2), -contracts, note, multiplier),
        make_leg(fund, underlying, expiry, "C", round(ref_spot * (1 + cap_pct), 2), -contracts, note, multiplier),
    ]
    return pd.DataFrame(legs, columns=BOOK_COLS)


def collar(
    fund: str,
    underlying: str,
    aum: float,
    spot: float,
    valuation_date: date,
    put_hi: float = 0.05,
    put_lo: float = 0.20,
    cap_pct: float = 0.04,
    ladder: int = 3,
    coverage: float = 1.0,
    strike_step: float = 5.0,
    multiplier: float = 100.0,
) -> pd.DataFrame:
    expiries = next_quarterly_expiries(valuation_date, ladder)
    contracts = coverage * aum / (spot * multiplier) / len(expiries)
    note = f"collar put {1 - put_hi:.0%}/{1 - put_lo:.0%} cap {cap_pct:+.1%} quarterly x{len(expiries)}"
    rows = []
    for expiry in expiries:
        put_hi_strike = round_to_step(spot * (1 - put_hi), strike_step)
        put_lo_strike = round_to_step(spot * (1 - put_lo), strike_step)
        call_strike = round_to_step(spot * (1 + cap_pct), strike_step)
        rows.append(make_leg(fund, underlying, expiry, "P", put_hi_strike, +contracts, note, multiplier))
        rows.append(make_leg(fund, underlying, expiry, "P", put_lo_strike, -contracts, note, multiplier))
        rows.append(make_leg(fund, underlying, expiry, "C", call_strike, -contracts, note, multiplier))
    return pd.DataFrame(rows, columns=BOOK_COLS)


def scale_from(
    observed_book: pd.DataFrame,
    source_fund: str,
    target_fund: str,
    scale: float,
) -> pd.DataFrame:
    source = observed_book[observed_book["fund"] == source_fund]
    if source.empty:
        raise ValueError(f"scale_from: no observed legs for {source_fund!r}")
    out = source.copy()
    out["fund"] = target_fund
    out["position"] = out["position"] * scale
    out["source"] = "inferred"
    out["inference_note"] = f"scaled from {source_fund} x{scale:,.1f} (AUM ratio)"
    return out


def _param(row, key, default):
    """Read a numeric inference param; fall back to default if blank/missing."""
    value = row.get(key, np.nan)
    if value is None or value == "":
        return default
    if isinstance(value, float) and np.isnan(value):
        return default
    return float(value)


def infer_fund(
    row: pd.Series,
    aum: float,
    spots: dict,
    valuation_date: date,
    observed_book: Optional[pd.DataFrame] = None,
    aum_by_fund: Optional[dict] = None,
) -> pd.DataFrame:
    """Build the synthetic book for one inference-rule row."""
    rule = str(row["rule"]).strip().lower()
    fund = row["fund"]
    underlying = row.get("underlying")

    if rule == "exclude":
        return pd.DataFrame(columns=BOOK_COLS)

    if rule == "scale_from":
        source_fund = row["scale_from"]
        source_aum = (aum_by_fund or {}).get(source_fund)
        if not source_aum:
            raise ValueError(f"{fund}: need AUM for source fund {source_fund}")
        return scale_from(observed_book, source_fund, fund, aum / source_aum)

    if underlying not in spots:
        raise ValueError(f"{fund}: no spot for underlying {underlying}")
    spot = spots[underlying]
    strike_step = _param(row, "strike_step", 5.0)

    if rule == "covered_call":
        return covered_call(
            fund, underlying, aum, spot, valuation_date,
            coverage=_param(row, "coverage", 1.0),
            otm_pct=_param(row, "otm_pct", 0.0),
            frequency=str(row.get("frequency") or "monthly"),
            ladder=int(_param(row, "ladder", 1)),
            strike_step=strike_step,
        )

    if rule == "buffer":
        ref_spot = _param(row, "ref_spot", spot)
        if row.get("expiry"):
            expiry = pd.Timestamp(row["expiry"]).date()
        else:
            expiry = next_monthly_expiries(valuation_date, 12)[-1]
        return buffer(
            fund, underlying, aum, ref_spot, expiry,
            buffer_pct=_param(row, "buffer_pct", 0.10),
            cap_pct=_param(row, "cap_pct", 0.15),
            spot=spot,
            invested_pct=_param(row, "invested_pct", 1.0),
        )

    if rule == "collar":
        return collar(
            fund, underlying, aum, spot, valuation_date,
            put_hi=_param(row, "put_hi", 0.05),
            put_lo=_param(row, "put_lo", 0.20),
            cap_pct=_param(row, "cap_pct", 0.04),
            ladder=int(_param(row, "ladder", 3)),
            coverage=_param(row, "coverage", 1.0),
            strike_step=strike_step,
        )

    raise ValueError(f"{fund}: unknown rule {rule!r}")
