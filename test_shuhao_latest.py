"""Independent numerical checks and regression cases for the new import path."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import shuhao_latest as latest


def test_security_type_uses_header_not_long_short_column():
    headers=["ID","SECURITY_TYP.POSITION_TYPE","SECURITY_TYP","SECURITY_TYP.ID_DATE","id().reported_mkt_val.REPORTED_MKT_VAL"]
    data=[["STOCK","L","Common Stock",pd.Timestamp("2026-11-20"),123],
          ["SWAP","L","Swap",pd.Timestamp("2026-11-20"),456]]
    s=latest.holdings_summary(headers,data)
    assert s["security_types"] == {"Common Stock":1,"Swap":1}
    assert s["listed_option_or_ELN_rows"] == 1
    assert s["market_value_sum"] == 579


def test_aum_categories_reconcile_and_are_not_priced_risk_coverage():
    etfs=pd.DataFrame({"ticker":["A","B","C"],"aum_usd":[60.,30.,10.]})
    x=latest.aum_coverage(etfs,["A","B"],["B"])
    assert x["direct_book_reference_aum_share"] == .9
    assert x["any_priced_reference_aum_share"] == .3
    assert x["direct_book_reference_aum_usd"]+x["missing_book_reference_aum_usd"] == 100
    assert x["funds_requiring_missing_overlay_or_lookthrough"] == ["C"]
    with pytest.raises(ValueError):latest.aum_coverage(etfs,["A"],["B"])


def test_text_errors_and_dates_are_not_numeric_data():
    assert latest.number("#N/A") is None
    assert latest.number("31.7") is None
    assert latest.number(True) is None
    assert latest.number(float("nan")) is None
    assert latest.number(0) == 0
    assert latest.number(31.7) == 31.7


def test_duplicate_response_tabs_do_not_double_count_or_choose_an_error():
    base = {"fund": "F", "id": "ID", "returned_id": "ID", "sheet": "BQL逐腿", "ivol(mid)": 22.0}
    result, conflicts = latest.merge_responses([base, dict(base, sheet="BQL逐腿 (2)", strike_px=100)])
    assert len(result) == 1 and not conflicts
    assert result[("F", "ID")]["duplicate_response_count"] == 2
    assert result[("F", "ID")]["strike_px"] == 100
    _, conflicts = latest.merge_responses([base, dict(base, sheet="copy", **{"ivol(mid)": 23.0})])
    assert conflicts[0]["field"] == "ivol(mid)"


def quote_fixture():
    row = SimpleNamespace(holding_id="OPT", strike=100., expiry=pd.Timestamp("2026-12-18"), cp="P")
    quote = {"returned_id": "OPT", "expire_dt": pd.Timestamp("2026-12-18"), "strike_px": 100.,
             "put_call": "Put", "ivol(mid)": 20., "ivol(mid).DATE": pd.Timestamp("2026-11-20"),
             "requested_date": "2026-11-20"}
    return row, quote


def test_returned_date_and_terms_must_match():
    row, q = quote_fixture()
    assert latest.validate_quote(row, q, "2026-11-20") == "same_date_terms_matched_iv"
    assert latest.validate_quote(row, dict(q, strike_px=101), "2026-11-20") == "strike_mismatch"
    assert latest.validate_quote(row, dict(q, **{"ivol(mid).DATE": pd.Timestamp("2026-11-23")}), "2026-11-20") == "iv_date_mismatch_or_missing"
    assert latest.validate_quote(row, dict(q, **{"ivol(mid)": "#N/A"}), "2026-11-20") == "iv_not_numeric_positive"


def test_european_price_put_call_parity_and_finite_difference_greeks():
    s, k, t, r, q, sigma = 105., 100., .5, .04, .015, .25
    c = latest.bs(s, k, t, r, q, sigma, "C")
    p = latest.bs(s, k, t, r, q, sigma, "P")
    assert c["price"] - p["price"] == pytest.approx(s * np.exp(-q*t) - k*np.exp(-r*t), abs=1e-10)
    h, v = .01, .00001
    f = lambda x, y: latest.bs(x, k, t, r, q, y, "C")["price"]
    assert c["delta"] == pytest.approx((f(s+h,sigma)-f(s-h,sigma))/(2*h), rel=1e-6)
    assert c["gamma"] == pytest.approx((f(s+h,sigma)-2*f(s,sigma)+f(s-h,sigma))/h**2, rel=1e-5)
    assert c["vega"] == pytest.approx((f(s,sigma+v)-f(s,sigma-v))/(2*v), rel=1e-6)


def test_expiry_day_requires_time_instead_of_zero_greeks():
    with pytest.raises(ValueError, match="timestamp"):
        latest.bs(100, 100, 0, .04, 0, .2, "C")


def test_american_tree_exercise_and_convergence():
    args = (100.,100.,.5,.04,0.,.2,"C")
    european = latest.bs(*args)["price"]
    p200 = latest.crr_price(*args,200)[0]
    p800 = latest.crr_price(*args,800)[0]
    assert abs(p800-european) < abs(p200-european)
    assert abs(p800-european) < .01
    american = latest.model_greeks(80.,100.,.5,.08,0.,.2,"P","American",800)
    assert american["price"] >= max(20.,latest.bs(80.,100.,.5,.08,0.,.2,"P")["price"])
    assert -1.001 <= american["delta"] <= 0 and american["gamma"] >= -1e-8
    assert american["vega"] >= -1e-6


def surface(sigma=.2):
    return latest.StrictSurface(pd.DataFrame([{"days":d,"moneyness":m,"iv":sigma} for d in [30,60] for m in [.8,1.,1.2]]),100.,"spot",.04,0.)


def test_surface_does_not_extrapolate_or_fill_holes():
    s = surface()
    assert s.at_strike(100,45/365) == pytest.approx(.2)
    with pytest.raises(ValueError, match="tenor outside"):
        s.at_strike(100,7/365)
    with pytest.raises(ValueError, match="moneyness outside"):
        s.at_strike(150,45/365)
    missing = s.frame.drop(index=0)
    with pytest.raises(ValueError, match="missing"):
        latest.StrictSurface(missing,100.,"spot",.04,0.)
    with pytest.raises(ValueError, match="confirm"):
        latest.StrictSurface(s.frame,100.,"unknown",.04,0.)


def test_total_variance_interpolation_and_true_delta_fixed_point():
    s = surface()
    s.frame.loc[s.frame.days==60,"iv"] = .3
    s.__post_init__()
    expected = np.sqrt((.2**2*30 + .3**2*60)/2/45)
    assert s.at_strike(100,45/365) == pytest.approx(expected)
    flat = surface()
    assert flat.sticky_delta(100,45/365,102) == pytest.approx(.2)
    for i,row in s.frame.iterrows():
        s.frame.loc[i,"iv"] = .2 - .1*(row.moneyness-1)
    s.__post_init__()
    sigma = s.sticky_delta(100,45/365,102)
    delta = latest.bs(102,100,45/365,.04,0,sigma,"C")["delta"]
    assert sigma == pytest.approx(s.at_delta(delta,45/365),abs=1e-10)


def test_scaled_gamma_matches_hedge_trade_and_zero_shock():
    g = latest.bs(100,100,30/365,.04,0,.2,"C")
    book = pd.DataFrame([dict(ticker="F",holding_id="ID",underlying="SPX",spot=100.,strike=100.,dte=30,
                             rate=.04,dividend_yield=0.,selected_iv=.2,cp="C",exercise="European",
                             contracts=-10.,multiplier=100.,delta=g["delta"],latest_pricing_status="priced_research_estimate")])
    config = {"american_tree_steps":100,"confirmed_surface_basis":None,"confirmed_tenor_daycounts":False}
    result=latest.ladder(book,config,shocks=[0,.00001])
    assert result["rows"][0]["dealer_hedge_trade_usd"] == pytest.approx(0,abs=1e-12)
    # Short ETF option => long dealer option => dealer sells the underlying on an up move.
    assert result["rows"][1]["dealer_hedge_trade_usd"] < 0
    gamma_flow = -10*100*g["gamma"]*100**2*.00001
    assert result["rows"][1]["dealer_hedge_trade_usd"] == pytest.approx(gamma_flow,rel=1e-4)
    assert latest.ladder(book,config,days_forward=30)["status"] == "roll_or_settlement_inputs_required"
    assert latest.ladder(book,config,vol_mode="sticky_delta")["status"] == "surface_coordinate_and_tenor_definitions_unconfirmed"


def structured_fixture():
    row = {"AS_OF_DATE": pd.Timestamp("2026-11-20"), "SECURITY": "OPTION",
           "EXPIRE_DT": pd.Timestamp("2026-12-18"), "STRIKE_PX": 100., "PUT_CALL": "Call",
           "IVOL (%)": 20., "IVOL_DATE": pd.Timestamp("2026-11-20"), "EXER_TYP": "American",
           "UNDERLYING_SECURITY": "DUK US Equity", "FIELD": "ivol(mid)", "sheet": "DUK US Equity", "row": 2}
    book = pd.DataFrame([{"ticker": f, "holding_id": "OPTION", "underlying": "DUK"} for f in ["DIVO", "ACIO"]])
    return row, book


def test_one_structured_quote_joins_two_funds_without_duplicate_positions():
    row, book = structured_fixture()
    result = latest.structured_records([row, dict(row, row=3)], book, "2026-11-20")
    assert result["structured_audit"]["unique_option_securities"] == 1
    assert set(result["unique"]) == {("DIVO", "OPTION"), ("ACIO", "OPTION")}
    assert result["unique"][("DIVO", "OPTION")]["ivol(mid)"] == 20.  # percent retained, not scaled twice
    assert len(result["responses"]) == 2


@pytest.mark.parametrize("change", [
    {"IVOL_DATE": pd.Timestamp("2026-11-19")}, {"AS_OF_DATE": pd.Timestamp("2026-11-19")},
    {"IVOL (%)": "#N/A"}, {"IVOL (%)": 0}, {"EXPIRE_DT": None},
    {"UNDERLYING_SECURITY": "SPX Index"}, {"FIELD": "unrecognised"},
])
def test_structured_quotes_reject_stale_invalid_or_misjoined_data(change):
    row, book = structured_fixture()
    with pytest.raises(ValueError):
        latest.structured_records([dict(row, **change)], book, "2026-11-20")


def test_structured_conflicting_quotes_do_not_choose_one_silently():
    row, book = structured_fixture()
    with pytest.raises(ValueError, match="conflicting"):
        latest.structured_records([row, dict(row, **{"IVOL (%)": 21.})], book, "2026-11-20")


def test_structured_surface_preserves_requested_date_without_inventing_quote_date():
    _, book = structured_fixture()
    row = {"AS_OF_DATE": pd.Timestamp("2026-11-20"), "SECURITY": "SPX Index", "UNDERLYING_SECURITY": "SPX Index",
           "TENOR": "30DAY", "MONEYNESS (%)": 100., "IVOL (%)": 15.,
           "FIELD": "30DAY_IMPVOL_100%MNY_DF", "sheet": "SPX Index", "row": 2}
    result = latest.structured_records([row], book, "2026-11-20")
    assert result["vol_nodes"][0]["quote_date"] is None
    assert result["vol_nodes"][0]["moneyness"] == 1.
    with pytest.raises(ValueError, match="coordinate"):
        latest.structured_records([dict(row, TENOR="60DAY")], book, "2026-11-20")


def test_committed_clean_workbook_matches_139_positions_and_135_surface_nodes():
    review = latest.previous.load_review()
    result = latest.structured_iv_data(latest.ROOT / "data/sheets/Project10_IV_By_Underlying.xlsx", review.book, "2026-11-20")
    audit = result["structured_audit"]
    assert (audit["asset_sheets"], audit["total_observations"], audit["standard_surface_nodes"],
            audit["unique_option_securities"], audit["matched_fund_position_records"]) == (70, 273, 135, 138, 139)
    assert not audit["unmatched_option_securities"]
    assert not result["conflicts"]
    for row in review.book.itertuples():
        if (row.ticker, row.holding_id) in result["unique"]:
            assert latest.validate_quote(row, result["unique"][(row.ticker, row.holding_id)], "2026-11-20") == "same_date_terms_matched_iv"
