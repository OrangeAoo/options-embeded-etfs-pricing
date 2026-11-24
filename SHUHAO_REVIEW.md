# Project 10 research review and next steps

This branch adds a reproducible review alongside the existing project. All
original files, including `core/`, notebooks, Excel files and `data/sheets/`,
remain unchanged. The new entry point is `shuhao_review.py`; the original
entry points retain their existing behavior.

The implementation and validation were prepared with Codex assistance. The
course brief permits disclosed LLM-assisted research and prohibits LLM project
computations. This branch is a review artifact for team inspection; members
must independently reproduce and approve any calculation used in a submission
under the course rules. None of the estimates is a completed Top-20 finding.

## Run and inspect

```bash
python -m pip install -r shuhao_requirements.txt
python -m pytest tests test_shuhao_review.py -q
python shuhao_review.py
```

Open `shuhao_review_report.html` for the fund-level review. The complete
machine-readable outputs and per-leg exclusions are in
`shuhao_review_results.json`. Its `input_sha256` entries identify every original
runtime CSV. Re-running the review does not refresh or overwrite original data.

Optional, read-only issuer refresh:

```bash
python shuhao_issuer.py --fetch --date 2026-11-20
python shuhao_review.py
```

This contacts only public First Trust holdings and historical-pricing pages.
It saves factual records separately in `shuhao_issuer_snapshot.json`, with
URLs, retrieval times and source-response hashes. TLS verification remains on.
The parser requires `lxml`; HTTPS uses the `certifi` trusted CA bundle.

## Changes that can be verified now

| Issue in the prototype | Treatment in this additive entry point |
| --- | --- |
| Missing matched positions become zero | Unique-key joins; unmatched, invalid and wrong-date rows remain explicit exclusions |
| Every quantity is treated as contracts | Two guarded, provisional FNOV long-leg unit overrides in a derived copy; raw quantity and evidence retained |
| Missing IV becomes 20% without a source label | No default vol; separate quoted-IV and market-value-implied-IV subsets; failed price bounds remain missing |
| Zero shock changes baseline IV | Each leg retains its base IV in sticky-strike scenarios; zero-shock results must reconcile |
| Sticky-delta appears to work without a surface | An explicit error requires non-flat surfaces for every underlying; any future K/S proxy must be labelled |
| One-day options are put in the 0DTE bucket | 0DTE contains exactly zero days; one day starts the next bucket |
| Expiry-day legs are silently set to zero risk | Active expiry/roll-pending rows are held out until a timestamp/settlement policy and replacement book exist |
| Dollar-delta changes are called hedge trades | Rebalance shares are computed from native-unit option delta changes, then multiplied by the shocked native spot |
| A zero gamma curve is called a gamma flip | A flip requires an actual sign change |
| Missing funds appear as generic skips | ELN reconstruction, fund look-through and autocallable inputs have distinct statuses and request lists |

All scenarios are static-book research calculations. Time steps stop before
the first expiry; they do not invent a rolled book. SPX-only shocks move SPX
and SPY, leaving Nasdaq and single-name spots unchanged. The parallel shock
moves every supplied underlying. Spot/vol stresses use explicit vol-point
shifts, not an estimated empirical spot-vol response.

## Work advanced from the proposal

The review produces two clearly separated live-position estimates: one using
the supplied Bloomberg IVs and one adding IVs reconstructed from reported
market values. Reconstruction divides the signed reported value by signed
underlying units, checks European BSM price bounds, and solves for IV. It does
not turn a reported accounting mark into a live quote or an OVDV surface.

The original 285 records are classified as 145 active positions with supplied
IV, 10 active positions with recoverable price-implied IV, 74 expiry/roll-pending
positions, 47 zero positions, 8 BOXX box legs and 1 remaining inconsistent
mark. These are record counts, not complete-fund exposure coverage.

BOXX's two complete European boxes have net delta/gamma/vega zero by the
four-leg identity; no fictitious per-leg IV is needed. Its reported
value/weight-implied NAV is only about $125m against the list's $14.6bn AUM.
That scale/source discrepancy remains an explicit unresolved data issue.

