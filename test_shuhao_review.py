"""Regression checks for additive review; original tests are left untouched."""
from datetime import date
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from core import MarketData, FlatVol, VolSurface, bs_greeks
from shuhao_issuer import parse_holdings, parse_history
from shuhao_review import (
    ROOT, GREEKS, apply_unit_overrides, analytical_box_ids, build_results,
    expiry_buckets, gamma_flip, implied_vol, infer_eln_call_book, load_review,
    price_subset, scale_child_positions, scenario_ladder,
)


def toy_book(position=-10, strike=100, iv=.25, underlying="SPX"):
    return pd.DataFrame({"fund": ["F"], "ticker": ["F"], "holding_id": ["OPT"],
                         "underlying": [underlying], "expiry": pd.to_datetime(["2026-12-18"]),
                         "cp": ["C"], "strike": [strike], "position": [position],
                         "multiplier": [100.0], "units": ["contracts"], "iv": [iv]})


def market():
    return MarketData(date(2026, 11, 20), {"SPX": 100., "NDX": 200.}, rate=.04,
                      div_yields={"SPX": .01, "NDX": .005}, default_vol=None)


@pytest.mark.parametrize("cp", ["C", "P"])
def test_market_value_iv_inversion_recovers_price(cp):
    premium = float(bs_greeks(100, 110, .25, .04, .01, .31, cp)["price"])
    assert implied_vol(premium, 100, 110, .25, .04, .01, cp) == pytest.approx(.31, abs=1e-10)


@pytest.mark.parametrize("premium", [-1., 0., 100., 101.])
def test_iv_refuses_bad_or_unidentifiable_prices(premium):
    with pytest.raises(ValueError):
        implied_vol(premium, 100, 100, .25, 0, 0, "C")


def test_units_override_is_derived_and_snapshot_guarded():
    original = toy_book(position=3100400).assign(ticker="FNOV", holding_id="FLEX")
    rule = dict(ticker="FNOV", holding_id="FLEX", expected_raw_position=3100400,
                units="shares", status="provisional")
    adjusted = apply_unit_overrides(original, [rule])
    assert adjusted.contracts.iloc[0] == 31004
    assert adjusted.raw_position.iloc[0] == original.position.iloc[0] == 3100400
    assert original.units.iloc[0] == "contracts"
    with pytest.raises(ValueError, match="snapshot changed"):
        apply_unit_overrides(original.assign(position=3100401), [rule])


def test_missing_multiplier_is_not_silently_assumed():
    with pytest.raises(ValueError, match="multiplier"):
        apply_unit_overrides(toy_book().assign(multiplier=np.nan), [])


def test_zero_shock_keeps_quoted_iv_even_with_a_different_surface():
    book, mkt = toy_book(iv=.33), market()
    mkt.surfaces["SPX"] = FlatVol(.12)
    base = price_subset(book, mkt)
    zero = scenario_ladder(book, mkt, [0]).iloc[0]
    for key in GREEKS:
        assert zero[key] == pytest.approx(base[key].sum(), rel=1e-12)
    assert zero.hedge_trade_usd == pytest.approx(0, abs=1e-10)


def test_missing_iv_is_not_filled_with_20_percent():
    with pytest.raises(ValueError, match="explicit positive IV"):
        price_subset(toy_book(iv=np.nan), market())


def test_no_fake_sticky_delta_comparison():
    with pytest.raises(ValueError, match="non-flat surfaces"):
        scenario_ladder(toy_book(), market(), [0], vol_mode="sticky_delta")
    mkt = market()
    mkt.surfaces["SPX"] = FlatVol(.2)
    with pytest.raises(ValueError, match="non-flat surfaces"):
        scenario_ladder(toy_book(), mkt, [0], vol_mode="sticky_delta")


def test_surface_scenario_is_anchored_to_base_iv_and_changes_with_spot():
    mkt, book = market(), toy_book(iv=.3)
    mkt.surfaces["SPX"] = VolSurface.synthetic(100.)
    ss = scenario_ladder(book, mkt, [0, .05])
    sd = scenario_ladder(book, mkt, [0, .05], vol_mode="sticky_delta")
    assert ss.iloc[0][GREEKS[0]] == pytest.approx(sd.iloc[0][GREEKS[0]], rel=1e-12)
    assert ss.iloc[1][GREEKS[0]] != pytest.approx(sd.iloc[1][GREEKS[0]], rel=1e-4)


def test_constant_delta_exposure_has_no_rebalancing_trade():
    mkt = replace(market(), rate=0., div_yields={"SPX": 0.})
    ladder = scenario_ladder(toy_book(position=10, strike=1), mkt, [-.1, 0, .1])
    assert ladder.hedge_trade_usd.to_numpy() == pytest.approx([0, 0, 0], abs=1e-8)


def test_long_gamma_mm_buys_dips_and_sells_rallies():
    ladder = scenario_ladder(toy_book(position=-10), market(), [-.01, 0, .01])
    assert ladder.hedge_trade_usd.iloc[0] > 0
    assert ladder.hedge_trade_usd.iloc[2] < 0


def test_spx_only_shock_does_not_move_ndx():
    ladder = scenario_ladder(toy_book(underlying="NDX", strike=200), market(), [-.1, 0, .1], mode="spx_only")
    assert ladder.hedge_trade_usd.to_numpy() == pytest.approx([0, 0, 0], abs=1e-8)
    assert ladder[GREEKS[0]].nunique() == 1


def test_expiry_and_negative_vol_are_explicit_failures():
    with pytest.raises(ValueError, match="expiry-day"):
        price_subset(toy_book().assign(expiry=pd.Timestamp("2026-11-20")), market())
    with pytest.raises(ValueError, match="crosses an expiry"):
        scenario_ladder(toy_book(), market(), [0], days_forward=28)
    with pytest.raises(ValueError, match="positive IV"):
        scenario_ladder(toy_book(), market(), [0], vol_shift=-.3)


