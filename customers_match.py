"""Customer matching.

customers.csv has no email-domain column, so matching is by company name:
  * name appearing in the RFQ text (signature/body/title), and
  * the sender email's domain core (e.g. mesafab.example -> "mesafab").
Both are scored with rapidfuzz; the customer_id is set only when the best score
is >= CUSTOMER_MATCH_MIN_SCORE (default 90). Otherwise null - never guessed.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rapidfuzz import fuzz

from config import CUSTOMER_MATCH_MIN_SCORE, CUSTOMERS_CSV

EMAIL_RE = re.compile(r"[\w.\-]+@([\w.\-]+)")


@dataclass
class _Customer:
    customer_id: str
    name: str


def _load_customers() -> List[_Customer]:
    out: List[_Customer] = []
    with open(CUSTOMERS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = (row.get("customer_id") or "").strip()
            name = (row.get("name") or "").strip()
            if cid and name:
                out.append(_Customer(cid, name))
    return out


def _domain_cores(text: str) -> List[str]:
    cores = []
    for domain in EMAIL_RE.findall(text):
        core = domain.split(".")[0].lower()
        if core and "ridgeline" not in core:  # skip our own recipient domain
            cores.append(core)
    return cores


class CustomerMatcher:
    def __init__(self) -> None:
        self.customers = _load_customers()

    def match(self, meta_text: str) -> Tuple[Optional[str], Optional[str]]:
        if not meta_text:
            return None, None
        text = meta_text.lower()
        domains = _domain_cores(meta_text)

        best: Optional[_Customer] = None
        best_score = 0.0
        for cust in self.customers:
            name = cust.name.lower()
            name_letters = re.sub(r"[^a-z0-9]", "", name)
            score_name = fuzz.partial_ratio(name, text)
            score_domain = max(
                (fuzz.partial_ratio(name_letters, d) for d in domains), default=0
            )
            score = max(score_name, score_domain)
            if score > best_score:
                best, best_score = cust, score

        if best is not None and best_score >= CUSTOMER_MATCH_MIN_SCORE:
            return best.customer_id, best.name
        return None, None
