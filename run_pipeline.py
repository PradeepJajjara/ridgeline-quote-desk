"""End-to-end Ridgeline Quote Desk pipeline.

For each RFQ in rfqs/:
  load -> extract (LLM for text/PDF, pandas for Excel, stub for photo)
       -> validate (source-text check on LLM lines)
       -> match (code: Ridgeline PN / cross-ref / fuzzy description)
       -> customer match -> needs_review -> write outputs/<rfq>.json

The pipeline never reads answer_key/. Run: `python run_pipeline.py`.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import List, Optional

from config import OUTPUT_DIR, REVIEW_THRESHOLD, RFQ_DIR
from customers_match import CustomerMatcher
from llm_extract import LLMExtractor
from loaders import LoadedRFQ, NoExtractableTextError, load_rfq
from matching import Matcher
from models import ExtractedLine, LineItem, MatchMethod, RFQOutput
from validate import in_source_text

STUB_NOTE = "photo source: needs OCR or a vision model"


def _needs_review(confidence: float, flags: List[str]) -> bool:
    return confidence < REVIEW_THRESHOLD or bool(flags)


def _match_text(as_written: str, effective_description: Optional[str]) -> str:
    """Text handed to the matcher: literal PNs live in as_written; the resolved
    spec (amendments/anaphora) is appended so fuzzy/matching sees the real ask."""
    if effective_description:
        return f"{as_written} {effective_description}"
    return as_written


def _build_stub(rfq: LoadedRFQ) -> RFQOutput:
    line = LineItem(
        line=1, as_written="entire RFQ", quantity=None, unit=None,
        ridgeline_part_number=None, match_method=None, confidence=0.0,
        needs_review=True, flags=["stub"], note=STUB_NOTE,
    )
    return RFQOutput(rfq_id=rfq.rfq_id, customer_id=None, customer_name=None,
                     lines=[line], non_item_rows=[])


def _process_llm_lines(
    extracted: List[ExtractedLine], source_text: str, matcher: Matcher
) -> List[LineItem]:
    lines: List[LineItem] = []
    for i, ex in enumerate(extracted, start=1):
        flags = list(ex.flags)
        # Stage 4: source-text check (LLM lines only).
        if not in_source_text(ex.as_written, source_text):
            flags.append("not_in_source")

        result = matcher.match(_match_text(ex.as_written, ex.effective_description))
        for f in result.flags:
            if f not in flags:
                flags.append(f)

        lines.append(LineItem(
            line=i, as_written=ex.as_written, quantity=ex.quantity, unit=ex.unit,
            ridgeline_part_number=result.ridgeline_part_number,
            match_method=result.match_method, confidence=result.confidence,
            needs_review=_needs_review(result.confidence, flags),
            flags=flags, note=ex.note,
        ))
    return lines


def _process_excel_lines(rfq: LoadedRFQ, matcher: Matcher) -> List[LineItem]:
    lines: List[LineItem] = []
    for i, row in enumerate(rfq.excel_rows or [], start=1):
        result = matcher.match(row.as_written, explicit_competitor=row.competitor_part)
        flags = list(result.flags)
        note = None
        if "no_match" in flags:
            note = "Not found in products or cross-reference."
        lines.append(LineItem(
            line=i, as_written=row.as_written, quantity=row.quantity, unit=row.unit,
            ridgeline_part_number=result.ridgeline_part_number,
            match_method=result.match_method, confidence=result.confidence,
            needs_review=_needs_review(result.confidence, flags),
            flags=flags, note=note,
        ))
    return lines


def process_rfq(
    path: Path, matcher: Matcher, cust_matcher: CustomerMatcher,
    extractor: LLMExtractor,
) -> RFQOutput:
    rfq = load_rfq(path)

    if rfq.kind == "photo":
        return _build_stub(rfq)

    if rfq.kind == "excel":
        lines = _process_excel_lines(rfq, matcher)
        non_item_rows = rfq.non_item_rows
    else:  # text / pdf -> LLM path
        extraction = extractor.extract(rfq.text or "", rfq.rfq_id)
        lines = _process_llm_lines(extraction.lines, rfq.text or "", matcher)
        non_item_rows = extraction.non_item_rows

    customer_id, customer_name = cust_matcher.match(rfq.meta_text)
    return RFQOutput(
        rfq_id=rfq.rfq_id, customer_id=customer_id, customer_name=customer_name,
        lines=lines, non_item_rows=non_item_rows,
    )


def _print_summary(out: RFQOutput) -> None:
    methods = Counter(
        (ln.match_method.value if isinstance(ln.match_method, MatchMethod) else "none")
        for ln in out.lines
    )
    review = sum(1 for ln in out.lines if ln.needs_review)
    by_method = ", ".join(f"{k}={v}" for k, v in sorted(methods.items()))
    print(f"  {out.rfq_id:<24} lines={len(out.lines):<2} "
          f"needs_review={review:<2} [{by_method}]")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    matcher = Matcher()
    cust_matcher = CustomerMatcher()
    extractor = LLMExtractor()

    print(f"Running pipeline (REVIEW_THRESHOLD={REVIEW_THRESHOLD})\n")
    for path in sorted(RFQ_DIR.iterdir()):
        if not path.is_file():
            continue
        try:
            out = process_rfq(path, matcher, cust_matcher, extractor)
        except NoExtractableTextError as err:
            print(f"  SKIPPED {path.name}: {err}")
            continue
        dest = OUTPUT_DIR / f"{out.rfq_id}.json"
        dest.write_text(
            json.dumps(out.to_output_dict(), indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        _print_summary(out)

    if extractor.fallback_used:
        print(f"\nNote: model id '{__import__('config').MODEL}' was unavailable; "
              f"fell back to '{extractor.model}'.")


if __name__ == "__main__":
    main()
