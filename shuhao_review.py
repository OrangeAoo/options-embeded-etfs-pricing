"""Additive Project 10 research review. Never modifies original code or data.

python shuhao_review.py
pytest tests test_shuhao_review.py

Greeks remain research estimates: flat rates, missing dividend yields, European
exercise approximation and unconfirmed snapshot conventions are disclosed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from core import data_io, greek_engine as engine
from core.impact import index_family

ROOT = Path(__file__).resolve().parent
GREEKS = ["mm_dollar_gamma_1pct", "mm_dollar_vega_1vol"]
READY = {"bloomberg_iv", "market_value_implied_iv"}


def implied_vol(premium, spot, strike, time_years, rate, dividend_yield, cp):
    """Invert a non-negative per-underlying option mark, enforcing BSM bounds.

    A successful inversion is an estimate conditional on the supplied mark and
    BSM inputs; it is not a replacement for an observed OVDV surface.
    """
    numbers = [premium, spot, strike, time_years, rate, dividend_yield]
    if not all(np.isfinite(v) for v in numbers) or min(spot, strike, time_years) <= 0 or cp not in {"C", "P"}:
        raise ValueError("invalid price, contract terms or time to expiry")
    prepaid_spot = spot * np.exp(-dividend_yield * time_years)
    pv_strike = strike * np.exp(-rate * time_years)
    lower = max(0.0, prepaid_spot - pv_strike) if cp == "C" else max(0.0, pv_strike - prepaid_spot)
    upper = prepaid_spot if cp == "C" else pv_strike
    tolerance = max(1e-8, spot * 1e-10)
    if premium <= lower + tolerance or premium >= upper - tolerance:
        raise ValueError("mark outside strict BSM price bounds or IV not identifiable at a bound")
    def difference(vol):
        return float(engine.bs_greeks(spot, strike, time_years, rate, dividend_yield, vol, cp)["price"]) - premium
    if difference(1e-6) * difference(5.0) > 0:
        raise ValueError("no IV root in the explicit (0, 500%) bracket")
    return float(brentq(difference, 1e-6, 5.0, xtol=1e-12))


def apply_unit_overrides(book, overrides):
    out = book.copy()
    if not (np.isfinite(out.multiplier) & (out.multiplier > 0)).all():
        raise ValueError("missing or invalid contract multiplier; do not silently assume 100")
    out["raw_position"] = out["position"]
    out["units"] = "contracts"
    out["unit_status"] = "inherited_contract_convention_unconfirmed"
    for rule in overrides:
        mask = (out.ticker == rule["ticker"]) & (out.holding_id == rule["holding_id"])
        if int(mask.sum()) != 1:
            raise ValueError(f"unit override must match exactly one row: {rule['holding_id']}")
        if not np.isclose(float(out.loc[mask, "position"].iloc[0]), rule["expected_raw_position"], rtol=0, atol=1e-9):
            raise ValueError(f"snapshot changed; recheck unit evidence: {rule['holding_id']}")
        if rule["units"] not in {"shares", "contracts"}:
            raise ValueError("invalid unit override")
        out.loc[mask, "units"] = rule["units"]
        out.loc[mask, "unit_status"] = rule["status"]
    return engine.normalize_book(out)


def analytical_box_ids(book):
    """Exact same-expiry European box identity, independent of assumed IV.

    All four signed legs must be present with equal underlying-unit sizes.
    This establishes net delta/gamma/vega = 0, not zero per-leg risk.
    """
    result = set()
    for _, group in book.groupby(["fund", "underlying", "expiry"], dropna=False):
        quantities = group.assign(signed_size=group.contracts * group.multiplier).groupby(["strike", "cp"])["signed_size"].sum()
        strikes = sorted(group.strike.unique())
        if len(strikes) != 2 or set(quantities.index) != {(k, cp) for k in strikes for cp in ("C", "P")}:
            continue
        lo, hi = strikes
        q = float(quantities.loc[(lo, "C")])
        expected = [q, -q, -q, q]
        actual = [quantities.loc[(lo, "C")], quantities.loc[(lo, "P")], quantities.loc[(hi, "C")], quantities.loc[(hi, "P")]]
        if q != 0 and np.allclose(actual, expected, rtol=1e-12, atol=1e-8):
            result.update(group.index)
    return result


def expiry_buckets(priced):
    out = priced.copy()
    out["expiry_bucket"] = pd.cut(out["dte"], [-1, 0, 7, 30, 91, np.inf],
                                  labels=["0DTE", "1d-1w", "1w-1m", "1-3m", ">3m"])
    return out


def price_subset(book, market):
    if book.empty:
        return pd.DataFrame()
    if not (np.isfinite(book.iv) & (book.iv > 0)).all():
        raise ValueError("all priced rows need explicit positive IV; no silent flat-vol fallback")
    if ((book.expiry - pd.Timestamp(market.valuation_date)).dt.days <= 0).any():
        raise ValueError("expiry-day/expired positions require a timestamp and roll/settlement policy")
    priced = engine.price_book(book, replace(market, use_book_iv=True, default_vol=None, surfaces={}))
    if not np.isfinite(priced[GREEKS].to_numpy()).all():
        raise ValueError("non-finite computed Greeks")
    return expiry_buckets(priced)


def scenario_ladder(book, market, shocks, *, mode="parallel", vol_mode="sticky_strike", days_forward=0, vol_shift=0.0):
    """Native-unit hedge change times NEW spot; zero shock preserves per-leg IV.

    sticky_delta requires a supplied surface for every underlying; the imported
    surface implements a K/S smile-following approximation, which is disclosed.
    No actual OVDV surface is present in the current snapshot.
    """
    if mode not in {"parallel", "spx_only", "ndx_only"}:
        raise ValueError("unknown spot shock mode")
    if vol_mode not in {"sticky_strike", "sticky_delta"}:
        raise ValueError("unknown vol mode")
    if not np.isfinite(days_forward) or days_forward < 0:
        raise ValueError("days_forward must be non-negative")
    if book.empty:
        raise ValueError("no eligible book")
    if days_forward >= (book.expiry - pd.Timestamp(market.valuation_date)).dt.days.min():
        raise ValueError("time ladder crosses an expiry: refresh actual rolled/settled holdings first")
    if vol_mode == "sticky_delta":
        missing = [u for u in book.underlying.unique() if u not in market.surfaces or isinstance(market.surfaces[u], engine.FlatVol)]
        if missing:
            raise ValueError(f"sticky-delta requires real non-flat surfaces for: {sorted(missing)}")
    base = price_subset(book, market)
    base_units = -base.contracts.to_numpy() * base.multiplier.to_numpy() * base.delta.to_numpy()
    rows = []
    for shock in shocks:
        if not np.isfinite(shock) or shock <= -1:
            raise ValueError("spot shocks must be finite and greater than -100%")
        spots = {}
        for u, value in market.spots.items():
            moves = mode == "parallel" or index_family(u) == ("SPX" if mode == "spx_only" else "NDX")
            spots[u] = value * (1 + float(shock) if moves else 1)
        shifted = replace(market, spots=spots, valuation_date=(pd.Timestamp(market.valuation_date) + pd.Timedelta(days=days_forward)).date())
        scenario_book = book.copy()
        if vol_mode == "sticky_delta":
            for u, idx in scenario_book.groupby("underlying").groups.items():
                surface = market.surfaces[u]
                original_t = (book.loc[idx, "expiry"] - pd.Timestamp(market.valuation_date)).dt.days.to_numpy() / 365
                new_t = (book.loc[idx, "expiry"] - pd.Timestamp(shifted.valuation_date)).dt.days.to_numpy() / 365
                k = book.loc[idx, "strike"].to_numpy()
                anchor = surface.iv(k, original_t, spot=market.spots[u], mode="sticky_delta")
                target = surface.iv(k, new_t, spot=spots[u], mode="sticky_delta")
                scenario_book.loc[idx, "iv"] += target - anchor
        scenario_book["iv"] += vol_shift
        priced = price_subset(scenario_book, shifted)
        units = -priced.contracts.to_numpy() * priced.multiplier.to_numpy() * priced.delta.to_numpy()
        row = {"shock": float(shock), "days_forward": float(days_forward), "mode": mode,
               "vol_mode": vol_mode, "vol_shift": float(vol_shift),
               "hedge_trade_usd": float(np.sum(-(units - base_units) * priced.spot.to_numpy()))}
        row.update({key: float(priced[key].sum()) for key in GREEKS})
        rows.append(row)
    return pd.DataFrame(rows)


def gamma_flip(ladder):
    """Return a genuine sign crossing; an identically zero curve has no flip."""
    series = ladder.groupby("shock")[GREEKS[0]].sum().sort_index()
    if series.empty or not np.isfinite(series).all():
        return None
    tolerance = max(1e-8, series.abs().max() * 1e-10)
    series = series[series.abs() > tolerance]
    crossings = []
    for (x0, y0), (x1, y1) in zip(series.items(), list(series.items())[1:]):
        if y0 * y1 < 0:
            crossings.append(float(x0 - y0 * (x1 - x0) / (y1 - y0)))
    return min(crossings, key=abs) if crossings else None


def scale_child_positions(child_book, held_shares, child_shares_outstanding):
    if not np.isfinite(held_shares) or not np.isfinite(child_shares_outstanding) or child_shares_outstanding <= 0 or held_shares < 0 or held_shares > child_shares_outstanding:
        raise ValueError("look-through requires a valid same-date ownership fraction in [0, 1]")
    out = child_book.copy()
    out["position"] *= held_shares / child_shares_outstanding
    return out


def infer_eln_call_book(*, fund, underlying, option_notional_usd, spot, strikes, expiries, allocation_weights):
    """Explicit scenario inputs only: ELN asset allocation is not option notional."""
    if option_notional_usd is None or not np.isfinite(option_notional_usd) or option_notional_usd <= 0 or spot <= 0:
        raise ValueError("provide calibrated option-equivalent notional; do not use an ELN allocation limit")
    weights = np.asarray(allocation_weights, float)
    if len(strikes) != len(expiries) or len(strikes) != len(weights) or not len(weights) or not np.isfinite(weights).all() or (weights <= 0).any() or not np.isclose(weights.sum(), 1):
        raise ValueError("strike/expiry/weight ladder must align and positive weights sum to one")
    if not np.isfinite(strikes).all() or min(strikes) <= 0:
        raise ValueError("invalid inferred strike")
    return pd.DataFrame({"fund": fund, "ticker": fund.split()[0], "underlying": underlying,
                         "holding_id": [f"{fund}:inferred:{i}" for i in range(len(weights))],
                         "expiry": pd.to_datetime(expiries), "strike": strikes, "cp": "C",
                         "position": -option_notional_usd * weights / (spot * 100),
                         "units": "contracts", "multiplier": 100, "source": "inferred",
                         "inference_note": "explicit user/calibration inputs; no live fund inference supplied by default"})


@dataclass
class Review:
    etfs: pd.DataFrame
    book: pd.DataFrame
    market: engine.MarketData
    coverage: pd.DataFrame
    source_audit: list
    dependencies: list
    fingerprints: dict
    assumptions: dict


def load_review(sheets_dir=data_io.SHEETS_DIR, config_path=ROOT / "shuhao_review_inputs.json"):
    sheets = Path(sheets_dir)
    config = json.loads(Path(config_path).read_text())
    etfs = data_io.load_etf_list(sheets).sort_values("aum_usd", ascending=False).reset_index(drop=True)
    if etfs.ticker.duplicated().any() or etfs.fund.duplicated().any() or len(etfs) != 20:
        raise ValueError("review requires 20 distinct funds; reconcile the universe before running")
    etfs["rank"] = np.arange(1, len(etfs) + 1)
    market = data_io.load_market(sheets, rate=config["rate"], default_vol=None)
    if str(market.valuation_date) != config["valuation_date"]:
        raise ValueError("configured date disagrees with market snapshot")
    terms = pd.read_csv(sheets / "Option_Book_Copy.csv")
    terms["ETF_Ticker"] = terms["ETF_Ticker"].ffill()
    terms = terms.rename(columns={"ETF_Ticker": "ticker", "Option Security ID": "holding_id", "Underlying": "underlying", "Call / Put": "cp", "Strike": "strike", "Expiry": "expiry", "Contract Size": "multiplier", "IV_MID": "iv_pct"})
    if terms.duplicated(["ticker", "holding_id"]).any():
        raise ValueError("duplicate option term keys")
    positions, audits, dependencies = [], [], []
    for etf in etfs.itertuples():
        raw = pd.read_csv(sheets / "holdings" / f"{etf.ticker}.csv")
        option = raw.SECURITY_TYP.astype(str).str.contains("Option", case=False, na=False)
        pos = raw.loc[option, ["ID", "id().position.POSITION", "id().reported_mkt_val.REPORTED_MKT_VAL", "SECURITY_TYP.ID_DATE"]].copy()
        pos.columns = ["holding_id", "position", "reported_value_usd", "holding_date"]
        pos["ticker"] = etf.ticker
        if pos.holding_id.duplicated().any():
            raise ValueError(f"duplicate holdings keys for {etf.ticker}")
        missing_terms = pos.loc[~pos.holding_id.isin(terms.loc[terms.ticker == etf.ticker, "holding_id"])]
        missing_active = missing_terms.loc[pd.to_numeric(missing_terms.position, errors="coerce") != 0, "holding_id"].tolist()
        positions.append(pos)
        weights = pd.to_numeric(raw["id().weights.WEIGHTS"], errors="coerce")
        values = pd.to_numeric(raw["id().reported_mkt_val.REPORTED_MKT_VAL"], errors="coerce")
        mask = weights.abs().gt(1e-6) & values.ne(0) & values.notna()
        implied_nav = float((values[mask] / weights[mask] * 100).median()) if mask.any() else None
        audits.append({"ticker": etf.ticker, "aum_usd": etf.aum_usd, "holdings_implied_nav_usd": implied_nav,
                       "nav_to_list_aum": implied_nav / etf.aum_usd if implied_nav is not None else None,
                       "active_options_missing_terms": missing_active,
                       "security_types": raw.SECURITY_TYP.value_counts().to_dict()})
        if etf.ticker in {"BUFR", "BUFD"}:
            for row in raw.loc[raw.SECURITY_TYP == "ETP"].itertuples(index=False, name=None):
                child = row[0].split()[0]
                dependencies.append({"parent": etf.ticker, "child": child,
                                     "parent_held_shares": float(row[1]), "held_value_usd": float(row[6]),
                                     "required": "same-date child option holdings and shares outstanding",
                                     "source_url": f"https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker={child}",
                                     "overlap_with_selected_fund": child in set(etfs.ticker)})
    book = terms.merge(pd.concat(positions, ignore_index=True), on=["ticker", "holding_id"], how="left", validate="one_to_one", indicator=True)
    book["fund"] = book.ticker.map(dict(zip(etfs.ticker, etfs.fund)))
    book["underlying"] = book.underlying.map(data_io.normalize_underlying)
    book["cp"] = book.cp.astype(str).str.upper().str[0]
    for col in ["strike", "position", "multiplier", "reported_value_usd", "iv_pct"]:
        book[col] = pd.to_numeric(book[col], errors="coerce")
    book["expiry"] = pd.to_datetime(book.expiry, errors="coerce")
    book["iv"] = book.iv_pct / 100
    book["source"] = "observed"
    book = apply_unit_overrides(book, config.get("unit_overrides", []))
    def source_date(value):
        try:
            numeric = float(value)
            if numeric > 20000:
                return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=numeric)).date()
        except (ValueError, TypeError):
            pass
        date = pd.to_datetime(value, errors="coerce")
        return date.date() if pd.notna(date) else None
    book["holding_asof"] = book.holding_date.map(source_date)
    book["dte"] = (book.expiry - pd.Timestamp(market.valuation_date)).dt.days
    book["pricing_status"] = "missing_iv"
    book["iv_recovery_note"] = ""
    box_ids = analytical_box_ids(book[(book.position != 0) & (book.dte > 0)])
    for idx, row in book.iterrows():
        valid = row["_merge"] == "both" and np.isfinite(row.position) and np.isfinite(row.multiplier) and row.multiplier > 0 and np.isfinite(row.strike) and row.strike > 0 and pd.notna(row.expiry) and row.cp in {"C", "P"} and row.underlying in market.spots and row.holding_asof == market.valuation_date
        if not valid:
            status = "invalid_or_unmatched_input"
        elif row.position == 0:
            status = "zero_position"
        elif row.dte <= 0:
            status = "expiry_or_roll_pending"
        elif idx in box_ids:
            status = "analytic_box_net_zero"
        elif np.isfinite(row.iv) and row.iv > 0:
            status = "bloomberg_iv"
        else:
            try:
                premium = row.reported_value_usd / (row.contracts * row.multiplier)
                iv = implied_vol(premium, market.spots[row.underlying], row.strike, row.dte / 365, market.rate, market.div_yields.get(row.underlying, 0), row.cp)
                book.loc[idx, "iv"] = iv
                book.loc[idx, "iv_recovery_note"] = "BSM inversion of signed reported market value / signed underlying units; mark and unit convention unconfirmed"
                status = "market_value_implied_iv"
            except ValueError as exc:
                book.loc[idx, "iv_recovery_note"] = str(exc)
                status = "missing_or_inconsistent_iv"
        book.loc[idx, "pricing_status"] = status
    rows = []
    for etf in etfs.itertuples():
        fund_book = book[book.ticker == etf.ticker]
        counts = fund_book.pricing_status.value_counts().to_dict()
        audit = next(a for a in audits if a["ticker"] == etf.ticker)
        if etf.ticker in config["pending_funds"]:
            status = config["pending_funds"][etf.ticker]["status"]
        elif counts.get("analytic_box_net_zero", 0) and set(counts) <= {"analytic_box_net_zero", "zero_position"}:
            status = "analytic_box_only_size_still_unverified"
        elif counts.get("expiry_or_roll_pending", 0):
            status = "partial_or_expiry_pending"
        elif any(k not in READY | {"zero_position"} for k in counts) or audit["active_options_missing_terms"]:
            status = "partial_missing_inputs"
        else:
            status = "priced_research_estimate"
        rows.append({"ticker": etf.ticker, "rank": etf.rank, "aum_usd": etf.aum_usd,
                     "status": status, "n_records": len(fund_book), "pricing_counts": counts,
                     "aum_snapshot_verified": False})
    raw_market = pd.read_csv(sheets / "Underlying_Market_Copy.csv")
    missing_div = raw_market.loc[pd.to_numeric(raw_market["Dividend Yield (to collect)"], errors="coerce").isna(), "Underlying"].dropna().tolist()
    fingerprints = {str(p.relative_to(sheets)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sheets.rglob("*.csv"))}
    assumptions = {"rate": market.rate, "rate_note": config["rate_note"], "missing_dividend_yields_assumed_zero": missing_div,
                   "exercise_model": "European BSM for all supplied options, including an approximation for single-stock/American options",
                   "street_convention": "MM = -ETF, conditional counterparty exposure rather than total dealer inventory",
                   "snapshot": "date-only November 20 inputs; no intraday expiry/roll timestamp; AUM date not independently verified",
                   "iv_estimates": "market-value implied IV is separately tagged, not an observed vol surface",
                   "unit_overrides": config.get("unit_overrides", []), "pending_funds": config["pending_funds"],
                   "source_notes": config.get("source_notes", [])}
    return Review(etfs, book, market, pd.DataFrame(rows), audits, dependencies, fingerprints, assumptions)


def summarise(priced, by=None):
    if priced.empty:
        return []
    if by is None:
        return {key: float(priced[key].sum()) for key in GREEKS}
    return priced.groupby(by, observed=True)[GREEKS].sum().reset_index().to_dict("records")


def issuer_lookthrough_preview(review, path):
    if not path.exists():
        return {"status": "issuer cache unavailable", "required_children": review.dependencies}
    cached = json.loads(path.read_text())
    funds = {r["ticker"]: r for r in cached["funds"]}
    target_date = str(review.market.valuation_date)
    def usable(record):
        return record.get("snapshot_usable_for_research", False) and record.get("holdings_asof") == target_date == record.get("pricing_asof")
    previews = []
    for parent in ["BUFR", "BUFD"]:
        p = funds.get(parent, {})
        result = {"parent": parent, "included_in_bloomberg_aggregate": False,
                  "note": "separate issuer-source partial preview; overlap and snapshot differences prevent automatic addition", "children": []}
        if not usable(p):
            result["status"] = "parent_snapshot_conflict_or_missing"
        else:
            result["status"] = "partial_issuer_preview"
            rows = []
            for dep in [d for d in review.dependencies if d["parent"] == parent]:
                child = funds.get(dep["child"], {})
                item = {"ticker": dep["child"], "overlap_with_selected_fund": dep["overlap_with_selected_fund"]}
                held = next((h for h in p.get("holdings", []) if h["identifier"] == dep["child"]), None)
                if not usable(child) or held is None:
                    item["status"] = "snapshot_conflict_or_missing"
                else:
                    item["status"] = "child_terms_available"
                    item["ownership_fraction"] = held["quantity"] / child["shares_outstanding_from_nav"]
                    item["parent_weight_pct"] = held["weight_pct"]
                    options = [h for h in child.get("holdings", []) if "expiry" in h and h["quantity"] != 0]
                    deep_calls = [h for h in options if h["cp"] == "C" and h["strike"] < .1 * review.market.spots["SPY"]]
                    same_sizes = len(options) == 4 and np.allclose([abs(h["quantity"]) for h in options], abs(options[0]["quantity"]), rtol=0, atol=1e-9)
                    scale_ok = bool(deep_calls) and all(.5 < h["market_value_usd"] / (h["quantity"] * 100 * review.market.spots["SPY"]) < 1.5 for h in deep_calls)
                    if not same_sizes or not scale_ok or not 0 <= item["ownership_fraction"] <= 1:
                        item["status"] = "contract_unit_or_ownership_check_required"
                        result["children"].append(item)
                        continue
                    child_rows = []
                    item["leg_checks"] = []
                    for h in options:
                        dte = (pd.Timestamp(h["expiry"]) - pd.Timestamp(review.market.valuation_date)).days
                        if dte <= 0:
                            item["leg_checks"].append({"identifier": h["identifier"], "status": "expiry_or_roll_pending"})
                            continue
                        try:
                            iv = implied_vol(h["market_value_usd"] / (h["quantity"] * 100), review.market.spots["SPY"], h["strike"], dte / 365, review.market.rate, review.market.div_yields["SPY"], h["cp"])
                        except ValueError as exc:
                            item["leg_checks"].append({"identifier": h["identifier"], "status": "unpriceable_mark", "note": str(exc)})
                            continue
                        item["leg_checks"].append({"identifier": h["identifier"], "status": "issuer_price_implied_iv", "iv": iv})
                        child_rows.append({"fund": parent, "ticker": parent, "holding_id": h["identifier"],
                                     "underlying": "SPY", "cp": h["cp"], "strike": h["strike"], "expiry": pd.Timestamp(h["expiry"]),
                                     "position": h["quantity"], "units": "contracts", "multiplier": 100,
                                     "iv": iv, "source": "issuer_price_implied", "child": dep["child"]})
                    if child_rows:
                        scaled = scale_child_positions(pd.DataFrame(child_rows), held["quantity"], child["shares_outstanding_from_nav"])
                        rows.extend(scaled.to_dict("records"))
                    item["n_priceable_legs"] = sum(r["child"] == dep["child"] for r in rows)
                result["children"].append(item)
            child_book = pd.DataFrame(rows)
            result["priced_legs"] = len(child_book)
            result["partial_greeks"] = summarise(price_subset(child_book, review.market)) if len(child_book) else None
            result["parent_weight_with_some_priced_child_legs_pct"] = sum(c.get("parent_weight_pct", 0) for c in result["children"] if c.get("n_priceable_legs", 0) > 0)
            result["parent_weight_with_all_four_child_legs_priced_pct"] = sum(c.get("parent_weight_pct", 0) for c in result["children"] if c.get("n_priceable_legs", 0) == 4)
        previews.append(result)
    return {"source_funds_captured": len(funds), "source_funds_reconciled": sum(r.get("snapshot_usable_for_research", False) for r in funds.values()),
            "source_conflicts": [{"ticker": t, "note": r["validation_note"]} for t, r in funds.items() if not r.get("snapshot_usable_for_research")],
            "parents": previews}


def build_results(review, issuer_path=ROOT / "shuhao_issuer_snapshot.json"):
    outputs = {}
    for name, mask in [("quoted_iv_live_subset", review.book.pricing_status.eq("bloomberg_iv")),
                       ("quoted_plus_price_implied_live_subset", review.book.pricing_status.isin(READY))]:
        selected = review.book.loc[mask].copy()
        priced = price_subset(selected, review.market)
        outputs[name] = {"n_legs": len(priced), "funds_with_at_least_one_priced_leg": sorted(selected.ticker.unique().tolist()),
                         "scope": "partial live-option research estimate; excludes expiry/roll-pending legs and the five missing overlays",
                         "totals": summarise(priced), "by_fund": summarise(priced, "ticker"),
                         "by_underlying": summarise(priced, "underlying"), "by_expiry": summarise(priced, "expiry_bucket")}
        if not priced.empty:
            ladder = scenario_ladder(selected, review.market, np.round(np.linspace(-.10, .10, 21), 4))
            zero = ladder[ladder.shock == 0].iloc[0]
            outputs[name]["zero_shock_reconciles"] = all(np.isclose(zero[k], priced[k].sum(), atol=1e-6, rtol=1e-12) for k in GREEKS)
            outputs[name]["spot_ladder"] = ladder.to_dict("records")
            outputs[name]["gamma_flip"] = gamma_flip(ladder)
            outputs[name]["spx_only_ladder"] = scenario_ladder(selected, review.market, [-.10, -.05, 0, .05, .10], mode="spx_only").to_dict("records")
            outputs[name]["spot_vol_stress"] = [scenario_ladder(selected, review.market, [shock], vol_shift=vol).iloc[0].to_dict() for shock, vol in [(-.20, .10), (-.10, .05), (.10, -.02)]]
            max_days = int((selected.expiry - pd.Timestamp(review.market.valuation_date)).dt.days.min()) - 1
            outputs[name]["time_ladder"] = [scenario_ladder(selected, review.market, [-.05, 0, .05], days_forward=d).to_dict("records") for d in [0, 1, 3, 5] if d <= max_days]
    legacy_funds = review.coverage.loc[review.coverage.n_records > 0]
    box_rows = review.book.loc[(review.book.ticker == "BOXX") & review.book.position.ne(0)]
    full_box_identity = not box_rows.empty and box_rows.pricing_status.eq("analytic_box_net_zero").all()
    return {"valuation_date": str(review.market.valuation_date), "readiness": "research prototype; full Top-20 exposure and IV-response conclusions remain pending",
            "universe_aum_usd": float(review.etfs.aum_usd.sum()), "universe": review.etfs[["rank", "ticker", "aum_usd", "bucket"]].to_dict("records"),
            "legacy_coverage": {"funds": len(legacy_funds), "aum_usd": float(legacy_funds.aum_usd.sum()), "aum_share": float(legacy_funds.aum_usd.sum() / review.etfs.aum_usd.sum()), "interpretation": "funds with any original option records; not validated full-fund risk coverage"},
            "pricing_status_counts": review.book.pricing_status.value_counts().to_dict(), "coverage": review.coverage.to_dict("records"),
            "source_audit": review.source_audit, "lookthrough_data_requests": review.dependencies, "assumptions": review.assumptions,
            "analytic_box": {"fund": "BOXX", "n_legs": int(review.book.pricing_status.eq("analytic_box_net_zero").sum()), "net_delta_gamma_vega": 0 if full_box_identity else None,
                             "note": "European four-leg identity; per-leg IVs and the fund-size discrepancy are unresolved; no fabricated IVs"},
            "scenario_conventions": {"sticky_strike": "retains each leg's base IV", "sticky_delta": "blocked without non-flat OVDV surfaces; K/S proxy must be labelled if used", "hedge_trade": "negative change in signed MM native-unit delta, multiplied by shocked native spot", "time_ladder": "static book before first expiry; does not invent new roll positions"},
            "outputs": outputs, "issuer_lookthrough_preview": issuer_lookthrough_preview(review, issuer_path),
            "input_sha256": review.fingerprints,
            "leg_audit": review.book[["ticker", "holding_id", "position", "raw_position", "units", "unit_status", "expiry", "dte", "iv", "pricing_status", "iv_recovery_note"]].assign(expiry=lambda d: d.expiry.astype(str)).replace({np.nan: None}).to_dict("records")}


def write_html(result, path):
    def table(records):
        if not records:
            return "<p>No eligible rows.</p>"
        return pd.DataFrame(records).to_html(index=False, escape=True, border=0, float_format=lambda v: f"{v:,.4f}")
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Project 10 data review</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 24px;color:#182633}h1,h2{line-height:1.25}h2{margin-top:42px}table{border-collapse:collapse;font-size:13px;width:100%;display:block;overflow:auto}th,td{border:1px solid #d8dfe4;padding:9px 12px;text-align:left;vertical-align:top}th{background:#edf3f7}tr:nth-child(even){background:#fafbfd}code{background:#edf1f4;padding:2px 5px}a{color:#165a9c}details{margin:18px 0}.notice{padding:16px;background:#fff4d9;border-left:4px solid #ae7216}small{color:#52606b}@media print{body{max-width:none;margin:0;font-size:11px}table{display:table;font-size:9px}details>*{display:block}}</style>",
             "<h1>Project 10 data review and corrected research pipeline</h1>",
             f"<p>Valuation date: {html.escape(result['valuation_date'])}. Original files are retained. Run <code>python shuhao_review.py</code>.</p>",
             f"<p class='notice'>{html.escape(result['readiness'])}. All reported Greeks below are partial, assumption-dependent estimates.</p>",
             "<h2>Fund coverage</h2>", table([{k:v for k,v in row.items() if k != "pricing_counts"} for row in result["coverage"]]),
             "<h2>Why records are held out</h2>", table([{"status": k, "records": v} for k,v in result["pricing_status_counts"].items()]),
             "<h2>Snapshot and sizing checks</h2>", table([{k:v for k,v in row.items() if k not in {"security_types", "active_options_missing_terms"}} for row in result["source_audit"]])]
    for name, output in result["outputs"].items():
        parts += [f"<h2>{html.escape(name.replace('_', ' '))}</h2>", f"<p>{output['n_legs']} live positions; {html.escape(output['scope'])}. Zero-shock reconciliation: {output.get('zero_shock_reconciles')}.</p>", table(output["by_fund"]),
                  "<details><summary>Spot ladder and correctly measured hedge trades</summary>", table(output.get("spot_ladder", [])), "</details>"]
    parts += ["<h2>Issuer look-through progress</h2><pre style='white-space:pre-wrap'>", html.escape(json.dumps(result["issuer_lookthrough_preview"], indent=2)), "</pre>",
              "<h2>Missing overlay inputs</h2>", table([{"fund": k, "status": v["status"], "next_inputs": "; ".join(v.get("required", ["same-date child holdings and ownership fractions"]))} for k,v in result["assumptions"]["pending_funds"].items()]),
              "<h2>Assumptions and provenance</h2><p>Flat rate and missing dividends remain explicit model assumptions. Market-value implied IVs are reconstructed estimates. Dealer exposure assumes MM = -ETF. Same-day expired legs remain pending until reset/settlement timing is known. BOXX is verified only through the four-leg risk identity.</p>",
              "<p>See <code>shuhao_review_results.json</code> for every leg status, source URL, recovery failure and input SHA-256.</p></html>"]
    Path(path).write_text("\n".join(parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.output_dir.resolve() == (ROOT / "data").resolve() or (ROOT / "data").resolve() in args.output_dir.resolve().parents:
        raise ValueError("write review outputs outside original data/")
    review = load_review()
    result = build_results(review)
    (args.output_dir / "shuhao_review_results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    write_html(result, args.output_dir / "shuhao_review_report.html")
    print(json.dumps({"coverage": result["legacy_coverage"], "pricing_status_counts": result["pricing_status_counts"], "zero_shock_reconciles": {k:v.get("zero_shock_reconciles") for k,v in result["outputs"].items()}}, indent=2))


if __name__ == "__main__":
    main()
