# Teammate baseline versus structured-IV calculation

Snapshot: 2026-11-20. Reproduced teammate main commit `442fef605159b7effe9e5e981caaa3c04251e048`. Changes are confined to the personal branch.

## Can the new data complete Top 20 Greeks?

No. The five missing derivative books (JEPI, JEPQ, BUFR, BUFD, ACYN) still represent 52.2% of the fixed reference-universe AUM. New IV data do not supply ELN notionals/terms, child-fund look-through or autocallable terms. Fund-membership AUM is not derivative-risk coverage.

The original default call emits 285 rows for 15 funds. It uses a flat 20% fallback on 46 missing-IV rows: 19 nonzero positive-tenor legs (eight box legs and 11 other positions), 23 expiry-day legs and four zero positions. It assigns date-only expiry-day positions zero gamma/vega. The cleaned-input run prices 139 positions in nine funds, leaves 17 positive-tenor IV gaps and 74 expiry-day positions pending, excludes 47 zero positions, and separately identifies eight conditional box legs.

All 139 retained positions already had numeric IV in the original file; the new quotes confirm dates/terms and exercise style rather than increase this priced cohort. Their maximum IV difference is only 0.000535 volatility percentage points, consistent with the original file's rounded values. Six other original numeric IVs were not returned in the clean snapshot. Their undated fallback is disabled. Fewer accepted rows therefore does not imply fewer numbers in the source file.

## Reproduced aggregate results and controlled bridge

All risk columns are USD millions, MM = -ETF. Native-underlying sums are provided for comparison; they are not beta-adjusted SPX-equivalent or verified full-universe totals.

| Calculation stage | MM gamma / 1% | MM vega / vol point | Gamma change | Vega change |
|---|---:|---:|---:|---:|
| teammate_default_full_observed_rows | 489.787 | 10.539 | n.a. | n.a. |
| teammate_on_common_priced_position_cohort | 492.046 | 9.984 | 2.259 | -0.556 |
| same_cohort_new_iv_only_original_BSM_market_units | 492.045 | 9.983 | -0.001 | -0.000 |
| same_cohort_new_iv_market_units_all_BSM | 492.045 | 9.983 | 0.000 | 0.000 |
| structured_iv_exercise_aware_final | 486.979 | 10.014 | -5.066 | 0.031 |

The original rerun agrees with the progress report's rounded +$490m gamma and +$10.5m vega. The headline totals are numerically close, but substantial positive and negative cohort contributions offset. The controlled bridge holds the priced-position cohort fixed before changing IV, market/units and the American exercise model. It is an ordered attribution, not a claim of unique causal contributions.

In particular, original HELO MM gamma is -$56.307m versus -$7.221m on the newer partial book; one missing-IV leg priced at flat 20% in the original is now omitted, so this is not evidence that HELO's full risk fell. QQQI is +$67.275m versus +$41.783m because one of its two legs lacks a clean quote. GPIX, GPIQ and BALT originally used the flat-vol fallback and now remain unavailable. Comparing only the aggregate would hide these coverage changes.

## Comparison for every Top 20 fund

Missing data remain missing. Original zero on expiry day and conditional box net zero have different meanings.

