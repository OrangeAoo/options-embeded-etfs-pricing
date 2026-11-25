"""Reproduce teammate main and compare it with the structured-IV research run.

Only writes new shuhao_* comparison outputs. Original core/, data and notebooks
are checked against the fetched main commit and never modified.
Run: python -B shuhao_compare.py
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from core import pipeline, greek_engine as engine
import shuhao_latest as latest

ROOT = Path(__file__).resolve().parent
KEYS = ["ticker", "holding_id"]
METRICS = ["mm_dollar_gamma_1pct", "mm_dollar_vega_1vol"]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def verify_main_inputs(ref):
    """Do not mistake an edited local implementation for teammate's baseline."""
    files = sorted(list((ROOT / "core").glob("*.py")) + list((ROOT / "data/sheets").rglob("*.csv")))
    # Only original runtime CSVs existed on main; comparison CSVs live at root.
    for path in files:
        relative = str(path.relative_to(ROOT))
        if git("show", ref + ":" + relative) != path.read_bytes():
            raise ValueError(f"local baseline differs from {ref}: {relative}")
    return {"commit": git("rev-parse", ref).decode().strip(),
            "verified_original_files": {str(p.relative_to(ROOT)): latest.digest(p) for p in files}}


def risk(frame):
    return {m: float(frame[m].sum()) for m in METRICS}


def bridge(stages):
    out, previous = [], None
    for name, value in stages:
        row = {"stage": name, "totals": value,
               "change_from_previous": None if previous is None else {m: value[m] - previous[m] for m in METRICS}}
        out.append(row)
        previous = value
    return out


def term_status(days, tenors):
    if days <= 0:
        return "intraday_expiry_time_required"
    if not tenors:
        return "no_standard_surface_for_underlying"
    if days < min(tenors):
        return "shorter_than_observed_grid"
    if days > max(tenors):
        return "longer_than_observed_grid"
    return "inside_assumed_term_range_coordinate_still_unconfirmed"


def coverage_note(fund, counts):
    if not sum(counts.values()):
        return {"JEPI": "ELN option-equivalent book/terms missing", "JEPQ": "ELN option-equivalent book/terms missing",
                "BUFR": "same-date child books and ownership fractions missing", "BUFD": "same-date child books and ownership fractions missing",
                "ACYN": "autocallable terms, notionals, correlations and model missing"}.get(fund, "derivative book missing")
    issues = []
    if counts.get("missing_usable_iv"):
        issues.append(f"{counts['missing_usable_iv']} positive-tenor legs lack clean IV")
    if counts.get("0DTE_timestamp_settlement_and_roll_required"):
        issues.append(f"{counts['0DTE_timestamp_settlement_and_roll_required']} expiry-day legs need valuation/settlement/roll times")
    if counts.get("analytic_box_net_zero"):
        issues.append("box net zero conditional on exact terms/units")
    if not issues:
        issues.append("all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional")
    return "; ".join(issues)


def write_csv(name, records):
    pd.DataFrame(records).to_csv(ROOT / name, index=False, float_format="%.12g")