def test_zero_dte_bucket_does_not_include_one_day():
    result = expiry_buckets(pd.DataFrame({"dte": [0, 1, 7, 8, 30, 31, 91, 92]}))
    assert result.expiry_bucket.astype(str).tolist() == ["0DTE", "1d-1w", "1d-1w", "1w-1m", "1w-1m", "1-3m", "1-3m", ">3m"]


def test_box_requires_all_four_equal_sized_legs():
    book = pd.concat([toy_book(position=10, strike=90), toy_book(position=-10, strike=90).assign(cp="P"),
                      toy_book(position=-10, strike=110), toy_book(position=10, strike=110).assign(cp="P")], ignore_index=True)
    book = apply_unit_overrides(book, [])
    assert analytical_box_ids(book) == {0, 1, 2, 3}
    assert analytical_box_ids(book.iloc[:3]) == set()
    assert analytical_box_ids(book.assign(position=[10, -10, -9, 10], contracts=[10, -10, -9, 10])) == set()


def test_gamma_flip_is_a_sign_change_not_a_zero_plateau():
    assert gamma_flip(pd.DataFrame({"shock": [-.1, 0, .1], GREEKS[0]: [0, 0, 0]})) is None
    assert gamma_flip(pd.DataFrame({"shock": [-.1, 0, .1], GREEKS[0]: [-1, 0, 1]})) == pytest.approx(0)
    assert gamma_flip(pd.DataFrame({"shock": [-.1, 0, .1], GREEKS[0]: [1, 2, 0]})) is None


def test_lookthrough_uses_ownership_fraction():
    original = toy_book(position=100)
    assert scale_child_positions(original, 200, 1000).position.iloc[0] == 20
    assert original.position.iloc[0] == 100
    with pytest.raises(ValueError):
        scale_child_positions(original, 1001, 1000)


def test_eln_requires_actual_option_notional_and_aligned_ladder():
    kwargs = dict(fund="JEPI US Equity", underlying="SPX", spot=100,
                  strikes=[102, 103], expiries=["2026-12-04", "2026-12-18"], allocation_weights=[.5, .5])
    with pytest.raises(ValueError, match="option-equivalent notional"):
        infer_eln_call_book(option_notional_usd=None, **kwargs)
    inferred = infer_eln_call_book(option_notional_usd=1_000_000, **kwargs)
    assert float((-inferred.position * inferred.multiplier * 100).sum()) == 1_000_000
    assert set(inferred.source) == {"inferred"}


def test_issuer_parser_preserves_short_signs_and_dates():
    document = """<html>Holdings of the Fund as of 11/20/2026<table><tr><th>Security Name</th><th>Identifier</th><th>CUSIP</th><th>Shares / Quantity</th><th>Market Value</th><th>Weighting</th></tr><tr><td>2027-11-19 SPY P 620.83</td><td>4SPY271119P</td><td></td><td>-17,062</td><td>($39,669,150.00)</td><td>-3.37%</td></tr></table></html>"""
    result = parse_holdings(document)
    assert result["holdings_asof"] == "2026-11-20"
    assert result["holdings"][0]["quantity"] == -17062
    assert result["holdings"][0]["market_value_usd"] == -39669150
    assert result["holdings"][0]["cp"] == "P"
    history = "<table><tr><td>11/20/2026</td><td>$55.40</td><td>$55.32</td><td>$55.41</td><td>144,855</td><td>$2,122,780,181</td></tr></table>"
    assert parse_history(history, "2026-11-20")["net_assets_usd"] == 2122780181


@pytest.fixture(scope="module")
def review():
    return load_review()


def test_real_snapshot_has_explicit_five_missing_overlays_and_recovered_ivs(review):
    statuses = review.coverage.set_index("ticker").status
    assert statuses.JEPI == statuses.JEPQ == "eln_terms_required"
    assert statuses.BUFR == statuses.BUFD == "lookthrough_required"
    assert statuses.ACYN == "autocallable_terms_required"
    assert review.book.pricing_status.eq("market_value_implied_iv").any()
    assert len(review.dependencies) == 24
    assert set(review.book.loc[review.book.ticker == "FNOV", "pricing_status"]) == {"expiry_or_roll_pending"}
    assert review.book.pricing_status.eq("analytic_box_net_zero").sum() == 8


def test_all_real_scenario_baselines_reconcile_and_original_csvs_are_unchanged(review):
    result = build_results(review)
    json.dumps(result, allow_nan=False)
    assert all(r["zero_shock_reconciles"] for r in result["outputs"].values())
    assert result["readiness"].startswith("research prototype")
    for relative, expected in review.fingerprints.items():
        assert hashlib.sha256((ROOT / "data" / "sheets" / relative).read_bytes()).hexdigest() == expected


def test_missing_position_match_is_not_zero_filled(tmp_path):
    sheets = tmp_path / "sheets"
    shutil.copytree(ROOT / "data" / "sheets", sheets)
    path = sheets / "holdings" / "XYLD.csv"
    data = pd.read_csv(path)
    mask = data.SECURITY_TYP.astype(str).str.contains("Option") & data["id().position.POSITION"].ne(0)
    missing_id = data.loc[mask, "ID"].iloc[0]
    data.loc[~data.ID.eq(missing_id)].to_csv(path, index=False)
    result = load_review(sheets)
    row = result.book[result.book.holding_id == missing_id].iloc[0]
    assert pd.isna(row.position)
    assert row.pricing_status == "invalid_or_unmatched_input"
