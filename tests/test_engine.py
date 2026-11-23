from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import (
    MarketData,
    FlatVol,
    VolSurface,
    bs_greeks,
    gamma_flip,
    parse_option_id,
    price_book,
    spot_ladder,
    data_io,
    impact,
    pipeline,
)

ROOT = Path(__file__).resolve().parents[1]
SHEETS = ROOT / "data" / "sheets"


def test_put_call_parity():
    spot, strike, time_years, rate, div_yield, vol = 100.0, 95.0, 0.5, 0.04, 0.01, 0.25
    call = bs_greeks(spot, strike, time_years, rate, div_yield, vol, "C")["price"]
    put = bs_greeks(spot, strike, time_years, rate, div_yield, vol, "P")["price"]
    forward = spot * np.exp(-div_yield * time_years) - strike * np.exp(-rate * time_years)
    assert call - put == pytest.approx(forward, abs=1e-10)


@pytest.mark.parametrize("cp", ["C", "P"])
def test_greeks_match_finite_differences(cp):
    spot, strike, time_years, rate, div_yield, vol = 100.0, 105.0, 0.25, 0.03, 0.015, 0.2
    greeks = bs_greeks(spot, strike, time_years, rate, div_yield, vol, cp)

    def price(s=spot, t=time_years, v=vol):
        return bs_greeks(s, strike, t, rate, div_yield, v, cp)["price"]

    bump = 1e-3
    assert greeks["delta"] == pytest.approx((price(s=spot + bump) - price(s=spot - bump)) / (2 * bump), rel=1e-6)
    assert greeks["gamma"] == pytest.approx(
        (price(s=spot + bump) - 2 * price() + price(s=spot - bump)) / bump ** 2, rel=1e-4
    )
    assert greeks["vega"] == pytest.approx((price(v=vol + 1e-5) - price(v=vol - 1e-5)) / 2e-5, rel=1e-6)

    dt = 1e-5
    assert greeks["theta"] == pytest.approx(
        -(price(t=time_years + dt) - price(t=time_years - dt)) / (2 * dt) / 365, rel=1e-5
    )

    def delta(t=time_years, v=vol):
        return bs_greeks(spot, strike, t, rate, div_yield, v, cp)["delta"]

    assert greeks["vanna"] == pytest.approx((delta(v=vol + 1e-5) - delta(v=vol - 1e-5)) / 2e-5, rel=1e-5)
    assert greeks["charm"] == pytest.approx(
        -(delta(t=time_years + dt) - delta(t=time_years - dt)) / (2 * dt) / 365, rel=1e-4
    )


def test_expired_leg_is_intrinsic():
    greeks = bs_greeks(100, 90, 0.0, 0.04, 0.0, 0.2, "C")
    assert greeks["price"] == pytest.approx(10.0)
    assert greeks["gamma"] == 0
    assert greeks["delta"] == 1


def test_parse_flex_ticker():
    terms = parse_option_id("4SPY US 12/18/26 P680.59 FLX")
    assert terms.underlying == "SPY"
    assert terms.expiry == date(2026, 12, 18)
    assert terms.cp == "P"
    assert terms.strike == 680.59
    assert terms.is_flex is True
    assert parse_option_id("SPXW US 1/16/27 C7600 Index").underlying == "SPX"
    assert parse_option_id("DGCXX US Equity") is None


def test_surface_interpolation_and_modes():
    surface = VolSurface.synthetic(100.0)
    atm_30d = surface.iv(100.0, 30 / 365)[0]
    assert 0.15 < atm_30d < 0.2
    assert surface.iv(110, 30 / 365, spot=110, mode="sticky_delta")[0] == pytest.approx(atm_30d)
    assert surface.iv(90, 30 / 365, spot=120, mode="sticky_strike")[0] == pytest.approx(
        surface.iv(90, 30 / 365)[0]
    )


def _toy_book():
    return pd.DataFrame({
        "fund": ["F", "F"],
        "underlying": ["SPX", "SPX"],
        "expiry": ["2026-12-18", "2026-12-18"],
        "cp": ["C", "P"],
        "strike": [7600, 7600],
        "position": [-10, 10],
    })