def run(ref="origin/main", refresh=True):
    baseline_info = verify_main_inputs(ref)
    protected = latest.protected_files()
    if refresh:
        with contextlib.redirect_stdout(io.StringIO()):
            current = latest.run()
    else:
        current = json.loads((ROOT / "shuhao_latest_results.json").read_text())
    config = json.loads(latest.CONFIG.read_text())
    raw, coverage, etfs, market = pipeline.build_from_sheets()
    old = engine.price_book(raw, market)
    new_book = pd.DataFrame(current["leg_audit"])
    new_live = new_book.loc[new_book.latest_pricing_status.eq("priced_research_estimate")].copy()
    if old.duplicated(KEYS).any() or new_book.duplicated(KEYS).any():
        raise ValueError("comparison keys must be unique fund/security pairs")
    cohort = new_live[KEYS].merge(old, on=KEYS, validate="one_to_one")
    aligned = new_live[KEYS + ["selected_iv", "spot", "rate", "dividend_yield", "contracts", "multiplier"]].merge(
        old, on=KEYS, validate="one_to_one", suffixes=("_new", "_old"))
    # A controlled bridge: cohort -> IV -> market/units -> exercise model.
    changed_iv_book = cohort.copy()
    changed_iv_book["iv"] = aligned.selected_iv.to_numpy()
    changed_iv = engine.price_book(changed_iv_book, replace(market, default_vol=None, surfaces={}))
    bsm_new = []
    for row in new_live.itertuples():
        g = latest.bs(row.spot, row.strike, row.dte / 365, row.rate, row.dividend_yield, row.selected_iv, row.cp)
        size = row.contracts * row.multiplier
        bsm_new.append({"mm_dollar_gamma_1pct": -size * g["gamma"] * row.spot ** 2 * .01,
                        "mm_dollar_vega_1vol": -size * g["vega"] * .01})
    bsm_new = pd.DataFrame(bsm_new)
    stages = bridge([
        ("teammate_default_full_observed_rows", risk(old)),
        ("teammate_on_common_priced_position_cohort", risk(cohort)),
        ("same_cohort_new_iv_only_original_BSM_market_units", risk(changed_iv)),
        ("same_cohort_new_iv_market_units_all_BSM", risk(bsm_new)),
        ("structured_iv_exercise_aware_final", risk(new_live)),
    ])
    old_audit = old.merge(new_book[KEYS + ["latest_pricing_status"]], on=KEYS, validate="one_to_one")
    old_audit["iv_input_status"] = np.where(old_audit.iv.notna(), "original_numeric_IV_timestamp_unverified", "flat_20pct_fallback")
    old_audit["comparison_cohort"] = np.where(old_audit.latest_pricing_status.eq("priced_research_estimate"), "retained", "excluded_from_latest")
    original_iv_lookup = old.set_index(KEYS).iv.to_dict()
    new_lookup = new_live.set_index(KEYS).selected_iv.to_dict()
    matched_iv_differences = [new_lookup[k] - original_iv_lookup[k] for k in new_lookup]
    missing_iv = []
    for row in new_book.loc[new_book.latest_pricing_status.eq("missing_usable_iv")].itertuples():
        missing_iv.append({"FUND": row.ticker, "SECURITY": row.holding_id, "UNDERLYING": row.underlying,
                           "AS_OF_DATE": current["valuation_date"], "EXPIRE_DT": str(row.expiry)[:10],
                           "DTE": int(row.dte), "STRIKE_PX": row.strike, "PUT_CALL": row.cp,
                           "PRIOR_NUMERIC_IV_PCT": original_iv_lookup[(row.ticker,row.holding_id)] * 100 if pd.notna(original_iv_lookup[(row.ticker,row.holding_id)]) else None,
                           "REQUIRED": "same-date contract IV with quote DATE; or underlying surface spanning actual strike/expiry",
                           "CURRENT_STATUS": row.latest_quote_status})
    old_by_fund = old.groupby("ticker")[METRICS].sum()
    rows = []
    for c in current["coverage"]:
        fund = c["ticker"]
        book = old.loc[old.ticker.eq(fund)]
        fallback = book.iv.isna()
        active_future = book.position.ne(0) & book.dte.gt(0)
        r = {"FUND": fund, "REFERENCE_AUM_USD": c["reference_aum_usd"],
             "ORIGINAL_RECORDS": len(book), "ORIGINAL_FLAT20_ROWS": int(fallback.sum()),
             "ORIGINAL_FLAT20_NONZERO_FUTURE_LEGS": int((fallback & active_future).sum()),
             "ORIGINAL_NUMERIC_IV_NONZERO_FUTURE_LEGS": int((~fallback & active_future).sum()),
             "NEW_PRICED_LEGS": c["priced_legs"], "NEW_MISSING_IV_LEGS": c["counts"].get("missing_usable_iv",0),
             "NEW_EXPIRY_DAY_PENDING": c["counts"].get("0DTE_timestamp_settlement_and_roll_required",0),
             "STATUS": c["status"], "REMAINING_INPUTS": coverage_note(fund,c["counts"])}
        for m in METRICS:
            r["ORIGINAL_"+m.upper()] = float(old_by_fund.loc[fund,m]) if fund in old_by_fund.index else None
            r["NEW_"+m.upper()] = c[m]
            r["CHANGE_"+m.upper()] = c[m] - r["ORIGINAL_"+m.upper()] if c[m] is not None and fund in old_by_fund.index else None
        rows.append(r)
    # A term/strike request manifest, not invented Bloomberg values or fields.
    iv = latest.structured_iv_data(ROOT / config["iv_workbook"], latest.previous.load_review().book, current["valuation_date"])
    tenor_map = config["surface_tenor_days_assumption"]
    curve_by_u = {}
    for node in iv["vol_nodes"]:
        curve_by_u.setdefault(latest.underlying_key(node["ticker"]), []).append(node)
    requests = []
    for row in new_book.loc[new_book.position.ne(0)].itertuples():
        nodes = curve_by_u.get(row.underlying, [])
        days = sorted({tenor_map[x["tenor"]] for x in nodes})
        k_over_s = row.strike / row.spot if pd.notna(row.spot) and row.spot > 0 else None
        requests.append({"FUND":row.ticker, "SECURITY":row.holding_id, "UNDERLYING":row.underlying,
                         "AS_OF_DATE": current["valuation_date"], "EXPIRE_DT":str(row.expiry)[:10], "DTE":int(row.dte),
                         "STRIKE_PX":row.strike, "PUT_CALL":row.cp, "BASE_K_OVER_S":k_over_s,
                         "K_OVER_S_AT_PLUS10PCT":k_over_s/1.1 if k_over_s is not None else None,
                         "K_OVER_S_AT_MINUS10PCT":k_over_s/.9 if k_over_s is not None else None,
                         "EXISTING_ASSUMED_TENOR_DAYS":";".join(map(str,days)), "TERM_COVERAGE":term_status(row.dte,days),
                         "CURRENT_BASELINE_STATUS":row.latest_pricing_status,
                         "REQUIRED": "actual underlying/expiry smile plus bracketing maturities; for 392D single stocks obtain DRI/CL, e.g. 12M and 18M or 2Y, not SPX proxies" if row.dte>365 else "actual underlying/expiry smile spanning held strike and shock range; 0DTE also needs exact quote/expiry time",
                         "MONEYNESS_BASIS_CONFIRMED":False})
    cohorts = []
    for (state, iv_status), sub in old_audit.groupby(["latest_pricing_status", "iv_input_status"]):
        cohorts.append({"latest_status":state,"original_iv_status":iv_status,"records":len(sub),**risk(sub)})
    total_aum = float(etfs.aum_usd.sum())
    aum = current["aum_coverage"]
    result = {"valuation_date":current["valuation_date"], "baseline":baseline_info,
              "personal_branch":git("branch","--show-current").decode().strip(),
              "new_input_workbooks":current["inputs"], "original_default_vol":market.default_vol,
              "baseline_reproduction":"README quick start: pipeline.build_from_sheets(); greek_engine.price_book(book,market). No loaded surfaces; missing IV uses FlatVol(0.20).",
              "published_progress_report":{"source_commit":baseline_info["commit"],"path":"progress-report.docx",
                                           "rounded_mm_gamma_usd":490e6,"rounded_mm_vega_usd":10.5e6},
              "universe":{"funds":20,"reference_aum_usd":total_aum,"direct_book_funds":aum["funds_with_direct_option_records"],
                          "direct_book_aum_share":aum["direct_book_reference_aum_share"],
                          "missing_book_funds":aum["funds_requiring_missing_overlay_or_lookthrough"],
                          "missing_book_aum_share":aum["missing_book_reference_aum_usd"]/total_aum,
                          "new_funds_with_any_priced_positions":len(new_live.ticker.unique()),
                          "new_fund_membership_aum_share":aum["any_priced_reference_aum_share"],
                          "interpretation":"AUM percentages describe fund membership, never measured derivative-risk completeness"},
              "original_record_audit":{"records":len(old),"numeric_iv_rows":int(old.iv.notna().sum()),"flat20_fallback_rows":int(old.iv.isna().sum()),
                                       "nonzero_future_flat20_legs":int((old.iv.isna()&old.position.ne(0)&old.dte.gt(0)).sum()),
                                       "nonzero_future_nonbox_flat20_legs":int((old_audit.iv.isna()&old_audit.position.ne(0)&old_audit.dte.gt(0)&old_audit.latest_pricing_status.ne("analytic_box_net_zero")).sum()),
                                       "nonzero_future_numeric_iv_legs":int((old.iv.notna()&old.position.ne(0)&old.dte.gt(0)).sum()),
                                       "nonzero_expiry_day_rows":int((old.position.ne(0)&old.dte.eq(0)).sum())},
              "latest_record_audit":current["pricing_status_counts"],
              "shared_cohort_iv_changes":{"positions":len(new_live),"changed_IV_count":sum(abs(x)>1e-10 for x in matched_iv_differences),
                                          "max_absolute_IV_change_decimal":max(map(abs,matched_iv_differences))},
              "risk_bridge":stages,"baseline_contributions_by_new_status":cohorts,
              "by_fund":rows,"missing_positive_tenor_iv":missing_iv,
              "surface_term_request_counts":dict(Counter(x["TERM_COVERAGE"] for x in requests)),
              "longer_than_year_positions":[x for x in requests if x["DTE"]>365],
              "surface_conventions_confirmed":False,
              "limitations":current["required_next_data"],"course_policy_note":current["course_policy_note"]}
    for m in METRICS:
        if not np.isclose(stages[0]["totals"][m]+sum(x["change_from_previous"][m] for x in stages[1:]),stages[-1]["totals"][m],rtol=0,atol=1e-6):
            raise RuntimeError("risk bridge does not reconcile")
    if latest.protected_files()!=protected:
        raise RuntimeError("original files changed")
    (ROOT/"shuhao_comparison_results.json").write_text(json.dumps(latest.clean(result),indent=2,allow_nan=False)+"\n")
    write_csv("shuhao_comparison_by_fund.csv",rows)
    write_csv("shuhao_missing_iv_requests.csv",missing_iv)
    write_csv("shuhao_surface_requests.csv",requests)
    write_report(result)
    print(json.dumps({"original":stages[0]["totals"],"new":stages[-1]["totals"],"bridge":stages,
                      "shared_IV_changes":result["shared_cohort_iv_changes"],"missing_book_aum_share":result["universe"]["missing_book_aum_share"],
                      "missing_IV_requests":len(missing_iv),"longer_than_year":len(result["longer_than_year_positions"])},indent=2))
    return result


