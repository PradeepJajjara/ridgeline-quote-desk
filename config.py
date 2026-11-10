"""Central configuration for the Ridgeline Quote Desk pipeline.

Single source of truth for paths, the review threshold, and matching knobs.
NOTE: this module intentionally does NOT define the answer_key path. The
pipeline must never read answer_key/; only evaluate.py may, and it defines
that path itself.
"""
from __future__ import annotations

from pathlib import Path

# --- Paths ---
REPO_ROOT = Path(__file__).resolve().parent
RFQ_DIR = REPO_ROOT / "rfqs"
OUTPUT_DIR = REPO_ROOT / "outputs"
PRODUCTS_CSV = REPO_ROOT / "products.csv"
CROSS_REFERENCE_CSV = REPO_ROOT / "cross_reference.csv"
CUSTOMERS_CSV = REPO_ROOT / "customers.csv"

# --- Review threshold --------------------------------------------------------
# ONE constant drives needs_review across the whole pipeline. Start at 0.8;
# the final value is chosen after looking at evaluate.py's threshold sweep.
REVIEW_THRESHOLD = 0.8

# --- LLM extraction ----------------------------------------------------------
# Single config constant for the model id. If the first live call fails with an
# unknown-model error, switch this to "claude-haiku-4-5" (the current Haiku id).
MODEL = "claude-haiku-5-5"
LLM_TEMPERATURE = 0
LLM_MAX_TOKENS = 2048

# --- Customer matching -------------------------------------------------------
# Fuzzy company-name match against customers.csv `name`; below this score the
# customer_id is left null (never guessed).
CUSTOMER_MATCH_MIN_SCORE = 90

# --- Description (fuzzy) matching -------------------------------------------
DESCRIPTION_CONFIDENCE_CAP = 0.85  # rapidfuzz score/100 is capped at this
DESCRIPTION_MIN_SCORE = 50         # below this fuzzy score -> no_match (never guess)
DESCRIPTION_RESCUE_SCORE = 70      # score given when a distinctive token matches
AMBIGUOUS_MARGIN = 8               # top-2 scores within this -> ambiguous_match

# --- Unit normalisation ------------------------------------------------------
# Maps free-text units to the answer key's vocabulary. Keys are lowercased and
# stripped of trailing punctuation before lookup.
UNIT_MAP = {
    "each": "EA", "ea": "EA", "ea.": "EA", "pc": "EA", "pcs": "EA",
    "piece": "EA", "pieces": "EA", "unit": "EA", "units": "EA",
    "box": "BX", "boxes": "BX", "bx": "BX",
    "case": "CS", "cases": "CS", "cs": "CS",
    "feet": "FT", "foot": "FT", "ft": "FT", "ft.": "FT",
    "pair": "PR", "pairs": "PR", "pr": "PR",
    "roll": "RL", "rolls": "RL",
    "set": "SET", "sets": "SET",
}
