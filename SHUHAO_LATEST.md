# Latest saved Bloomberg data: preliminary Top 20 calculation

Run `python shuhao_latest.py`; test with `pytest tests test_shuhao_review.py test_shuhao_latest.py`.

Original teammate code, notebooks and runtime CSVs remain unchanged. Membership is the existing 20 funds; no re-screening or new ranking was applied.

## What the latest files actually contain

Snapshot requested: **2026-11-20**. The BQL surface sheet has **0 successful numeric IV nodes**. The saved BDH grid has **135 numeric nodes** on SPX/NDX/RTY; see the audit for requests still pending or invalid.
Alternative copied option tabs contain {'BQL逐腿 (2)': 135, 'BQL逐腿': 68}; after merging identical records there are **140 unique returned option records**. A duplicate tab never creates a second position.

Same-date IV with matched ID, strike, C/P and expiry: **139** original position records. IV source counts: `{'latest_same_date_BQL_ivol_mid': 139, 'legacy_observed_IV_quote_timestamp_unverified': 6}`. Model counts: `{'American_CRR': 116, 'European_BSM': 23, 'BSM_exercise_unconfirmed': 6}`.

Direct option records exist for **15 funds**, representing **47.8%** of the fixed reference-universe AUM. Missing overlay/look-through books: ACYN, BUFD, BUFR, JEPI, JEPQ. This is a fund-membership AUM classification, not fully observed derivative-risk coverage. No inferred book is priced.

JEPI/JEPQ/ACYN holdings exports still contain equity, treasury and cash rather than complete ELN/OTC option exposure. The extraction workbook's actual contract-ID query column remains empty. Issuer ELN tables dated 23 Nov are kept separate from this 20 Nov calculation.

## Calculation and conventions

1. Join the fixed Top 20 book to returned responses by fund and actual security ID; validate returned terms and IV DATE. Reject text errors, incomplete requests and contradictory duplicate values.
2. Prefer the latest matched IV; otherwise retain a separately labelled original observed IV when available. Market-value IV inversion is disabled by default. Percent IV is multiplied by 0.01 exactly once.
3. Use European BSM for confirmed European options, a 400-step American CRR tree for confirmed American options, and explicitly label unconfirmed exercise style. Interest/dividend inputs remain provisional.
4. ETF $Gamma per 1% = signed contracts × multiplier × Gamma × S² × 0.01. ETF $Vega per vol point = signed contracts × multiplier × Vega × 0.01. Vega here is the derivative per 1.00 volatility before scaling. MM = −ETF.
5. Recognise a complete same-expiry European box analytically at net zero delta/gamma/vega. This is not a claim of zero risk on each box leg or verified position sizes.

## Fund coverage and available-position risk

All risk columns below are USD millions. They are available-position subtotals, not verified full-fund totals. Missing derivative books remain missing, not zero.

| Fund | Priced legs | Latest IV legs | MM gamma / 1% ($mn) | MM vega / vol point ($mn) | Coverage |
|---|---:|---:|---:|---:|---|
| JEPI | 0 | 0 | missing | missing | missing_derivative_book |
| JEPQ | 0 | 0 | missing | missing | missing_derivative_book |
| BOXX | 0 | 0 | 0.000 | 0.000 | analytic_box_net_zero_units_still_unconfirmed |
| QQQI | 2 | 1 | 67.275 | 1.602 | partial_or_conditional |
| SPYI | 2 | 2 | 96.729 | 1.616 | partial_or_conditional |
| BUFR | 0 | 0 | missing | missing | missing_derivative_book |
| QYLD | 1 | 1 | 249.677 | 8.072 | partial_or_conditional |
| DIVO | 1 | 1 | 5.509 | 0.072 | partial_or_conditional |
| GPIX | 0 | 0 | missing | missing | no_eligible_risk_yet |
| GPIQ | 0 | 0 | missing | missing | no_eligible_risk_yet |
| HELO | 8 | 8 | -7.221 | 0.129 | partial_or_conditional |
| KNG | 0 | 0 | missing | missing | no_eligible_risk_yet |
| BALT | 0 | 0 | missing | missing | no_eligible_risk_yet |
| XYLD | 1 | 1 | 149.305 | 3.367 | partial_or_conditional |
| ACYN | 0 | 0 | missing | missing | missing_derivative_book |
| FTHI | 5 | 5 | 26.127 | 0.579 | partial_or_conditional |
| ACIO | 118 | 113 | -56.406 | -0.584 | partial_or_conditional |
| FNOV | 0 | 0 | missing | missing | no_eligible_risk_yet |
| BUFD | 0 | 0 | missing | missing | missing_derivative_book |
| SFLR | 7 | 7 | -17.293 | -4.215 | partial_or_conditional |

## Scenarios and what is not finished

