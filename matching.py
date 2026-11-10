"""Code-only matching of extracted lines to Ridgeline part numbers.

Order (per the brief):
  a) a Ridgeline part number present in the line AND in products.csv -> ~0.99
  b) else a competitor PN resolved via cross_reference.csv              -> ~0.95
  c) else a rapidfuzz description match against products.csv  -> score/100, <=0.85
  d) else no match                                                      -> 0.0

No LLM here; nothing is invented. Prices are never read.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz, process

from config import (AMBIGUOUS_MARGIN, CROSS_REFERENCE_CSV,
                    DESCRIPTION_CONFIDENCE_CAP, DESCRIPTION_MIN_SCORE,
                    DESCRIPTION_RESCUE_SCORE, PRODUCTS_CSV)
from models import MatchMethod

RIDGELINE_PN_RE = re.compile(r"\bRL\d{4,}\b", re.IGNORECASE)
# Competitor PN candidates: start with >=2 letters, contain a digit (e.g. APX-BB-6206ZZ).
COMPETITOR_TOKEN_RE = re.compile(r"\b[A-Za-z]{2,}[-]?[A-Za-z0-9][A-Za-z0-9-]*\b")
# "Significant" numbers used for the size_mismatch check (bearing codes, sizes).
SIZE_TOKEN_RE = re.compile(r"\d+(?:/\d+)?(?:[a-zA-Z\"']+)?|\d{3,}")


@dataclass
class MatchResult:
    ridgeline_part_number: Optional[str]
    match_method: Optional[MatchMethod]
    confidence: float
    flags: List[str] = field(default_factory=list)


@dataclass
class _Product:
    part_number: str
    description: str
    unit: Optional[str]
    status: Optional[str]
    replaced_by: Optional[str]


def _normalize_pn(s: str) -> str:
    """Uppercase and strip spaces, dashes, and dots."""
    return re.sub(r"[\s\-.]", "", s).upper()


class Matcher:
    def __init__(self) -> None:
        self.products: List[_Product] = []
        self.pn_set: Set[str] = set()
        self.by_pn: Dict[str, _Product] = {}
        self._descriptions: List[str] = []
        self.cross_ref: Dict[str, Set[str]] = {}
        self._load_products()
        self._load_cross_reference()

    # --- data loading -------------------------------------------------------
    def _load_products(self) -> None:
        with open(PRODUCTS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pn = (row.get("part_number") or "").strip()
                if not pn:
                    continue
                prod = _Product(
                    part_number=pn,
                    description=(row.get("description") or "").strip(),
                    unit=(row.get("unit") or "").strip() or None,
                    status=(row.get("status") or "").strip() or None,
                    replaced_by=(row.get("replaced_by") or "").strip() or None,
                )
                self.products.append(prod)
                self.pn_set.add(pn.upper())
                self.by_pn[pn.upper()] = prod
                self._descriptions.append(prod.description)

    def _load_cross_reference(self) -> None:
        with open(CROSS_REFERENCE_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                comp = (row.get("competitor_part_number") or "").strip()
                rl = (row.get("ridgeline_part_number") or "").strip()
                if not comp or not rl:
                    continue
                self.cross_ref.setdefault(_normalize_pn(comp), set()).add(rl)

    # --- individual strategies ---------------------------------------------
    def _match_ridgeline_pn(self, text: str) -> Optional[str]:
        for m in RIDGELINE_PN_RE.findall(text):
            if m.upper() in self.pn_set:
                return m.upper()
        return None

    def _match_cross_reference(
        self, candidates: List[str]
    ) -> Tuple[Optional[str], List[str]]:
        for token in candidates:
            norm = _normalize_pn(token)
            if norm in self.cross_ref:
                rls = self.cross_ref[norm]
                if len(rls) == 1:
                    return next(iter(rls)), []
                # Ambiguous competitor PN (maps to several RLs): don't guess.
                return sorted(rls)[0], ["ambiguous_cross_reference"]
        return None, []

    def _match_description(self, query: str) -> MatchResult:
        if not query.strip() or not self._descriptions:
            return MatchResult(None, None, 0.0, ["no_match"])
        # token_set_ratio ranks correctly but under-scores terse lines; a
        # distinctive query token (bearing code / belt code / size, e.g.
        # "6204-2RS", "A42", "1/2-13") found in the candidate rescues the score.
        top = process.extract(
            query, self._descriptions, scorer=fuzz.token_set_ratio, limit=5
        )
        if not top:
            return MatchResult(None, None, 0.0, ["no_match"])

        # Candidates tied near the top score: prefer the one sharing the most
        # query word-tokens (disambiguates e.g. grade 5 vs grade 8, 2" vs 3").
        top_score = top[0][1]
        near = [t for t in top if top_score - t[1] <= AMBIGUOUS_MARGIN]
        ambiguous = len(near) > 1
        if ambiguous:
            q_words = self._words(query)
            best_desc, best_score, best_idx = max(
                near, key=lambda t: len(q_words & self._words(t[0]))
            )
        else:
            best_desc, best_score, best_idx = top[0]

        dtokens = self._distinctive_tokens(query)
        if any(self._token_in(tok, best_desc) for tok in dtokens):
            best_score = max(best_score, DESCRIPTION_RESCUE_SCORE)

        if best_score < DESCRIPTION_MIN_SCORE:
            return MatchResult(None, None, 0.0, ["no_match"])

        confidence = min(best_score / 100.0, DESCRIPTION_CONFIDENCE_CAP)
        flags: List[str] = []
        if ambiguous:
            flags.append("ambiguous_match")
        if self._size_conflict(query, best_desc):
            flags.append("size_mismatch")
        return MatchResult(
            ridgeline_part_number=self.products[best_idx].part_number,
            match_method=MatchMethod.description,
            confidence=round(confidence, 2),
            flags=flags,
        )

    @staticmethod
    def _words(text: str) -> Set[str]:
        return set(re.findall(r"[a-z0-9/\"#\-]{2,}", text.lower()))

    @staticmethod
    def _distinctive_tokens(query: str) -> Set[str]:
        """Identifier-like tokens: contain a digit and either a letter/symbol or
        >=4 digits (bearing codes). Excludes bare quantities like 20, 150."""
        out: Set[str] = set()
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9/\-\"#.]*", query):
            if not any(c.isdigit() for c in tok):
                continue
            digits = re.sub(r"\D", "", tok)
            has_alpha_sym = any(c.isalpha() for c in tok) or any(c in '/-"#' for c in tok)
            if has_alpha_sym or len(digits) >= 4:
                out.add(tok.lower())
        return out

    @staticmethod
    def _token_in(token: str, description: str) -> bool:
        desc = description.lower()
        if token in desc:
            return True
        digits = re.sub(r"\D", "", token)  # bearing code e.g. 6205 inside 6205-2RS
        return len(digits) >= 4 and digits in re.sub(r"\D", "", desc)

    @staticmethod
    def _size_conflict(query: str, candidate: str) -> bool:
        """True if the query carries significant size/number tokens, none of which
        appear in the candidate description."""
        q_nums = {t for t in SIZE_TOKEN_RE.findall(query) if any(c.isdigit() for c in t)}
        q_nums = {t for t in q_nums if len(re.sub(r"\D", "", t)) >= 2}
        if not q_nums:
            return False
        cand = candidate.lower()
        return not any(t.lower() in cand for t in q_nums)

    def _resolve_discontinued(self, result: MatchResult) -> MatchResult:
        """If the matched product is discontinued and has a replacement, return the
        replacement part number, flag discontinued_replaced, and force review.
        Applies to every match method."""
        pn = result.ridgeline_part_number
        if not pn:
            return result
        prod = self.by_pn.get(pn.upper())
        if prod and (prod.status or "").lower() == "discontinued" and prod.replaced_by:
            result.ridgeline_part_number = prod.replaced_by
            if "discontinued_replaced" not in result.flags:
                result.flags.append("discontinued_replaced")
        return result

    # --- public API ---------------------------------------------------------
    def match(
        self, text: str, explicit_competitor: Optional[str] = None
    ) -> MatchResult:
        """Match a single line. `text` is used for Ridgeline-PN/competitor scans
        and fuzzy description; `explicit_competitor` (Excel Item #) is tried first
        for the cross-reference step."""
        # a) Ridgeline part number present and real.
        rl = self._match_ridgeline_pn(text)
        if rl:
            return self._resolve_discontinued(
                MatchResult(rl, MatchMethod.ridgeline_part_number, 0.99, [])
            )

        # b) Cross-reference (explicit Excel part first, then tokens from text).
        candidates: List[str] = []
        if explicit_competitor:
            candidates.append(explicit_competitor)
        candidates += COMPETITOR_TOKEN_RE.findall(text)
        rl, xref_flags = self._match_cross_reference(candidates)
        if rl:
            confidence = 0.95 if not xref_flags else 0.6
            return self._resolve_discontinued(
                MatchResult(rl, MatchMethod.cross_reference, confidence, xref_flags)
            )

        # c) Fuzzy description match (capped).
        result = self._match_description(text)
        if result.ridgeline_part_number:
            return self._resolve_discontinued(result)

        # d) No match.
        return MatchResult(None, None, 0.0, ["no_match"])
