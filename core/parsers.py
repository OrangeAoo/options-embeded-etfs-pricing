"""Parse Bloomberg-style option identifiers into contract terms.

Handles listed and FLEX tickers from HLDR / BQL holdings, e.g.

    4SPY US 12/18/26 P680.59 FLX     (FLEX: leading digit on the root)
    SPXW US 12/18/26 C7600 Index
    QQQ US 12/19/26 C520 Equity

FLEX Greeks are often blank in Bloomberg, so we read terms from the ticker
and price the leg ourselves in greek_engine.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Option roots that map to a different pricing underlying.
ROOT_ALIASES = {
    "SPXW": "SPX",
    "NDXP": "NDX",
    "NQX": "NDX",
    "RUTW": "RUT",
    "XSP": "SPX",  # mini-SPX: same vol surface; size via multiplier
}

OPTION_ID_RE = re.compile(
    r"^\s*(?P<root>\S+)\s+(?P<exch>[A-Z]{2})\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class OptionTerms:
    root: str
    underlying: str
    expiry: date
    cp: str  # "C" or "P"
    strike: float
    is_flex: bool


def parse_option_date(text: str) -> date:
    month, day, year = (int(part) for part in text.split("/"))
    if year < 100:
        year += 2000
    return date(year, month, day)


def parse_option_id(holding_id: str) -> Optional[OptionTerms]:
    """Return OptionTerms for an option identifier, or None if it is not an option."""
    if not isinstance(holding_id, str):
        return None
    match = OPTION_ID_RE.match(holding_id)
    if not match:
        return None

    root = match["root"]
    is_flex = root[0].isdigit()
    clean_root = root.lstrip("0123456789")
    underlying = ROOT_ALIASES.get(clean_root, clean_root)
    return OptionTerms(
        root=root,
        underlying=underlying,
        expiry=parse_option_date(match["date"]),
        cp=match["cp"],
        strike=float(match["strike"]),
        is_flex=is_flex,
    )
