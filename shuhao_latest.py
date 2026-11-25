"""Additive, offline Project 10 implementation using saved Bloomberg responses.

Original core/, CSVs, and teammate notebooks are never modified. No Bloomberg
subscription or Excel installation is needed to reproduce the saved snapshot.

    python shuhao_latest.py
    pytest tests test_shuhao_review.py test_shuhao_latest.py

This is a partial-book research calculation, not a verified Top 20 total.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import openpyxl
import pandas as pd
from scipy.optimize import brentq
from scipy.special import ndtr

from core import greek_engine as engine
from core.data_io import normalize_underlying
import shuhao_review as previous

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "shuhao_latest_inputs.json"
RISK = ["etf_dollar_gamma_1pct", "etf_dollar_vega_1vol", "mm_dollar_gamma_1pct", "mm_dollar_vega_1vol"]


def number(value):
    """A Bloomberg text error, boolean or date is never an observed number."""
    if isinstance(value, (bool, str)) or value is None:
        return None
    if isinstance(value, (int, float, np.number)) and np.isfinite(value):
        return float(value)
    return None


def asdate(value):
    if value is None or isinstance(value, (int, float)):
        return None
    try:
        x = pd.Timestamp(value)
        return None if pd.isna(x) else x.date().isoformat()
    except (TypeError, ValueError):
        return None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protected_files():
    paths = list((ROOT / "core").glob("*.py")) + list((ROOT / "tests").glob("*.py"))
    paths += list((ROOT / "data/sheets").rglob("*.csv"))
    paths += [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / "Project10_Bloomberg_Extractor.xlsx"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths) if p.exists()}


def workbook_info(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": digest(path)}


def holdings_summary(headers, data):
    records = [dict(zip(headers, row)) for row in data]
    # POSITION_TYPE (L/S) is not SECURITY_TYP. Use returned column names.
    types = Counter(str(x.get("SECURITY_TYP")) for x in records)
    return {"rows": len(records), "security_types": dict(types),
            "reported_dates": sorted({asdate(x.get("SECURITY_TYP.ID_DATE")) for x in records if asdate(x.get("SECURITY_TYP.ID_DATE"))}),
            "market_value_sum": sum(number(x.get("id().reported_mkt_val.REPORTED_MKT_VAL")) or 0 for x in records),
            "listed_option_or_ELN_rows": sum(any(k in str(x.get("SECURITY_TYP")).lower() for k in ["option", "linked", "swap", "note"]) for x in records)}


def extractor_data(path, target):
    """Read cached values only. Never refresh, evaluate or overwrite Excel."""
    w = openpyxl.load_workbook(path, data_only=True, read_only=True)
    vols, history, holdings, duplicate_sheets = [], [], {}, []
    for name in ["波动率", "历史行情"]:
        s = w[name]
        for r, row in enumerate(s.iter_rows(min_row=9, max_col=6, values_only=True), 9):
            ticker, field = row[1], row[3]
            if not isinstance(ticker, str) or not isinstance(field, str):
                continue
            if "_IMPVOL_" not in field and name == "波动率":
                continue
            request_date, value = asdate(row[4]), row[5]
            record = {"ticker": ticker, "field": field, "requested_date": request_date,
                      "value": number(value), "raw_value": value,
                      "quote_date": None, "source": f"{path.name}:{name}!F{r}"}
            if name == "波动率":
                match = re.fullmatch(r"(\w+)_IMPVOL_([\d.]+)%MNY_DF", field)
                if match:
                    record.update(tenor=match[1], moneyness=float(match[2]) / 100)
                    vols.append(record)
            else:
                history.append(record)
    for fund in ["JEPI", "JEPQ", "ACYN"]:
        versions = []
        for name in w.sheetnames:
            if not re.fullmatch(fund + r"持仓(?: \(\d+\))?", name):
                continue
            s = w[name]
            headers = next(s.iter_rows(min_row=12, max_row=12, max_col=9, values_only=True))[1:9]
            data = []
            for row in s.iter_rows(min_row=13, max_col=9, values_only=True):
                if row[1] is not None:
                    data.append(list(row[1:9]))
            versions.append((name, data))
        if versions:
            first = versions[0][1]
            if any(v != first for _, v in versions[1:]):
                raise ValueError(f"conflicting duplicate holdings sheets for {fund}")
            duplicate_sheets.append({"fund": fund, "sheets": [n for n, _ in versions], "identical": True})
            holdings[fund] = holdings_summary(headers, first)
    s = w["合约资料"]
    submitted = sum(bool(row[5]) for row in s.iter_rows(min_row=9, max_col=6, values_only=True))
    w.close()
    if any(x["requested_date"] != target for x in vols + history):
        raise ValueError("mixed requested dates in source workbook")
    return {"vol_nodes": vols, "history": history, "holdings": holdings,
            "duplicate_holdings_sheets": duplicate_sheets, "submitted_contract_ids": submitted}


def bql_data(path):
    """Parse headers, not numeric cell counts; DATE and strike are not IV.

    Duplicate tabs are alternative cached responses, not additional positions.
    Field conflicts remain visible; neither tab is assumed newer by its name.
    """
    w = openpyxl.load_workbook(path, data_only=True, read_only=True)
    responses, errors, surfaces, surface_nodes = [], [], [], []
    for name in w.sheetnames:
        if not name.startswith(("BQL逐腿", "BQL曲面")):
            continue
        s = w[name]
        # Materialise once: repeated random access to a read-only worksheet is slow.
        cells = {c.coordinate: c.value for row in s for c in row if c.value is not None}
        if name.startswith("BQL曲面"):
            for r in range(10, 4690, 30):
                a = r + 4
                response = cells.get(f"B{a}")
                nodes = []
                if response == "ID":
                    headers = {c: cells.get(f"{openpyxl.utils.get_column_letter(c)}{a}") for c in range(2,41)}
                    for c, field in headers.items():
                        if not isinstance(field,str): continue
                        m = re.fullmatch(r"IMPLIED_VOLATILITY\(DELTA\s*=\s*([\d.]+),\s*EXPIRY\s*=\s*([\d.]+)D\)",field,re.I)
                        if not m: continue
                        dates = [k for k,v in headers.items() if v == field + ".DATE"]
                        for rr in range(a+1,a+24):
                            value=number(cells.get(f"{openpyxl.utils.get_column_letter(c)}{rr}"))
                            if value is None or value <= 0: continue
                            quote_date=asdate(cells.get(f"{openpyxl.utils.get_column_letter(dates[0])}{rr}")) if dates else None
                            nodes.append({"ticker":cells.get(f"B{rr}"),"delta":float(m[1]),"tenor_days":float(m[2]),
                                          "iv_pct":value,"quote_date":quote_date,"sheet":name,
                                          "cell":f"{openpyxl.utils.get_column_letter(c)}{rr}"})
                surface_nodes.extend(nodes)
                surfaces.append({"sheet": name, "row": r, "ticker": cells.get(f"B{r}"),
                                 "tenor_days": number(cells.get(f"C{r}")),
                                 "response": str(response) if response is not None else None,
                                 "numeric_iv_nodes": len(nodes)})
            continue
        for r in range(10, 2860, 10):
            fund, ident = cells.get(f"B{r}"), cells.get(f"C{r}")
            if not isinstance(ident, str):
                continue
            a, d = r + 3, r + 4
            if cells.get(f"B{a}") != "ID":
                message = cells.get(f"B{a}")
                errors.append({"fund": fund, "id": ident, "sheet": name,
                               "cell": f"B{a}", "response": str(message) if message is not None else "empty"})
                continue
            record = {"fund": fund, "id": ident, "returned_id": cells.get(f"B{d}"),
                      "requested_date": asdate(cells.get(f"F{r}")), "sheet": name,
                      "source_cell": f"B{d}"}
            for c in range(3, 41):
                letter = openpyxl.utils.get_column_letter(c)
                key = cells.get(f"{letter}{a}")
                if isinstance(key, str):
                    record[key.replace("()", "")] = cells.get(f"{letter}{d}")
            responses.append(record)
    w.close()
    unique, conflicts = merge_responses(responses)
    return {"responses": responses, "unique": unique, "conflicts": conflicts,
            "nonresponses": errors, "surface_requests": surfaces, "surface_nodes":surface_nodes,
            "per_sheet_returned": dict(Counter(x["sheet"] for x in responses))}


def merge_responses(records):
    groups = {}
    for r in records:
        groups.setdefault((r["fund"], r["id"]), []).append(r)
    result, conflicts = {}, []
    keys = ["returned_id", "expire_dt", "strike_px", "put_call", "ivol(mid)", "ivol(mid).DATE", "exer_typ"]
    for key, versions in groups.items():
        score = lambda r: sum(number(r.get(k)) is not None for k in ["strike_px", "ivol(mid)", "px_bid", "px_ask", "px_last"])
        best = max(versions, key=score).copy()
        for field in keys:
            valid = [v[field] for v in versions if v.get(field) is not None and not str(v[field]).startswith("#")]
            values = set(str(v) for v in valid)
            if len(values) > 1:
                # Compare floating-point IVs with a much tighter tolerance than a vol point.
                numeric = [number(v) for v in valid]
                if not all(x is not None for x in numeric) or not np.allclose(numeric, numeric[0], rtol=1e-9, atol=1e-9):
                    conflicts.append({"fund": key[0], "id": key[1], "field": field, "values": sorted(values)})
                    best["conflicting_response"] = True
            if best.get(field) is None and valid:
                best[field] = valid[0]
        best["duplicate_response_count"] = len(versions)
        best["source_sheets"] = sorted({v["sheet"] for v in versions})
        result[key] = best
    return result, conflicts


def validate_quote(row, quote, target):
    if quote.get("conflicting_response"):
        return "conflicting_duplicate_response"
    if quote.get("returned_id") != row.holding_id:
        return "returned_id_mismatch"
    if asdate(quote.get("expire_dt")) != row.expiry.date().isoformat():
        return "expiry_mismatch"
    if number(quote.get("strike_px")) is None or not np.isclose(quote["strike_px"], row.strike, rtol=1e-10):
        return "strike_mismatch"
    if str(quote.get("put_call", "")).upper()[:1] != row.cp:
        return "call_put_mismatch"
    if asdate(quote.get("ivol(mid).DATE")) != target:
        return "iv_date_mismatch_or_missing"
    if number(quote.get("ivol(mid)")) is None or quote["ivol(mid)"] <= 0:
        return "iv_not_numeric_positive"
    if quote.get("requested_date") != target:
        return "request_date_mismatch"
    return "same_date_terms_matched_iv"


def bs(spot, strike, t, r, q, vol, cp):
    if t <= 0 or vol <= 0 or min(spot, strike) <= 0 or cp not in {"C", "P"}:
        raise ValueError("positive S/K/T/IV and C/P are required; 0DTE needs a timestamp")
    x = engine.bs_greeks(spot, strike, t, r, q, vol, cp)
    return {k: float(x[k]) for k in ["price", "delta", "gamma", "vega"]}


@lru_cache(maxsize=20000)
def crr_price(spot, strike, t, r, q, vol, cp, steps=400):
    if t <= 0 or vol <= 0 or min(spot, strike) <= 0 or steps < 3 or cp not in {"C", "P"}:
        raise ValueError("invalid American option input")
    dt = t / steps
    u = math.exp(vol * math.sqrt(dt)); d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0 <= p <= 1:
        raise ValueError("CRR probability outside [0,1]; increase time steps, do not clip it")
    sign = 1 if cp == "C" else -1
    j = np.arange(steps + 1)
    stocks = spot * u ** j * d ** (steps - j)
    values = np.maximum(sign * (stocks - strike), 0)
    discount = math.exp(-r * dt)
    level1 = level2 = None
    for n in range(steps - 1, -1, -1):
        values = discount * ((1 - p) * values[:-1] + p * values[1:])
        stocks = stocks[:-1] / d
        values = np.maximum(values, np.maximum(sign * (stocks - strike), 0))
        if n == 2: level2 = values.copy()
        if n == 1: level1 = values.copy()
    delta = (level1[1] - level1[0]) / (spot * (u - d))
    up_delta = (level2[2] - level2[1]) / (spot * (u * u - 1))
    down_delta = (level2[1] - level2[0]) / (spot * (1 - d * d))
    gamma = (up_delta - down_delta) / (spot * (u * u - d * d) / 2)
    intrinsic = max(sign * (spot - strike), 0)
    if intrinsic > 0 and abs(values[0] - intrinsic) < 1e-10:
        delta, gamma = sign, 0.0
    return float(values[0]), float(delta), float(gamma)


def model_greeks(spot, strike, t, r, q, vol, cp, exercise, steps=400):
    if exercise == "American":
        price, delta, gamma = crr_price(spot, strike, t, r, q, vol, cp, steps)
        bump = min(0.0001, vol / 10)
        vega = (crr_price(spot, strike, t, r, q, vol + bump, cp, steps)[0]
                - crr_price(spot, strike, t, r, q, vol - bump, cp, steps)[0]) / (2 * bump)
        return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "model": "American_CRR"}
    return dict(bs(spot, strike, t, r, q, vol, cp), model="European_BSM" if exercise == "European" else "BSM_exercise_unconfirmed")


@dataclass
class StrictSurface:
    """No flat extrapolation, filled holes, silent proxies or coordinate guesses."""
    frame: pd.DataFrame
    ref_spot: float
    basis: str
    rate: float
    dividend: float

    def __post_init__(self):
        if self.basis not in {"spot", "forward"}:
            raise ValueError("confirm spot or forward moneyness before using a surface")
        if self.frame.duplicated(["days", "moneyness"]).any():
            raise ValueError("duplicate surface coordinate")
        self.grid = self.frame.pivot(index="days", columns="moneyness", values="iv").sort_index().sort_index(axis=1)
        if self.grid.isna().any().any() or not (self.grid.to_numpy() > 0).all():
            raise ValueError("surface has missing or nonpositive nodes")
        self.days = self.grid.index.to_numpy(float)
        self.money = self.grid.columns.to_numpy(float)

    def tenor_rows(self, t):
        days = t * 365
        if days < self.days[0] - 1e-8 or days > self.days[-1] + 1e-8:
            raise ValueError("tenor outside observed grid; no extrapolation")
        hi = min(np.searchsorted(self.days, days), len(self.days) - 1)
        lo = max(hi - 1, 0)
        return lo, hi, days

    def combine(self, a, b, lo, hi, days):
        if lo == hi: return float(a)
        t0, t1 = self.days[lo], self.days[hi]
        variance = a * a * t0 + (b * b * t1 - a * a * t0) * (days - t0) / (t1 - t0)
        if variance <= 0: raise ValueError("nonpositive interpolated variance")
        return math.sqrt(variance / days)

    def at_strike(self, strike, t):
        lo, hi, days = self.tenor_rows(t)
        forward = self.ref_spot * math.exp((self.rate - self.dividend) * t)
        m = strike / (self.ref_spot if self.basis == "spot" else forward)
        if not self.money[0] <= m <= self.money[-1]:
            raise ValueError("moneyness outside observed grid; no extrapolation")
        vs = [np.interp(math.log(m), np.log(self.money), self.grid.iloc[i]) for i in [lo, hi]]
        return self.combine(*vs, lo, hi, days)

    def at_delta(self, delta, t):
        lo, hi, days = self.tenor_rows(t)
        values = []
        for i in [lo, hi]:
            ti = self.days[i] / 365
            anchor = self.ref_spot if self.basis == "spot" else self.ref_spot * math.exp((self.rate - self.dividend) * ti)
            ks, sigmas = self.money * anchor, self.grid.iloc[i].to_numpy()
            deltas = np.array([bs(self.ref_spot, k, ti, self.rate, self.dividend, sigma, "C")["delta"] for k, sigma in zip(ks, sigmas)])
            order = np.argsort(deltas); ds, sigmas = deltas[order], sigmas[order]
            if np.any(np.diff(ds) <= 1e-10): raise ValueError("non-invertible delta grid")
            if not ds[0] <= delta <= ds[-1]: raise ValueError("delta outside observed grid")
            values.append(np.interp(delta, ds, sigmas))
        return self.combine(*values, lo, hi, days)

    def sticky_delta(self, strike, t, spot):
        """Solve sigma = surface(call delta(S,K,T,sigma), T); not K/S relabelling."""
        self.tenor_rows(t)
        def residual(sigma):
            delta = bs(spot, strike, t, self.rate, self.dividend, sigma, "C")["delta"]
            return sigma - self.at_delta(delta, t)
        # A valid fixed point is between the minimum and maximum observed IV.
        sigmas = np.linspace(float(self.grid.min().min()), float(self.grid.max().max()), 81)
        if sigmas[-1] - sigmas[0] < 1e-12:
            residual(sigmas[0]); return float(sigmas[0])
        brackets, last = [], None
        for x in sigmas:
            try: y = residual(x)
            except ValueError: last = None; continue
            if abs(y) < 1e-12: return float(x)
            if last and y * last[1] < 0: brackets.append((last[0], x))
            last = (x, y)
        if len(brackets) != 1: raise ValueError("no unique in-grid sticky-delta solution")
        return float(brentq(residual, *brackets[0], xtol=1e-12))


def prepare_book(review, data, cached, config):
    book = review.book.copy()
    rows, target = [], config["valuation_date"]
    spots, dividends = dict(review.market.spots), dict(review.market.div_yields)
    for x in data["history"]:
        val = x["value"]
        if val is None: continue
        underlying = normalize_underlying(x["ticker"].replace(" Equity", "").replace(" Index", ""))
        if x["field"] == "PX_LAST": spots[underlying] = val
        if x["field"] in {"INDX_EST_DVD_YLD", "EQY_DVD_YLD_IND"}: dividends[underlying] = val / 100
    for row in book.itertuples():
        rec = row._asdict(); rec.pop("_merge", None)
        quote = cached["unique"].get((row.ticker, row.holding_id))
        quote_status = validate_quote(row, quote, target) if quote else "no_returned_response"
        rec.update(latest_quote_status=quote_status, iv_source=None, selected_iv=None,
                   exercise=None, quote_source=None, quote_date=None,
                   latest_bid=None, latest_ask=None, latest_last=None, latest_iv_pct=None)
        if quote:
            rec["latest_iv_pct"] = number(quote.get("ivol(mid)"))
            rec["quote_source"] = quote["sheet"] + "!" + quote["source_cell"]
            rec["quote_date"] = asdate(quote.get("ivol(mid).DATE"))
            rec["exercise"] = quote.get("exer_typ")
            for f in ["bid", "ask", "last"]:
                if asdate(quote.get(f"px_{f}.DATE")) == target:
                    rec[f"latest_{f}"] = number(quote.get(f"px_{f}"))
        valid_terms = (all(np.isfinite(v) for v in [row.position, row.multiplier, row.strike])
                       and row.multiplier > 0 and row.strike > 0 and row.cp in {"C", "P"})
        if not valid_terms: status = "invalid_or_unmatched_input"
        elif row.position == 0: status = "zero_position"
        elif row.holding_asof != review.market.valuation_date: status = "holdings_date_mismatch"
        elif row.dte < 0: status = "expired_snapshot_needs_roll_reconciliation"
        elif row.dte == 0: status = "0DTE_timestamp_settlement_and_roll_required"
        elif row.pricing_status == "analytic_box_net_zero": status = "analytic_box_net_zero"
        elif quote_status.endswith("mismatch") or quote_status in {"conflicting_duplicate_response", "strike_mismatch"}: status = "new_terms_conflict_needs_reconciliation"
        else:
            if quote_status == "same_date_terms_matched_iv":
                rec["selected_iv"], rec["iv_source"] = quote["ivol(mid)"] * config["iv_scale"], "latest_same_date_BQL_ivol_mid"
            elif config["allow_legacy_observed_iv"] and number(row.iv_pct) is not None and row.iv_pct > 0:
                rec["selected_iv"], rec["iv_source"] = row.iv_pct / 100, "legacy_observed_IV_quote_timestamp_unverified"
            elif config["allow_market_value_implied_iv"] and row.pricing_status == "market_value_implied_iv":
                rec["selected_iv"], rec["iv_source"] = row.iv, "market_value_inversion_inferred"
            status = "priced_research_estimate" if rec["selected_iv"] else "missing_usable_iv"
        if row.underlying not in spots and status == "priced_research_estimate": status = "missing_spot"
        rec["latest_pricing_status"] = status
        if row.underlying in spots: rec["spot"] = spots[row.underlying]
        rec["dividend_yield"] = dividends.get(row.underlying, 0.0)
        rec["rate"] = config["rate"]
        if status == "priced_research_estimate":
            g = model_greeks(rec["spot"], row.strike, row.dte / 365, config["rate"], rec["dividend_yield"], rec["selected_iv"], row.cp, rec["exercise"], config["american_tree_steps"])
            size = row.contracts * row.multiplier
            rec.update(g, etf_dollar_delta=size * g["delta"] * rec["spot"],
                       etf_dollar_gamma_1pct=size * g["gamma"] * rec["spot"] ** 2 * 0.01,
                       etf_dollar_vega_1vol=size * g["vega"] * 0.01)
            rec["mm_dollar_gamma_1pct"] = -rec["etf_dollar_gamma_1pct"]
            rec["mm_dollar_vega_1vol"] = -rec["etf_dollar_vega_1vol"]
            bid, ask = rec["latest_bid"], rec["latest_ask"]
            rec["quote_spread_status"] = "missing_bid_or_ask"
            if bid is not None and ask is not None:
                rec["quote_spread_status"] = "crossed_market" if ask < bid else "noncrossed"
                if ask >= bid:
                    rec["quoted_mid"] = (ask + bid) / 2
                    rec["model_minus_mid_per_underlying_unit"] = g["price"] - rec["quoted_mid"]
                    if rec["quoted_mid"] > 0 and number(row.reported_value_usd) is not None:
                        ratio = abs(row.reported_value_usd / (size * rec["quoted_mid"]))
                        rec["holding_value_to_quoted_mid_value_ratio"] = ratio
                        rec["unit_reconciliation_flag"] = "possible_100x_unit_mismatch" if .005 < ratio < .02 or 50 < ratio < 200 else "compare_snapshot_marks_and_units"
        rec["expiry_bucket"] = "0DTE" if row.dte == 0 else "<=1W" if row.dte <= 7 else "1W-1M" if row.dte <= 30 else "1-3M" if row.dte <= 91 else ">3M"
        rows.append(rec)
    return pd.DataFrame(rows), spots, dividends


def surfaces(data, spots, dividends, config, basis):
    result = {}
    for ticker in sorted({x["ticker"] for x in data["vol_nodes"]}):
        u = normalize_underlying(ticker.replace(" Index", "").replace(" Equity", ""))
        points = [{"days": config["surface_tenor_days_assumption"][x["tenor"]],
                   "moneyness": x["moneyness"], "iv": x["value"] * config["iv_scale"]}
                  for x in data["vol_nodes"] if x["ticker"] == ticker and x["value"] is not None and x["value"] > 0]
        if points and u in spots:
            result[u] = StrictSurface(pd.DataFrame(points), spots[u], basis, config["rate"], dividends.get(u, 0))
    return result


def curve_diagnostics(book, data, spots, dividends, config):
    """Use saved BDH nodes, with BOTH coordinate hypotheses clearly labelled.

    These comparisons are not silently substituted into baseline risk.
    """
    out = []
    for basis in ["spot", "forward"]:
        grids = surfaces(data, spots, dividends, config, basis)
        for row in book.loc[book.latest_pricing_status == "priced_research_estimate"].itertuples():
            if row.underlying not in grids: continue
            x = {"ticker": row.ticker, "id": row.holding_id, "basis_assumption": basis,
                 "used_for_baseline": False, "tenor_daycounts_verified": False}
            try:
                x["curve_iv"] = grids[row.underlying].at_strike(row.strike, row.dte / 365)
                x["leg_iv"] = row.selected_iv
                x["curve_minus_leg_vol_points"] = (x["curve_iv"] - row.selected_iv) * 100
                x["status"] = "in_grid_diagnostic_only"
            except ValueError as exc: x["status"] = str(exc)
            out.append(x)
    return out


def ladder(book, config, *, shocks=None, grids=None, vol_mode="sticky_strike", days_forward=0):
    """One fixed, disclosed cohort; no dropping failed legs at individual shocks."""
    shocks = np.arange(-10, 11) / 100 if shocks is None else shocks
    active = book.loc[book.latest_pricing_status == "priced_research_estimate"].copy()
    if active.empty: return {"status": "no_priced_legs", "rows": []}
    if days_forward >= active.dte.min():
        return {"status": "roll_or_settlement_inputs_required", "rows": [], "first_expiry_days": int(active.dte.min())}
    if vol_mode == "sticky_delta":
        if not config["confirmed_surface_basis"] or not config["confirmed_tenor_daycounts"]:
            return {"status": "surface_coordinate_and_tenor_definitions_unconfirmed", "rows": []}
        eligible = []
        for idx, row in active.iterrows():
            try:
                grid = grids[row.underlying]
                for shock in shocks:
                    grid.sticky_delta(row.strike, (row.dte - days_forward) / 365, row.spot * (1 + float(shock)))
                eligible.append(idx)
            except (KeyError, ValueError): pass
        active = active.loc[eligible]
        if active.empty: return {"status": "no_fixed_cohort_covered_at_all_shocks", "rows": []}
    rows = []
    for shock in shocks:
        fund_rows = {}
        for row in active.itertuples():
            spot, t = row.spot * (1 + float(shock)), (row.dte - days_forward) / 365
            sigma = row.selected_iv
            if vol_mode == "sticky_delta":
                grid = grids[row.underlying]
                sigma += grid.sticky_delta(row.strike, t, spot) - grid.sticky_delta(row.strike, row.dte / 365, row.spot)
            g = model_greeks(spot, row.strike, t, row.rate, row.dividend_yield, sigma, row.cp, row.exercise, config["american_tree_steps"])
            size = row.contracts * row.multiplier
            key = (row.ticker, row.underlying)
            x = fund_rows.setdefault(key, {"shock": float(shock), "days_forward": days_forward, "ticker": row.ticker,
                                         "underlying": row.underlying, "vol_mode": vol_mode,
                                         "n_legs": 0, "mm_dollar_gamma_1pct": 0.0, "mm_dollar_vega_1vol": 0.0,
                                         "dealer_hedge_trade_usd": 0.0})
            x["n_legs"] += 1
            x["mm_dollar_gamma_1pct"] -= size * g["gamma"] * spot ** 2 * .01
            x["mm_dollar_vega_1vol"] -= size * g["vega"] * .01
            # Dealer hedge is the negative of dealer option delta: +ETF delta.
            x["dealer_hedge_trade_usd"] += size * (g["delta"] - row.delta) * spot
        rows.extend(fund_rows.values())
    return {"status": "partial_book_research_estimate", "n_legs": len(active),
            "cohort_ids": active.holding_id.tolist(), "rows": rows,
            "shock_definition": "same proportional shock to each native underlying; not SPX-equivalent beta aggregation"}


def clean(x):
    if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [clean(v) for v in x]
    if isinstance(x, (pd.Timestamp,)): return x.isoformat()
    if hasattr(x, "isoformat") and not isinstance(x, str): return x.isoformat()
    if isinstance(x, np.integer): return int(x)
    if isinstance(x, (float, np.floating)): return float(x) if np.isfinite(x) else None
    if x is pd.NA: return None
    return x


def aum_coverage(etfs, direct_tickers, priced_tickers):
    direct, priced = set(direct_tickers), set(priced_tickers)
    if not priced <= direct: raise ValueError("priced funds must have a position book")
    total=float(etfs.aum_usd.sum())
    direct_aum=float(etfs.loc[etfs.ticker.isin(direct),"aum_usd"].sum())
    priced_aum=float(etfs.loc[etfs.ticker.isin(priced),"aum_usd"].sum())
    return {"funds_with_direct_option_records":len(direct),"direct_book_reference_aum_usd":direct_aum,
            "direct_book_reference_aum_share":direct_aum/total,
            "funds_requiring_missing_overlay_or_lookthrough":sorted(set(etfs.ticker)-direct),
            "missing_book_reference_aum_usd":total-direct_aum,
            "missing_book_reference_aum_share":1-direct_aum/total,
            "funds_with_any_priced_position":len(priced),"any_priced_reference_aum_share":priced_aum/total,
            "inferred_books_priced":0,
            "interpretation":"fund-membership AUM categories, NOT the fraction of AUM whose derivative risk is fully observed"}


def run(config_path=CONFIG, output_prefix="shuhao_latest"):
    config = json.loads(Path(config_path).read_text())
    protected = protected_files()
    review = previous.load_review()
    if config["valuation_date"] != str(review.market.valuation_date): raise ValueError("snapshot mismatch")
    data = extractor_data(ROOT / config["extractor"], config["valuation_date"])
    cached = bql_data(ROOT / config["iv_workbook"])
    book, spots, dividends = prepare_book(review, data, cached, config)
    priced = book.loc[book.latest_pricing_status == "priced_research_estimate"].copy()
    coverage = []
    for etf in review.etfs.itertuples():
        subset = book.loc[book.ticker == etf.ticker]
        ready = subset.loc[subset.latest_pricing_status == "priced_research_estimate"]
        states = subset.latest_pricing_status.value_counts().to_dict()
        pending = states.get("0DTE_timestamp_settlement_and_roll_required", 0) + states.get("missing_usable_iv", 0)
        only_box = len(subset) > 0 and set(states) <= {"analytic_box_net_zero", "zero_position"}
        status = "analytic_box_net_zero_units_still_unconfirmed" if only_box else "partial_or_conditional" if len(ready) else "missing_derivative_book" if subset.empty else "no_eligible_risk_yet"
        row = {"ticker": etf.ticker, "rank": int(etf.rank), "reference_aum_usd": etf.aum_usd,
               "status": status, "records": len(subset), "nonzero_records": int(subset.position.ne(0).sum()),
               "priced_legs": len(ready), "latest_iv_legs": int(ready.iv_source.eq("latest_same_date_BQL_ivol_mid").sum()),
               "pending_0DTE_or_IV": pending, "counts": states,
               "fund_total_verified": False}
        for key in RISK: row[key] = float(ready[key].sum()) if len(ready) else 0.0 if only_box else None
        coverage.append(row)
    by_underlying = priced.groupby("underlying")[RISK].sum().reset_index().to_dict("records") if len(priced) else []
    by_bucket = priced.groupby("expiry_bucket")[RISK].sum().reset_index().to_dict("records") if len(priced) else []
    sticky = ladder(book, config)
    grids = surfaces(data, spots, dividends, config, config["confirmed_surface_basis"]) if config["confirmed_surface_basis"] else None
    delta = ladder(book, config, grids=grids, vol_mode="sticky_delta")
    time_ladder = [{"days_forward": day, **ladder(book, config, shocks=[0], days_forward=day)} for day in [0, 1, 3, 5, 7, 14, 21, 28]]
    mismatches = [x for x in cached["unique"].values() if x.get("conflicting_response")]
    report = {
        "valuation_date": config["valuation_date"], "status": "preliminary_partial_book_not_final_top20_total",
        "top20_membership": review.etfs.ticker.tolist(), "universe_reference_aum_usd": float(review.etfs.aum_usd.sum()),
        "aum_coverage": aum_coverage(review.etfs,book.ticker,priced.ticker),
        "inputs": [workbook_info(ROOT / config[k]) for k in ["extractor", "iv_workbook"]],
        "data_audit": {"holdings": data["holdings"], "duplicate_holdings_sheets": data["duplicate_holdings_sheets"],
                       "submitted_contract_ids_in_extractor": data["submitted_contract_ids"],
                       "historical_field_errors": [x for x in data["history"] if x["value"] is None],
                       "bdh_numeric_vol_nodes": sum(x["value"] is not None for x in data["vol_nodes"]),
                       "bdh_quote_dates_independently_saved": 0,
                       "bql_surface_requests": len(cached["surface_requests"]), "bql_surface_successful_iv_nodes": len(cached["surface_nodes"]),
                       "bql_surface_responses": dict(Counter("requesting" if "Requesting" in str(x["response"]) else "field_error" if "Invalid Parameter" in str(x["response"]) else str(x["response"]) for x in cached["surface_requests"])),
                       "per_sheet_returned_option_responses": cached["per_sheet_returned"],
                       "deduplicated_option_responses": len(cached["unique"]), "duplicate_conflicts": cached["conflicts"],
                       "same_date_terms_matched_iv_records": int(book.latest_quote_status.eq("same_date_terms_matched_iv").sum()),
                       "possible_100x_unit_mismatches": int(book.get("unit_reconciliation_flag",pd.Series(dtype=str)).eq("possible_100x_unit_mismatch").sum()),
                       "latest_quote_status_counts": book.latest_quote_status.value_counts().to_dict()},
        "pricing_status_counts": book.latest_pricing_status.value_counts().to_dict(),
        "iv_source_counts": priced.iv_source.value_counts().to_dict(), "model_counts": priced.model.value_counts().to_dict(),
        "coverage": coverage, "by_underlying": by_underlying, "by_expiry_bucket": by_bucket,
        "priced_subset_sum": {key: float(priced[key].sum()) for key in RISK},
        "priced_subset_warning": "sum of available positions under stated unit/rate/dividend assumptions; missing funds/legs are NOT zero",
        "curve_diagnostics": curve_diagnostics(book, data, spots, dividends, config),
        "returned_bql_surface_nodes":cached["surface_nodes"],
        "returned_option_responses":list(cached["unique"].values()),
        "cached_request_issues":[x for x in cached["nonresponses"] if x["response"].startswith("#")],
        "sticky_strike": sticky, "sticky_delta": delta, "time_ladder": time_ladder,
        "assumptions": {"rate": config["rate"], "rate_status": config["rate_status"],
                        "iv_scale": config["iv_scale"], "american_tree_steps": config["american_tree_steps"],
                        "continuous_dividend_yields": "legacy indicative dividend yields; missing values assumed zero for research only",
                        "unit_conventions": "inherited contracts plus two explicitly documented provisional FNOV share overrides; not independently verified",
                        "surface_basis_verified": bool(config["confirmed_surface_basis"]),
                        "surface_tenor_daycounts_verified": config["confirmed_tenor_daycounts"],
                        "source_quote_times": "BQL saves quote DATE; exact close time and pricing-source metadata still absent",
                        "known_counterparty_assumption": "MM=-ETF; conditional opposite side, not observed total dealer inventory",
                        "missing_funds": "JEPI/JEPQ ELN terms/notionals and Vega; ACYN path-dependent terms; BUFR/BUFD same-date child books and ownership fractions",
                        "American_unknown_style": "BSM exercise-unconfirmed where no validated returned style is available"},
        "required_next_data": [
            "Confirm option position units and multipliers, especially FLEX quantities and FNOV mixed scales.",
            "Resolve all nonzero 20-Nov expiry holdings using exact valuation, last-trading/settlement and roll timestamps plus new holdings.",
            "Export dated OVDV/BVOL or LIVE surfaces for actual underlyings, short maturities and real expiries; retain K/moneyness definition, source, quote side and timestamp.",
            "Confirm BDH moneyness basis (spot vs forward) and actual tenor daycount before using nodes for final interpolation or sticky-delta.",
            "Collect same-date SOFR/OIS discount factors and tenor-specific forwards/dividends; a flat 4% and indicative yields are approximations.",
            "JEPI/JEPQ: same-date ELN schedule, principal/option-equivalent exposure and risk-unit definitions, strikes/expiries or calibrated inference, Vega.",
            "ACYN: component notionals/weights, autocall/barrier observation and payment rules, correlations and a path-dependent model.",
            "BUFR/BUFD: same-date child holdings and shares outstanding; reconcile selected direct FNOV holding and parent exposure separately.",
            "Resolve corporate-action raw/adjusted conventions for affected option IDs and underlyings before using those records.",
            "Full SPX/NDX option OI by strike/expiry, ES/NQ ADV, and betas are needed for context ratios and SPX-equivalent reporting, not for the first partial-book Greeks."
        ],
        "original_files_sha256": protected,
        "leg_audit": book.to_dict("records"), "sources": config["sources"],
        "course_policy_note": "The course brief requests no LLM project computations. This AI-assisted implementation and its outputs must not be represented as independently produced course calculations; discuss permitted use with the course team."
    }
    if protected_files() != protected: raise RuntimeError("an original project input or code file changed")
    if not np.isfinite(priced[RISK].to_numpy()).all(): raise RuntimeError("nonfinite computed risk")
    report = clean(report)
    (ROOT / (output_prefix + "_results.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    write_report(report, ROOT / "SHUHAO_LATEST.md")
    print(json.dumps({"status": report["status"], "data_audit": report["data_audit"],
                      "pricing_status_counts": report["pricing_status_counts"], "iv_source_counts": report["iv_source_counts"],
                      "model_counts": report["model_counts"], "priced_subset_sum": report["priced_subset_sum"],
                      "sticky_delta_status": report["sticky_delta"]["status"], "original_files_preserved": True}, ensure_ascii=False))
    return report


def write_report(report, path):
    money = lambda x: "missing" if x is None else f"{x / 1e6:,.3f}"
    lines = ["# Latest saved Bloomberg data: preliminary Top 20 calculation", "",
             "Run `python shuhao_latest.py`; test with `pytest tests test_shuhao_review.py test_shuhao_latest.py`.", "",
             "Original teammate code, notebooks and runtime CSVs remain unchanged. Membership is the existing 20 funds; no re-screening or new ranking was applied.", "",
             "## What the latest files actually contain", "",
             f"Snapshot requested: **{report['valuation_date']}**. The BQL surface sheet has **{report['data_audit']['bql_surface_successful_iv_nodes']} successful numeric IV nodes**. The saved BDH grid has **{report['data_audit']['bdh_numeric_vol_nodes']} numeric nodes** on SPX/NDX/RTY; see the audit for requests still pending or invalid.",
             f"Alternative copied option tabs contain {report['data_audit']['per_sheet_returned_option_responses']}; after merging identical records there are **{report['data_audit']['deduplicated_option_responses']} unique returned option records**. A duplicate tab never creates a second position.", "",
             f"Same-date IV with matched ID, strike, C/P and expiry: **{report['data_audit']['same_date_terms_matched_iv_records']}** original position records. IV source counts: `{report['iv_source_counts']}`. Model counts: `{report['model_counts']}`.", "",
             f"Direct option records exist for **{report['aum_coverage']['funds_with_direct_option_records']} funds**, representing **{report['aum_coverage']['direct_book_reference_aum_share']:.1%}** of the fixed reference-universe AUM. Missing overlay/look-through books: {', '.join(report['aum_coverage']['funds_requiring_missing_overlay_or_lookthrough'])}. This is a fund-membership AUM classification, not fully observed derivative-risk coverage. No inferred book is priced.", "",
             "JEPI/JEPQ/ACYN holdings exports still contain equity, treasury and cash rather than complete ELN/OTC option exposure. The extraction workbook's actual contract-ID query column remains empty. Issuer ELN tables dated 23 Nov are kept separate from this 20 Nov calculation.", "",
             "## Calculation and conventions", "",
             "1. Join the fixed Top 20 book to returned responses by fund and actual security ID; validate returned terms and IV DATE. Reject text errors, incomplete requests and contradictory duplicate values.",
             "2. Prefer the latest matched IV; otherwise retain a separately labelled original observed IV when available. Market-value IV inversion is disabled by default. Percent IV is multiplied by 0.01 exactly once.",
             "3. Use European BSM for confirmed European options, a 400-step American CRR tree for confirmed American options, and explicitly label unconfirmed exercise style. Interest/dividend inputs remain provisional.",
             "4. ETF $Gamma per 1% = signed contracts × multiplier × Gamma × S² × 0.01. ETF $Vega per vol point = signed contracts × multiplier × Vega × 0.01. Vega here is the derivative per 1.00 volatility before scaling. MM = −ETF.",
             "5. Recognise a complete same-expiry European box analytically at net zero delta/gamma/vega. This is not a claim of zero risk on each box leg or verified position sizes.", "",
             "## Fund coverage and available-position risk", "",
             "All risk columns below are USD millions. They are available-position subtotals, not verified full-fund totals. Missing derivative books remain missing, not zero.", "",
             "| Fund | Priced legs | Latest IV legs | MM gamma / 1% ($mn) | MM vega / vol point ($mn) | Coverage |",
             "|---|---:|---:|---:|---:|---|"]
    for x in report["coverage"]:
        lines.append(f"| {x['ticker']} | {x['priced_legs']} | {x['latest_iv_legs']} | {money(x['mm_dollar_gamma_1pct'])} | {money(x['mm_dollar_vega_1vol'])} | {x['status']} |")
    lines += ["", "## Scenarios and what is not finished", "",
              "Sticky-strike: −10% through +10% in 1% steps, same fixed eligible cohort. Each native underlying receives the same percentage move. Hedge trade is computed from the change in underlying units at the new spot; it is not a change in dollar delta mistaken for a trade.",
              f"Sticky-delta status: **{report['sticky_delta']['status']}**. The implementation solves IV against a call-delta-indexed surface; it does not rename K/S smile-following as sticky-delta. Real-data output is withheld until coordinate/tenor definitions are confirmed, then uses only one cohort covered at every shock.",
              "BDH curves are also evaluated under separate spot- and forward-moneyness hypotheses in `curve_diagnostics`. Those values are diagnostic only and never replace baseline IV. Interpolation is linear in log-moneyness and total variance; outside-grid queries raise an explicit missing-data error.",
              "Time ladder: same held contracts, only before the earliest unresolved expiry. Later requested horizons return `roll_or_settlement_inputs_required`. No new roll holdings are invented. All date-only 0DTE records remain pending, not zero-gamma positions.",
              "No SPX-equivalent total, OI-normalised result or observed street inventory is claimed. They need betas, market-wide OI/ADV and counterparty evidence.", "",
              "## Remaining data, in order", ""]
    lines.extend(f"{i}. {x}" for i, x in enumerate(report["required_next_data"], 1))
    lines += ["", "## Specific positive-tenor positions still missing usable IV", "",
              "These remain missing in the risk subtotal. The saved request issues and full returned option records are retained in the JSON for diagnosis.", "",
              "| Fund | Security ID | Latest response status |", "|---|---|---|"]
    for x in report["leg_audit"]:
        if x["latest_pricing_status"] == "missing_usable_iv":
            lines.append(f"| {x['ticker']} | {x['holding_id']} | {x['latest_quote_status']} |")
    lines += ["", "## Reproducibility and review", "",
              "Raw workbooks are byte-preserved under `data/sheets/Shuhao_Latest_*.xlsx`. The JSON output records their SHA256, per-leg provenance, failures, model, unit status, all scenario rows and protected original-file hashes. The calculation asserts that protected original files are unchanged.",
              "The reference Top 20 AUM is retained for membership/context. The three new scalar AUM requests lack independent quote timestamps and are not used to silently re-rank funds. A fund's AUM associated with some priced positions is not the fraction of fund AUM whose derivative risk is fully observed.",
              "Rates are illustrative flat 4%; missing indicative dividends use zero explicitly. Option quantity units and exact valuation/roll timestamps remain unconfirmed. Thus none of these estimates is marked final or fully observed.", "",
              f"Saved position values versus current same-date bid/ask mid identify **{report['data_audit']['possible_100x_unit_mismatches']} possible 100x unit/mark mismatches**. They are flagged per leg; the code does not silently rewrite quantities from a ratio that may also reflect mark-time differences.", "",
              "## AI assistance and course constraint", "", report["course_policy_note"], "", "## Sources", ""]
    lines.extend("- " + x for x in report["sources"])
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    run(args.config)