def test_dollar_gamma_definition_and_sign():
    market = MarketData(date(2026, 11, 20), {"SPX": 7600.0}, {"SPX": FlatVol(0.15)}, rate=0.0)
    priced = price_book(_toy_book(), market)
    time_years = 28 / 365
    unit_gamma = bs_greeks(7600, 7600, time_years, 0.0, 0.0, 0.15, "C")["gamma"]
    assert priced.loc[0, "dollar_gamma_1pct"] == pytest.approx(-10 * 100 * unit_gamma * 7600 ** 2 * 0.01)
    assert priced["mm_dollar_gamma_1pct"].sum() == pytest.approx(-priced["dollar_gamma_1pct"].sum())
    assert priced["dollar_gamma_1pct"].sum() == pytest.approx(0.0, abs=1e-6)


def test_shares_units_convert_to_contracts():
    book = _toy_book().assign(units="shares", position=[-1000, 1000])
    market = MarketData(date(2026, 11, 20), {"SPX": 7600.0}, default_vol=0.15)
    assert price_book(book, market)["contracts"].tolist() == [-10, 10]


def test_ladder_zero_shock_matches_price_book():
    market = MarketData(date(2026, 11, 20), {"SPX": 7600.0}, {"SPX": VolSurface.synthetic(7600.0)})
    book = _toy_book().iloc[[0]]
    ladder = spot_ladder(book, market, shocks=[-0.05, 0.0, 0.05])
    base = price_book(book, market)["mm_dollar_gamma_1pct"].sum()
    assert ladder.loc[ladder.shock == 0, "mm_dollar_gamma_1pct"].iloc[0] == pytest.approx(base)


def test_gamma_flip_interpolates():
    ladder = pd.DataFrame({
        "shock": [-0.1, 0.0, 0.1],
        "mm_dollar_gamma_1pct": [-1.0, 1.0, 2.0],
    })
    assert gamma_flip(ladder) == pytest.approx(-0.05)


def test_project10_sheets_pipeline_runs():
    book, coverage, etf_list, market = pipeline.build_from_sheets(SHEETS)
    assert len(etf_list) >= 20
    assert (coverage["status"] == "observed").any()
    assert set(coverage["status"]) <= {"observed", "skipped"}
    # Pipeline prices observed holdings only (no synthetic/inferred legs).
    assert (book["source"] == "observed").all()
    for ticker in ("XYLD US Equity", "SPYI US Equity", "BOXX US Equity"):
        assert (coverage.loc[coverage.fund == ticker, "status"] == "observed").all()
    for ticker in ("JEPI US Equity", "JEPQ US Equity"):
        assert (coverage.loc[coverage.fund == ticker, "status"] == "skipped").all()
    priced = price_book(book, market)
    assert np.isfinite(priced["dollar_gamma_1pct"]).all()


def test_index_shock_ladder_parallel_matches_spot_ladder():
    market = MarketData(
        date(2026, 11, 20),
        {"SPX": 7600.0, "NDX": 27000.0},
        {"SPX": FlatVol(0.15), "NDX": FlatVol(0.18)},
    )
    book = pd.DataFrame({
        "fund": ["A", "B"],
        "underlying": ["SPX", "NDX"],
        "expiry": ["2026-12-18", "2026-12-18"],
        "cp": ["C", "C"],
        "strike": [7600, 27000],
        "position": [-10, -5],
    })
    shocks = [-0.05, 0.0, 0.05]
    got = impact.index_shock_ladder(
        book, market, shocks=shocks, mode="parallel", by=None, vol_mode="sticky_strike"
    )
    ref = spot_ladder(book, market, shocks=shocks, by=None, vol_mode="sticky_strike")
    assert got["mm_dollar_gamma_1pct"].to_numpy() == pytest.approx(ref["mm_dollar_gamma_1pct"].to_numpy())


def test_spx_only_shock_leaves_ndx_spot():
    market = MarketData(date(2026, 11, 20), {"SPX": 7600.0, "NDX": 27000.0}, default_vol=0.15)
    shocked = impact.shock_market(market, spx_shock=-0.10, ndx_shock=0.0)
    assert shocked.spots["SPX"] == pytest.approx(6840.0)
    assert shocked.spots["NDX"] == pytest.approx(27000.0)