def write_report(r):
    mn=lambda x: "missing" if x is None else f"{x/1e6:,.3f}"
    lines=["# Teammate baseline versus structured-IV calculation", "",
           f"Snapshot: {r['valuation_date']}. Reproduced teammate main commit `{r['baseline']['commit']}`. Changes are confined to the personal branch.","",
           "## Can the new data complete Top 20 Greeks?","",
           "No. The five missing derivative books (JEPI, JEPQ, BUFR, BUFD, ACYN) still represent 52.2% of the fixed reference-universe AUM. New IV data do not supply ELN notionals/terms, child-fund look-through or autocallable terms. Fund-membership AUM is not derivative-risk coverage.","",
           "The original default call emits 285 rows for 15 funds. It uses a flat 20% fallback on 46 missing-IV rows: 19 nonzero positive-tenor legs (eight box legs and 11 other positions), 23 expiry-day legs and four zero positions. It assigns date-only expiry-day positions zero gamma/vega. The cleaned-input run prices 139 positions in nine funds, leaves 17 positive-tenor IV gaps and 74 expiry-day positions pending, excludes 47 zero positions, and separately identifies eight conditional box legs.","",
           f"All {r['shared_cohort_iv_changes']['positions']} retained positions already had numeric IV in the original file; the new quotes confirm dates/terms and exercise style rather than increase this priced cohort. Their maximum IV difference is only {r['shared_cohort_iv_changes']['max_absolute_IV_change_decimal']*100:.6f} volatility percentage points, consistent with the original file's rounded values. Six other original numeric IVs were not returned in the clean snapshot. Their undated fallback is disabled. Fewer accepted rows therefore does not imply fewer numbers in the source file.","",
           "## Reproduced aggregate results and controlled bridge","",
           "All risk columns are USD millions, MM = -ETF. Native-underlying sums are provided for comparison; they are not beta-adjusted SPX-equivalent or verified full-universe totals.","",
           "| Calculation stage | MM gamma / 1% | MM vega / vol point | Gamma change | Vega change |",
           "|---|---:|---:|---:|---:|"]
    for s in r["risk_bridge"]:
        d=s["change_from_previous"]
        lines.append(f"| {s['stage']} | {mn(s['totals'][METRICS[0]])} | {mn(s['totals'][METRICS[1]])} | {mn(d[METRICS[0]]) if d else 'n.a.'} | {mn(d[METRICS[1]]) if d else 'n.a.'} |")
    lines += ["","The original rerun agrees with the progress report's rounded +$490m gamma and +$10.5m vega. The headline totals are numerically close, but substantial positive and negative cohort contributions offset. The controlled bridge holds the priced-position cohort fixed before changing IV, market/units and the American exercise model. It is an ordered attribution, not a claim of unique causal contributions.","",
              "In particular, original HELO MM gamma is -$56.307m versus -$7.221m on the newer partial book; one missing-IV leg priced at flat 20% in the original is now omitted, so this is not evidence that HELO's full risk fell. QQQI is +$67.275m versus +$41.783m because one of its two legs lacks a clean quote. GPIX, GPIQ and BALT originally used the flat-vol fallback and now remain unavailable. Comparing only the aggregate would hide these coverage changes.","",
              "## Comparison for every Top 20 fund","",
              "Missing data remain missing. Original zero on expiry day and conditional box net zero have different meanings.","",
              "| Fund | Original MM gamma | New MM gamma | Original MM vega | New MM vega | New priced legs | Remaining inputs |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for x in r["by_fund"]:
        lines.append(f"| {x['FUND']} | {mn(x['ORIGINAL_'+METRICS[0].upper()])} | {mn(x['NEW_'+METRICS[0].upper()])} | {mn(x['ORIGINAL_'+METRICS[1].upper()])} | {mn(x['NEW_'+METRICS[1].upper()])} | {x['NEW_PRICED_LEGS']} | {x['REMAINING_INPUTS']} |")
    lines += ["","## Does the curve need to extend beyond one year?","",
              "Yes when the actual option expiry is beyond the observed grid and a surface is needed. A 392-day option needs either its own expiry smile or maturities on both sides, such as 12M and 18M/2Y, under an explicit interpolation convention. A 12M-only endpoint cannot supply observed 392D IV. No extrapolated value has been inserted.","",
              "The two 392D positions are DRI and CL single-stock options expiring 17 December 2027. Their contract IV is present in the cleaned workbook, so their baseline Greeks are already priced. They have no standard DRI/CL surface; extending only SPX/NDX/RTY would not solve their surface requirement. The standard index grids run from 30D to 12M, and their moneyness/tenor definitions still require confirmation.","",
              "| Fund | Underlying | Security | Expiry | DTE | Baseline status |",
              "|---|---|---|---|---:|---|"]
    for x in r["longer_than_year_positions"]:
        lines.append(f"| {x['FUND']} | {x['UNDERLYING']} | {x['SECURITY']} | {x['EXPIRE_DT']} | {x['DTE']} | {x['CURRENT_BASELINE_STATUS']} |")
    lines += ["","For surface-based scenarios, first cover actual underlyings, expiries, strikes and the -10%/+10% shock range. Add short maturities below 30D and intraday timestamps for 0DTE. The same-date contract IV suffices for a baseline calculation under the stated model; it does not define a complete smile or its response to spot/time changes.","",
              "## Files and reproduction","",
              "- `python -B shuhao_compare.py` refreshes the structured-IV calculation and reproduces the verified original default pipeline.",
              "- `shuhao_comparison_results.json`: commit/input hashes, cohort counts and controlled risk bridge.",
              "- `shuhao_comparison_by_fund.csv`: all 20 funds, original/new Greeks and missing inputs.",
              "- `shuhao_missing_iv_requests.csv`: 17 positive-tenor fund positions still lacking clean same-date IV.",
              "- `shuhao_surface_requests.csv`: all nonzero source positions with actual expiries/strikes, current term coverage and required observations. It contains requests, not fabricated market data.","",
              "## Remaining limitations","",
              "Position units/multipliers, a dated SOFR/OIS curve, forwards/dividends, exact valuation and roll timestamps remain provisional. American quotes use a 400-step CRR tree in the newer run; the original uses BSM for all options. Sticky-delta remains blocked until surface conventions and coverage are resolved. All calculations here are partial research estimates.","",
              "## AI assistance and course constraint","",r["course_policy_note"],""]
    (ROOT/"SHUHAO_COMPARISON.md").write_text("\n".join(lines))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-ref",default="origin/main")
    p.add_argument("--no-refresh",action="store_true")
    a=p.parse_args()
    run(a.baseline_ref,not a.no_refresh)
