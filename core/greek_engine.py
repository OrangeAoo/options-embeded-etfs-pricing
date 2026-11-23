"""Price an option book and aggregate dollar Greeks.

Conventions (ETF side = as held; street / MM side = -ETF):

    contracts           = position / multiplier   if units == "shares"   else position
    $Delta              = contracts * m * Delta * S
    $Gamma per 1%       = contracts * m * Gamma * S^2 * 0.01   (hedge flow for a 1% move)
    $Vega per vol pt    = contracts * m * Vega * 0.01
    $Theta per day      = contracts * m * Theta_per_day
    $Vanna per vol pt   = contracts * m * Vanna * S * 0.01

Street = -ETF is an upper bound on dealer hedge flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Dict, Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd
from scipy.special import ndtr

from .vol_surface import FlatVol, VolSurface

SQRT_2PI = np.sqrt(2.0 * np.pi)
DOLLAR_COLS = [
    "dollar_delta",
    "dollar_gamma_1pct",
    "dollar_vega_1vol",
    "dollar_theta_1d",
    "dollar_vanna_1vol",
]
MM_COLS = ["mm_" + col for col in DOLLAR_COLS]
REQUIRED_BOOK_COLS = ["fund", "underlying", "expiry", "cp", "strike", "position"]


@dataclass
class MarketData:
    valuation_date: date
    spots: Dict[str, float]
    surfaces: Dict[str, Union[VolSurface, FlatVol]] = field(default_factory=dict)
    rate: float = 0.04
    div_yields: Dict[str, float] = field(default_factory=dict)
    vol_mode: str = "sticky_strike"
    use_book_iv: bool = True
    default_vol: Optional[float] = None

    def shocked(self, spot_shock: float = 0.0, days_forward: float = 0.0) -> "MarketData":
        """Scale every spot by (1 + spot_shock) and optionally roll the clock forward."""
        spots = {name: level * (1.0 + spot_shock) for name, level in self.spots.items()}
        new_date = pd.Timestamp(self.valuation_date) + pd.Timedelta(days=days_forward)
        return replace(self, spots=spots, valuation_date=new_date.date())


def bs_greeks(spot, strike, time_years, rate, div_yield, vol, cp) -> Dict[str, np.ndarray]:
    """Vectorised Black-Scholes-Merton price and Greeks (per one unit of underlying).

    time_years in years, vol as a decimal, cp is "C"/"P".
    Expired legs (T<=0) get intrinsic value, step delta, and zero gamma/vega/theta.
    Vega is per 1.00 of vol; theta is per calendar day.
    """
    spot, strike, time_years, rate, div_yield, vol = (
        np.asarray(x, float) for x in (spot, strike, time_years, rate, div_yield, vol)
    )
    spot, strike, time_years, rate, div_yield, vol = np.broadcast_arrays(
        spot, strike, time_years, rate, div_yield, vol
    )
    is_call = np.asarray(cp) == "C"
    live = (time_years > 0) & (vol > 0)

    # Avoid divide-by-zero on expired legs; results are overwritten below.
    T = np.where(live, time_years, 1.0)
    sigma = np.where(live, vol, 1.0)
    sqrt_T = np.sqrt(T)
    d1 = (np.log(spot / strike) + (rate - div_yield + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc_div = np.exp(-div_yield * T)
    disc_rate = np.exp(-rate * T)
    pdf = np.exp(-0.5 * d1 ** 2) / SQRT_2PI
    Nd1 = ndtr(d1)
    Nd2 = ndtr(d2)

    call_price = spot * disc_div * Nd1 - strike * disc_rate * Nd2
    put_price = strike * disc_rate * ndtr(-d2) - spot * disc_div * ndtr(-d1)
    price = np.where(is_call, call_price, put_price)

    delta = np.where(is_call, disc_div * Nd1, disc_div * (Nd1 - 1.0))
    gamma = disc_div * pdf / (spot * sigma * sqrt_T)
    vega = spot * disc_div * pdf * sqrt_T

    theta_call = (
        -spot * disc_div * pdf * sigma / (2 * sqrt_T)
        - rate * strike * disc_rate * Nd2
        + div_yield * spot * disc_div * Nd1
    )
    theta_put = (
        -spot * disc_div * pdf * sigma / (2 * sqrt_T)
        + rate * strike * disc_rate * ndtr(-d2)
        - div_yield * spot * disc_div * ndtr(-d1)
    )
    theta = np.where(is_call, theta_call, theta_put) / 365.0

    vanna = -disc_div * pdf * d2 / sigma
    charm_call = (
        div_yield * disc_div * Nd1
        - disc_div * pdf * (2 * (rate - div_yield) * T - d2 * sigma * sqrt_T)
        / (2 * T * sigma * sqrt_T)
    )
    charm_put = charm_call - div_yield * disc_div
    charm = np.where(is_call, charm_call, charm_put) / 365.0

    intrinsic = np.where(is_call, np.maximum(spot - strike, 0.0), np.maximum(strike - spot, 0.0))
    step_delta = np.where(is_call, (spot > strike).astype(float), -(spot < strike).astype(float))
    zero = np.zeros_like(spot)

    return {
        "price": np.where(live, price, intrinsic),
        "delta": np.where(live, delta, step_delta),
        "gamma": np.where(live, gamma, zero),
        "vega": np.where(live, vega, zero),
        "theta": np.where(live, theta, zero),
        "vanna": np.where(live, vanna, zero),
        "charm": np.where(live, charm, zero),
        "d1": np.where(live, d1, np.nan),
    }


def normalize_book(book: pd.DataFrame) -> pd.DataFrame:
    """Validate an option book and add contracts / multiplier / source defaults."""
    missing = [c for c in REQUIRED_BOOK_COLS if c not in book.columns]
    if missing:
        raise ValueError(f"option book missing columns: {missing}")

    out = book.copy()
    out["expiry"] = pd.to_datetime(out["expiry"])
    out["cp"] = out["cp"].astype(str).str.upper().str[0]

    if "multiplier" not in out.columns:
        out["multiplier"] = 100.0
    out["multiplier"] = out["multiplier"].fillna(100.0).astype(float)

    if "units" not in out.columns:
        out["units"] = "contracts"
    out["units"] = out["units"].fillna("contracts").str.lower()
    bad_units = ~out["units"].isin(["contracts", "shares"])
    if bad_units.any():
        raise ValueError(f"units must be 'contracts' or 'shares'; got {out.loc[bad_units, 'units'].unique()}")

    out["contracts"] = np.where(
        out["units"] == "shares",
        out["position"] / out["multiplier"],
        out["position"],
    )

    if "source" not in out.columns:
        out["source"] = "observed"
    out["source"] = out["source"].fillna("observed")
    return out


def resolve_vols(book: pd.DataFrame, market: MarketData, spots: np.ndarray, time_years: np.ndarray) -> np.ndarray:
    """Implied vol per leg from surfaces (and optional book 'iv' column)."""
    iv = np.full(len(book), np.nan)

    for underlying, row_idx in book.groupby("underlying").indices.items():
        surface = market.surfaces.get(underlying)
        if surface is None and market.default_vol is not None:
            surface = FlatVol(market.default_vol)
        if surface is None:
            continue
        iv[row_idx] = surface.iv(
            book["strike"].to_numpy()[row_idx],
            time_years[row_idx],
            spot=spots[row_idx],
            mode=market.vol_mode,
        )

    if "iv" in book.columns:
        given = book["iv"].to_numpy(float)
        use_given = np.isfinite(given) & (market.use_book_iv | np.isnan(iv))
        iv = np.where(use_given, given, iv)

    if np.isnan(iv).any():
        missing = book.loc[np.isnan(iv), "underlying"].unique()
        raise ValueError(
            f"no implied vol for underlyings {list(missing)}: "
            "add a surface, an 'iv' column, or default_vol"
        )
    return iv


def price_book(book: pd.DataFrame, market: MarketData, vol_shift: float = 0.0) -> pd.DataFrame:
    """Price every leg and attach per-unit and dollar Greeks (ETF side and MM side)."""
    book = normalize_book(book)

    missing_spots = sorted(set(book["underlying"]) - set(market.spots))
    if missing_spots:
        raise ValueError(f"no spot for underlyings {missing_spots}: add them to market spots")

    spots = book["underlying"].map(market.spots).to_numpy(float)
    div_yields = book["underlying"].map(lambda u: market.div_yields.get(u, 0.0)).to_numpy(float)
    days = (book["expiry"] - pd.Timestamp(market.valuation_date)).dt.days.clip(lower=0)
    time_years = (days / 365.0).to_numpy()
    vols = resolve_vols(book, market, spots, time_years) + vol_shift

    greeks = bs_greeks(
        spots,
        book["strike"].to_numpy(float),
        time_years,
        market.rate,
        div_yields,
        vols,
        book["cp"].to_numpy(),
    )
    contracts_x_mult = book["contracts"].to_numpy(float) * book["multiplier"].to_numpy(float)

    out = book.copy()
    out["spot"] = spots
    out["T"] = time_years
    out["iv_used"] = vols
    out["dte"] = (time_years * 365).round().astype(int)
    for name, values in greeks.items():
        out[name] = values

    out["moneyness"] = out["strike"] / out["spot"]
    out["notional"] = contracts_x_mult * spots
    out["dollar_delta"] = contracts_x_mult * greeks["delta"] * spots
    out["dollar_gamma_1pct"] = contracts_x_mult * greeks["gamma"] * spots ** 2 * 0.01
    out["dollar_vega_1vol"] = contracts_x_mult * greeks["vega"] * 0.01
    out["dollar_theta_1d"] = contracts_x_mult * greeks["theta"]
    out["dollar_vanna_1vol"] = contracts_x_mult * greeks["vanna"] * spots * 0.01

    for col in DOLLAR_COLS:
        out["mm_" + col] = -out[col]
    return out


def aggregate(
    priced: pd.DataFrame,
    by: Union[str, Sequence[str], None] = None,
    cols: Sequence[str] = tuple(DOLLAR_COLS + MM_COLS),
) -> pd.DataFrame:
    """Sum dollar Greeks by fund / underlying / bucket / expiry_bucket / source / ..."""
    cols = [c for c in cols if c in priced.columns]
    if by is None:
        return priced[cols].sum().to_frame("total").T
    summary = priced.groupby(by, dropna=False)[cols].sum()
    summary["n_legs"] = priced.groupby(by, dropna=False).size()
    return summary


def add_expiry_bucket(
    priced: pd.DataFrame,
    edges=(0, 1, 7, 30, 91, 10_000),
    labels=("0DTE", "1d-1w", "1w-1m", "1-3m", ">3m"),
) -> pd.DataFrame:
    out = priced.copy()
    out["expiry_bucket"] = pd.cut(out["dte"], bins=[-1, *edges[1:]], labels=labels, right=True)
    return out


def spot_ladder(
    book: pd.DataFrame,
    market: MarketData,
    shocks: Iterable[float] = np.linspace(-0.10, 0.10, 21),
    by: Union[str, Sequence[str], None] = None,
    vol_mode: Optional[str] = None,
    days_forward: float = 0.0,
) -> pd.DataFrame:
    """Reprice at S x (1+shock) for each shock. Returns MM-side dollar Greeks.

    sticky_strike keeps each strike's vol; sticky_delta moves the smile with spot.
    """
    base_market = replace(
        market,
        vol_mode=vol_mode or market.vol_mode,
        use_book_iv=False,
    )
    rows = []
    for shock in shocks:
        shocked = base_market.shocked(spot_shock=float(shock), days_forward=days_forward)
        priced = price_book(book, shocked)
        if by is None:
            summary = aggregate(priced)
        else:
            summary = aggregate(priced, by=by).reset_index()
        summary.insert(0, "shock", round(float(shock), 6))
        rows.append(summary)
    return pd.concat(rows, ignore_index=True)


def spot_time_grid(
    book: pd.DataFrame,
    market: MarketData,
    shocks: Iterable[float],
    days_forward: Iterable[float],
    col: str = "mm_dollar_gamma_1pct",
) -> pd.DataFrame:
    """Matrix of one MM Greek over (days forward x spot shock). Useful for heatmaps."""
    shocks = list(shocks)
    columns = {}
    for days in days_forward:
        ladder = spot_ladder(book, market, shocks=shocks, days_forward=days)
        columns[days] = ladder.set_index("shock")[col]
    grid = pd.DataFrame(columns).T
    grid.index.name = "days_forward"
    grid.columns.name = "shock"
    return grid


def gamma_flip(ladder: pd.DataFrame, col: str = "mm_dollar_gamma_1pct") -> Optional[float]:
    """Spot shock closest to 0 where total MM gamma changes sign. None if it never does."""
    totals = ladder.groupby("shock")[col].sum().sort_index()
    shocks = totals.index.to_numpy()
    values = totals.to_numpy()
    crossings = []
    for i in range(len(shocks) - 1):
        if values[i] == 0:
            crossings.append(shocks[i])
        elif values[i] * values[i + 1] < 0:
            weight = values[i] / (values[i + 1] - values[i])
            crossings.append(shocks[i] - weight * (shocks[i + 1] - shocks[i]))
    if not crossings:
        return None
    return min(crossings, key=abs)


def to_index_equivalent(
    priced: pd.DataFrame,
    betas: Dict[str, float],
    spots: Dict[str, float],
    target: str = "SPX",
) -> pd.DataFrame:
    """Rescale each leg's dollar gamma/delta into target-index terms (beta^2 for gamma)."""
    out = priced.copy()
    beta = out["underlying"].map(lambda u: 1.0 if u == target else betas.get(u, np.nan))
    out[f"dollar_gamma_1pct_{target}_eq"] = out["dollar_gamma_1pct"] * beta ** 2
    out[f"dollar_delta_{target}_eq"] = out["dollar_delta"] * beta
    return out
