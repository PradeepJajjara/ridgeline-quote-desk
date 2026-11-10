"""Evaluation for the Ridgeline Quote Desk pipeline.

This is the ONLY module allowed to read answer_key/. It compares outputs/ to the
answer keys for rfq_01..03, aligns lines by line number, prints a report plus a
review-threshold sweep, and writes evaluation/results.json.

Metric definitions (also commented inline):
  * extraction_accuracy : of all expected (answer-key) lines, the share whose
                          quantity AND unit both match the key.
  * match_precision     : of the lines where WE returned a part number, the share
                          whose part number equals the expected part number.
  * match_recall        : of the expected lines that HAVE a part number, the share
                          where we returned the correct one.
  * review_rate(t)      : share of output lines marked needs_review at threshold t
                          (needs_review := confidence < t OR any flag).
  * auto_approved_precision(t) : of auto-approved lines (not needs_review) that
                          returned a part number, the share that are correct.
  * accuracy_by_match_method   : per match_method, the share of lines whose part
                          number matches the key (null vs null counts as correct).

Run: `python evaluate.py`
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "outputs"
ANSWER_KEY_DIR = REPO_ROOT / "answer_key"  # evaluate.py ONLY

# The configured threshold the pipeline actually used (single source of truth).
from config import REVIEW_THRESHOLD

EVAL_RFQS = ("rfq_01_email", "rfq_02_clearwater", "rfq_03_tricounty")
SWEEP_THRESHOLDS = (0.6, 0.7, 0.8, 0.9)


# --- helpers ----------------------------------------------------------------
def _num_eq(a, b) -> bool:
    """Numeric equality that treats 15 and 15.0 as equal and None==None as equal."""
    if a is None or b is None:
        return a is b or (a is None and b is None)
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def _lines_by_number(doc: dict) -> Dict[int, dict]:
    return {ln["line"]: ln for ln in doc.get("lines", [])}


def _needs_review(line: dict, threshold: float) -> bool:
    """Recompute the review decision at an arbitrary threshold for the sweep."""
    conf = line.get("confidence")
    conf = 0.0 if conf is None else conf
    return conf < threshold or bool(line.get("flags"))


class Aligned:
    """A key line paired with the output line at the same line number (or None)."""

    def __init__(self, rfq: str, key: dict, out: Optional[dict]):
        self.rfq = rfq
        self.key = key
        self.out = out


def _load_pairs() -> List[Aligned]:
    pairs: List[Aligned] = []
    for rfq in EVAL_RFQS:
        key_doc = json.loads((ANSWER_KEY_DIR / f"{rfq}.json").read_text("utf-8"))
        out_doc = json.loads((OUTPUT_DIR / f"{rfq}.json").read_text("utf-8"))
        out_by_num = _lines_by_number(out_doc)
        for line_no, key_line in _lines_by_number(key_doc).items():
            pairs.append(Aligned(rfq, key_line, out_by_num.get(line_no)))
    return pairs


# --- core metrics -----------------------------------------------------------
def extraction_accuracy(pairs: List[Aligned]) -> float:
    # Of all expected lines, share where quantity AND unit match the key.
    expected = len(pairs)
    correct = sum(
        1 for p in pairs
        if p.out is not None
        and _num_eq(p.out.get("quantity"), p.key.get("quantity"))
        and p.out.get("unit") == p.key.get("unit")
    )
    return correct / expected if expected else 0.0


def match_precision(pairs: List[Aligned]) -> float:
    # Of lines where we returned a part number, share equal to the expected PN.
    returned = [p for p in pairs if p.out and p.out.get("ridgeline_part_number")]
    if not returned:
        return 0.0
    correct = sum(
        1 for p in returned
        if p.out["ridgeline_part_number"] == p.key.get("expected_part_number")
    )
    return correct / len(returned)


def match_recall(pairs: List[Aligned]) -> float:
    # Of expected lines that have a PN, share we returned correctly.
    expected_pn = [p for p in pairs if p.key.get("expected_part_number")]
    if not expected_pn:
        return 0.0
    correct = sum(
        1 for p in expected_pn
        if p.out and p.out.get("ridgeline_part_number") == p.key["expected_part_number"]
    )
    return correct / len(expected_pn)


def review_rate(pairs: List[Aligned], threshold: float) -> float:
    # Share of output lines marked needs_review at this threshold.
    out_lines = [p for p in pairs if p.out is not None]
    if not out_lines:
        return 0.0
    reviewed = sum(1 for p in out_lines if _needs_review(p.out, threshold))
    return reviewed / len(out_lines)


def auto_approved_precision(pairs: List[Aligned], threshold: float) -> Optional[float]:
    # Of auto-approved lines that returned a PN, share that are correct.
    auto_pn = [
        p for p in pairs
        if p.out and not _needs_review(p.out, threshold)
        and p.out.get("ridgeline_part_number")
    ]
    if not auto_pn:
        return None
    correct = sum(
        1 for p in auto_pn
        if p.out["ridgeline_part_number"] == p.key.get("expected_part_number")
    )
    return correct / len(auto_pn)


def accuracy_by_match_method(pairs: List[Aligned]) -> Dict[str, float]:
    # Per match_method, share of lines whose PN matches the key (null==null ok).
    buckets: Dict[str, List[bool]] = {}
    for p in pairs:
        if p.out is None:
            continue
        method = p.out.get("match_method") or "null"
        correct = p.out.get("ridgeline_part_number") == p.key.get("expected_part_number")
        buckets.setdefault(method, []).append(correct)
    return {m: round(sum(v) / len(v), 4) for m, v in sorted(buckets.items())}


# --- report -----------------------------------------------------------------
def main() -> None:
    pairs = _load_pairs()

    results = {
        "threshold": REVIEW_THRESHOLD,
        "extraction_accuracy": round(extraction_accuracy(pairs), 4),
        "match_precision": round(match_precision(pairs), 4),
        "match_recall": round(match_recall(pairs), 4),
        "additional_metrics": {
            "review_rate": round(review_rate(pairs, REVIEW_THRESHOLD), 4),
            "auto_approved_precision": (
                round(aap, 4)
                if (aap := auto_approved_precision(pairs, REVIEW_THRESHOLD)) is not None
                else None
            ),
            "accuracy_by_match_method": accuracy_by_match_method(pairs),
        },
    }

    eval_dir = REPO_ROOT / "evaluation"
    eval_dir.mkdir(exist_ok=True)
    (eval_dir / "results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    expected = len(pairs)
    print(f"Evaluated {len(EVAL_RFQS)} RFQs, {expected} expected lines "
          f"(threshold={REVIEW_THRESHOLD})\n")
    print(f"  extraction_accuracy : {results['extraction_accuracy']:.3f}")
    print(f"  match_precision     : {results['match_precision']:.3f}")
    print(f"  match_recall        : {results['match_recall']:.3f}")
    print("  accuracy_by_match_method:")
    for method, acc in results["additional_metrics"]["accuracy_by_match_method"].items():
        print(f"      {method:<22} {acc:.3f}")

    print("\n  Threshold sweep (choose REVIEW_THRESHOLD from this):")
    print(f"      {'threshold':>9}  {'review_rate':>11}  {'auto_approved_precision':>23}")
    for t in SWEEP_THRESHOLDS:
        rr = review_rate(pairs, t)
        aap = auto_approved_precision(pairs, t)
        aap_s = "n/a" if aap is None else f"{aap:.3f}"
        print(f"      {t:>9.1f}  {rr:>11.3f}  {aap_s:>23}")

    print(f"\n  Wrote {eval_dir / 'results.json'}")


if __name__ == "__main__":
    main()