BUFR and BUFD each require 12 child funds. Public issuer evidence for the
parents and all 24 children has been captured. Dates and holdings-net-value
versus historical NAV reconcile within an explicitly labelled 2% research
tolerance for 24 of the 26 records. FNOV and DNOV fail around the November
reset and remain excluded from the issuer preview. The preview checks
four-leg sizing, deep-ITM premium scale and same-date ownership fractions,
then prices only legs whose marks admit an IV. Per-child accepted legs,
failures and coverage weights are reported. These remain partial estimates.

Issuer look-through outputs are kept separate from the Bloomberg aggregate.
They are not added to it: parent/child overlap, reset timing and incompatible
source valuations must be reconciled first. In particular, BUFR holds FNOV,
which is also in the standalone universe. A full-universe result must count
the underlying option ownership once.

JEPI/JEPQ inference has a callable, tested interface requiring explicit
option-equivalent notional, strikes, expiries and weights. It is intentionally
not activated with arbitrary live-fund parameters. The 20% ELN asset-allocation
limit is not an option overwrite notional. Their issuer factsheets point to
an estimated SPX-equivalent unit delta/gamma document, a useful next source.
Options traded **on JEPI/JEPQ shares** do not describe options **inside** them.

## What is still required before final findings

1. Reconcile the 20-fund scope and common AUM date. The runtime CSV contains
   20 funds and leaves out CSHI, which is 21st among the original Excel's
   candidates using its stored AUMs. The review sorts those existing 20; it
   does not independently certify a complete market-wide ranking. Resolve
   BOXX inclusion and the meeting's buffer-family representative convention.
2. Confirm Bloomberg position units and the FNOV/DNOV reset cutoff. Keep the
   two provisional FNOV overrides provisional until the terminal confirms them.
3. Reconcile holdings-implied NAV with list AUM, particularly BOXX, JEPI/JEPQ
   and the other flagged funds. Do not blindly rescale quantities to match AUM.
4. Obtain OVDV surfaces, a same-date SOFR curve and reliable index dividends.
   Current flat 4% rates, zero substitutions for missing dividends and
   European exercise treatment remain disclosed approximations. Single-stock
   American exercise/early-exercise risk needs a separate decision.
5. Calibrate JEPI/JEPQ using ELN schedules and issuer delta/gamma evidence;
   validate the reconstructed overlay on observable funds before publishing
   an inferred range. ACYN needs a path-dependent swap/autocallable model.
6. Complete the look-through, resolve ownership double counting, then build
   full-fund aggregates and spot/vol/time profiles. Obtain correctly scaled
   option OI gamma/vega and ES/NQ ADV before claiming market-wide impact.

The November 24 progress update can describe the data pipeline, validation
repairs, issuer evidence, remaining coverage and proposed gamma-profile
extension. Present the current numerical outputs as partial research
estimates under review. The course document requests a progress report;
the detailed November 24 milestones in the proposal are the team's plan.

## Sources and provenance

- Existing Bloomberg exports: `data/sheets/`, date field November 20, 2026;
  AUM dates and position units still require confirmation.
- [BUFR issuer holdings](https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=BUFR)
  and [BUFD issuer holdings](https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=BUFD).
  Child-fund URLs and dated pricing evidence are in the issuer cache.
- [FNOV issuer holdings](https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=FNOV)
  and [historical net assets](https://www.ftportfolios.com/Retail/Etf/EtfPriceHistory.aspx?Ticker=FNOV).
- [JEPI issuer page](https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-equity-premium-income-etf-46641q332/)
  and [JEPQ issuer page](https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-jepq-46654q203).
- [JEPQ issuer factsheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/fs-jepq.pdf),
  which references the unit delta/gamma document; that document has not yet
  been retrieved or used as a quantitative input.
- The team's local proposal/November 19 meeting notes and the existing course
  project brief define the intended outputs and limitations.