| Fund | Original MM gamma | New MM gamma | Original MM vega | New MM vega | New priced legs | Remaining inputs |
|---|---:|---:|---:|---:|---:|---|
| JEPI | missing | missing | missing | missing | 0 | ELN option-equivalent book/terms missing |
| JEPQ | missing | missing | missing | missing | 0 | ELN option-equivalent book/terms missing |
| BOXX | 0.000 | 0.000 | 0.000 | 0.000 | 0 | box net zero conditional on exact terms/units |
| QQQI | 67.275 | 41.783 | 1.602 | 1.004 | 1 | 1 positive-tenor legs lack clean IV |
| SPYI | 96.730 | 96.729 | 1.616 | 1.616 | 2 | all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional |
| BUFR | missing | missing | missing | missing | 0 | same-date child books and ownership fractions missing |
| QYLD | 249.676 | 249.677 | 8.072 | 8.072 | 1 | all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional |
| DIVO | 5.505 | 5.509 | 0.074 | 0.072 | 1 | all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional |
| GPIX | 17.303 | missing | 0.161 | missing | 0 | 3 positive-tenor legs lack clean IV; 1 expiry-day legs need valuation/settlement/roll times |
| GPIQ | 6.347 | missing | 0.058 | missing | 0 | 3 positive-tenor legs lack clean IV; 1 expiry-day legs need valuation/settlement/roll times |
| HELO | -56.307 | -7.221 | -0.187 | 0.129 | 8 | 1 positive-tenor legs lack clean IV |
| KNG | 0.000 | missing | 0.000 | missing | 0 | 66 expiry-day legs need valuation/settlement/roll times |
| BALT | 0.048 | missing | 0.001 | missing | 0 | 4 positive-tenor legs lack clean IV |
| XYLD | 149.307 | 149.305 | 3.367 | 3.367 | 1 | all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional |
| ACYN | missing | missing | missing | missing | 0 | autocallable terms, notionals, correlations and model missing |
| FTHI | 26.127 | 26.127 | 0.579 | 0.579 | 5 | 1 expiry-day legs need valuation/settlement/roll times |
| ACIO | -56.433 | -57.636 | -0.588 | -0.611 | 113 | 5 positive-tenor legs lack clean IV; 1 expiry-day legs need valuation/settlement/roll times |
| FNOV | 0.000 | missing | 0.000 | missing | 0 | 4 expiry-day legs need valuation/settlement/roll times |
| BUFD | missing | missing | missing | missing | 0 | same-date child books and ownership fractions missing |
| SFLR | -15.788 | -17.293 | -4.216 | -4.215 | 7 | all nonzero positive-tenor supplied legs have IV; units/rates/dividends still provisional |

## Does the curve need to extend beyond one year?

Yes when the actual option expiry is beyond the observed grid and a surface is needed. A 392-day option needs either its own expiry smile or maturities on both sides, such as 12M and 18M/2Y, under an explicit interpolation convention. A 12M-only endpoint cannot supply observed 392D IV. No extrapolated value has been inserted.

The two 392D positions are DRI and CL single-stock options expiring 17 December 2027. Their contract IV is present in the cleaned workbook, so their baseline Greeks are already priced. They have no standard DRI/CL surface; extending only SPX/NDX/RTY would not solve their surface requirement. The standard index grids run from 30D to 12M, and their moneyness/tenor definitions still require confirmation.

| Fund | Underlying | Security | Expiry | DTE | Baseline status |
|---|---|---|---|---:|---|
| ACIO | DRI | DRI US L1727C240000 Equity | 2027-12-17 | 392 | priced_research_estimate |
| ACIO | CL | CL US L1727D975000 Equity | 2027-12-17 | 392 | priced_research_estimate |

For surface-based scenarios, first cover actual underlyings, expiries, strikes and the -10%/+10% shock range. Add short maturities below 30D and intraday timestamps for 0DTE. The same-date contract IV suffices for a baseline calculation under the stated model; it does not define a complete smile or its response to spot/time changes.

## Files and reproduction

- `python -B shuhao_compare.py` refreshes the structured-IV calculation and reproduces the verified original default pipeline.
- `shuhao_comparison_results.json`: commit/input hashes, cohort counts and controlled risk bridge.
- `shuhao_comparison_by_fund.csv`: all 20 funds, original/new Greeks and missing inputs.
- `shuhao_missing_iv_requests.csv`: 17 positive-tenor fund positions still lacking clean same-date IV.
- `shuhao_surface_requests.csv`: all nonzero source positions with actual expiries/strikes, current term coverage and required observations. It contains requests, not fabricated market data.

## Remaining limitations

Position units/multipliers, a dated SOFR/OIS curve, forwards/dividends, exact valuation and roll timestamps remain provisional. American quotes use a 400-step CRR tree in the newer run; the original uses BSM for all options. Sticky-delta remains blocked until surface conventions and coverage are resolved. All calculations here are partial research estimates.

## AI assistance and course constraint

The course brief requests no LLM project computations. This AI-assisted implementation and its outputs must not be represented as independently produced course calculations; discuss permitted use with the course team.
