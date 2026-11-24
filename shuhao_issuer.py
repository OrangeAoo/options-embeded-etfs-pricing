"""Read-only First Trust evidence capture; original data/ is never written.

Run explicitly with --fetch. Cached facts are a separate source, never silently
substituted into the Bloomberg snapshot. Conflicting dates/NAVs remain flagged.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import ssl
import time
from urllib.request import Request, urlopen

from lxml import html
import certifi


def number(value):
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    return float(text)


def cells(row):
    return [" ".join(" ".join(c.itertext()).split()) for c in row.xpath("./th|./td")]


def parse_holdings(document: str) -> dict:
    tree = html.fromstring(document)
    text = " ".join(tree.itertext())
    match = re.search(r"Holdings\s+of\s+the\s+Fund\s+as\s+of\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if not match:
        raise ValueError("issuer page has no explicit holdings as-of date")
    asof = datetime.strptime(match[1], "%m/%d/%Y").date().isoformat()
    records = []
    seen = set()
    for row in tree.xpath("//tr"):
        values = cells(row)
        if len(values) != 6 or values[0] == "Security Name":
            continue
        try:
            quantity, market_value, weight_pct = map(number, values[3:6])
        except ValueError:
            continue
        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        record = dict(name=values[0], identifier=values[1], cusip=values[2],
                      quantity=quantity, market_value_usd=market_value, weight_pct=weight_pct)
        option = re.match(r"^(\d{4}-\d{2}-\d{2}).*\s([CP])\s+([\d,.]+)$", values[0])
        if option:
            record.update(expiry=option[1], cp=option[2], strike=number(option[3]),
                          underlying="SPY", multiplier=100,
                          units="contracts_provisional_from_premium_scale")
        records.append(record)
    if not records:
        raise ValueError("no six-column holdings records found; inspect issuer schema")
    return {"holdings_asof": asof, "holdings": records,
            "holdings_net_value_usd": sum(r["market_value_usd"] for r in records)}


def parse_history(document: str, target_date: str) -> dict:
    target = datetime.fromisoformat(target_date).strftime("%-m/%-d/%Y")
    for row in html.fromstring(document).xpath("//tr"):
        values = cells(row)
        if len(values) == 6 and values[0].lstrip("0").replace("/0", "/") == target:
            return {"pricing_asof": target_date, "nav_per_share": number(values[2]),
                    "net_assets_usd": number(values[5])}
    raise ValueError(f"no historical NAV/net assets for {target_date}")


def capture(ticker: str, target_date: str) -> dict:
    result = {"ticker": ticker, "requested_asof": target_date,
              "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "sources": []}
    for kind, endpoint in [("holdings", "EtfHoldings"), ("history", "EtfPriceHistory")]:
        url = f"https://www.ftportfolios.com/Retail/Etf/{endpoint}.aspx?Ticker={ticker}"
        evidence = {"kind": kind, "url": url}
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (academic ETF snapshot review)"})
            with urlopen(req, timeout=35, context=ssl.create_default_context(cafile=certifi.where())) as response:
                body = response.read()
            evidence["sha256"] = hashlib.sha256(body).hexdigest()
            document = body.decode("utf-8", errors="replace")
            result.update(parse_holdings(document) if kind == "holdings" else parse_history(document, target_date))
        except Exception as exc:
            evidence["error"] = f"{type(exc).__name__}: {exc}"
        result["sources"].append(evidence)
        time.sleep(0.15)
    matches = result.get("holdings_asof") == target_date == result.get("pricing_asof")
    if matches and result.get("net_assets_usd", 0) > 0:
        ratio = result["holdings_net_value_usd"] / result["net_assets_usd"]
        result["holdings_value_to_nav_ratio"] = ratio
        result["snapshot_usable_for_research"] = abs(ratio - 1) <= 0.02
        result["shares_outstanding_from_nav"] = result["net_assets_usd"] / result["nav_per_share"]
        result["validation_note"] = "dates and net value reconcile within 2%" if abs(ratio - 1) <= 0.02 else "holdings total conflicts with same-date historical net assets; do not splice into base book"
    else:
        result["snapshot_usable_for_research"] = False
        result["validation_note"] = "missing or mismatched as-of dates; retained as evidence only"
    return result


def fetch_snapshot(sheets_dir: Path, target_date: str) -> dict:
    import csv
    tickers = {"BUFR", "BUFD"}
    for parent in ("BUFR", "BUFD"):
        with (sheets_dir / "holdings" / f"{parent}.csv").open() as stream:
            for row in csv.DictReader(stream):
                if row["SECURITY_TYP"] == "ETP":
                    tickers.add(row["ID"].split()[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda t: capture(t, target_date), sorted(tickers)))
    return {"target_date": target_date, "scope": "issuer source comparison; separate from frozen Bloomberg inputs",
            "funds": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", required=True)
    parser.add_argument("--date", default="2026-11-20")
    parser.add_argument("--output", type=Path, default=Path("shuhao_issuer_snapshot.json"))
    args = parser.parse_args()
    result = fetch_snapshot(Path(__file__).resolve().parent / "data" / "sheets", args.date)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"Captured {len(result['funds'])} issuer records; {sum(f['snapshot_usable_for_research'] for f in result['funds'])} reconcile")