Sticky-strike: −10% through +10% in 1% steps, same fixed eligible cohort. Each native underlying receives the same percentage move. Hedge trade is computed from the change in underlying units at the new spot; it is not a change in dollar delta mistaken for a trade.
Sticky-delta status: **surface_coordinate_and_tenor_definitions_unconfirmed**. The implementation solves IV against a call-delta-indexed surface; it does not rename K/S smile-following as sticky-delta. Real-data output is withheld until coordinate/tenor definitions are confirmed, then uses only one cohort covered at every shock.
BDH curves are also evaluated under separate spot- and forward-moneyness hypotheses in `curve_diagnostics`. Those values are diagnostic only and never replace baseline IV. Interpolation is linear in log-moneyness and total variance; outside-grid queries raise an explicit missing-data error.
Time ladder: same held contracts, only before the earliest unresolved expiry. Later requested horizons return `roll_or_settlement_inputs_required`. No new roll holdings are invented. All date-only 0DTE records remain pending, not zero-gamma positions.
No SPX-equivalent total, OI-normalised result or observed street inventory is claimed. They need betas, market-wide OI/ADV and counterparty evidence.

## Remaining data, in order

1. Confirm option position units and multipliers, especially FLEX quantities and FNOV mixed scales.
2. Resolve all nonzero 20-Nov expiry holdings using exact valuation, last-trading/settlement and roll timestamps plus new holdings.
3. Export dated OVDV/BVOL or LIVE surfaces for actual underlyings, short maturities and real expiries; retain K/moneyness definition, source, quote side and timestamp.
4. Confirm BDH moneyness basis (spot vs forward) and actual tenor daycount before using nodes for final interpolation or sticky-delta.
5. Collect same-date SOFR/OIS discount factors and tenor-specific forwards/dividends; a flat 4% and indicative yields are approximations.
6. JEPI/JEPQ: same-date ELN schedule, principal/option-equivalent exposure and risk-unit definitions, strikes/expiries or calibrated inference, Vega.
7. ACYN: component notionals/weights, autocall/barrier observation and payment rules, correlations and a path-dependent model.
8. BUFR/BUFD: same-date child holdings and shares outstanding; reconcile selected direct FNOV holding and parent exposure separately.
9. Resolve corporate-action raw/adjusted conventions for affected option IDs and underlyings before using those records.
10. Full SPX/NDX option OI by strike/expiry, ES/NQ ADV, and betas are needed for context ratios and SPX-equivalent reporting, not for the first partial-book Greeks.

## Specific positive-tenor positions still missing usable IV

These remain missing in the risk subtotal. The saved request issues and full returned option records are retained in the JSON for diagnosis.

| Fund | Security ID | Latest response status |
|---|---|---|
| HELO | SPY US W3026C727000 Equity | iv_not_numeric_positive |
| GPIX | BBG026QTQBX3 Equity | no_returned_response |
| GPIX | BBG026LL6RS8 Equity | no_returned_response |
| GPIX | BBG026F9PYX0 Equity | no_returned_response |
| GPIQ | BBG026F9PYQ8 Equity | no_returned_response |
| GPIQ | BBG026QTQBC6 Equity | no_returned_response |
| GPIQ | BBG026LL6SD2 Equity | no_returned_response |
| BALT | BBG025KVD0R1 Equity | no_returned_response |
| BALT | BBG025KVD0Y3 Equity | no_returned_response |
| BALT | BBG025KVCTY1 Equity | no_returned_response |
| BALT | BBG025KVCV30 Equity | no_returned_response |

## Reproducibility and review

Raw workbooks are byte-preserved under `data/sheets/Shuhao_Latest_*.xlsx`. The JSON output records their SHA256, per-leg provenance, failures, model, unit status, all scenario rows and protected original-file hashes. The calculation asserts that protected original files are unchanged.
The reference Top 20 AUM is retained for membership/context. The three new scalar AUM requests lack independent quote timestamps and are not used to silently re-rank funds. A fund's AUM associated with some priced positions is not the fraction of fund AUM whose derivative risk is fully observed.
Rates are illustrative flat 4%; missing indicative dividends use zero explicitly. Option quantity units and exact valuation/roll timestamps remain unconfirmed. Thus none of these estimates is marked final or fully observed.

Saved position values versus current same-date bid/ask mid identify **0 possible 100x unit/mark mismatches**. They are flagged per leg; the code does not silently rewrite quantities from a ratio that may also reflect mark-time differences.

## AI assistance and course constraint

The course brief requests no LLM project computations. This AI-assisted implementation and its outputs must not be represented as independently produced course calculations; discuss permitted use with the course team.

## Sources

- Derivatives - Project 10.docx: position-level IV, BSM/Black, dollar gamma per 1%, dollar vega per vol point, MM=-ETF, spot/time ladders
- https://strata.opengamma.io/apidocs/com/opengamma/strata/pricer/impl/option/BlackScholesFormulaRepository.html
- https://github.com/lballabio/QuantLib/blob/master/ql/pricingengines/vanilla/binomialengine.hpp
- https://data.bloomberglp.com/professional/sites/10/750114_Real-Time-Volatilities.pdf
