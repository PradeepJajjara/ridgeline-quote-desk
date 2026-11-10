"""Pydantic models for the Ridgeline Quote Desk pipeline.

Two groups:
  * Extraction models  - validate the raw JSON the LLM returns (no matching).
  * Output models      - the final per-RFQ JSON written to outputs/.

Field order in LineItem/RFQOutput is the exact order required in the output.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field

# quantity may be an int ("12") or a float ("2.5"); null when unknown.
Quantity = Optional[Union[int, float]]


class MatchMethod(str, Enum):
    """How a line was matched to a Ridgeline part. `null` (None) means no match."""

    ridgeline_part_number = "ridgeline_part_number"
    cross_reference = "cross_reference"
    description = "description"
    llm = "llm"


# --- Extraction models (what the LLM is allowed to produce) ------------------
class ExtractedLine(BaseModel):
    """A single line as extracted from the source. No part numbers, no matching.

    `effective_description` and `note` are internal aids: the LLM fills them only
    when a later message amends a line or a line references another ("same as
    above"). `effective_description` (the resolved spec) feeds the code matcher
    and is dropped before output; `note` is carried through to the output.
    """

    model_config = {"extra": "forbid"}

    as_written: str = Field(min_length=1)
    quantity: Quantity = None
    unit: Optional[str] = None
    effective_description: Optional[str] = None
    note: Optional[str] = None
    flags: List[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """The full structured result of extracting one RFQ via the LLM."""

    model_config = {"extra": "forbid"}

    lines: List[ExtractedLine] = Field(default_factory=list)
    non_item_rows: List[str] = Field(default_factory=list)


# --- Output models (final JSON per RFQ) -------------------------------------
class LineItem(BaseModel):
    """One output line. Field order here is the output order."""

    line: int = Field(ge=1)
    as_written: str = Field(min_length=1)
    quantity: Quantity = None
    unit: Optional[str] = None
    ridgeline_part_number: Optional[str] = None
    match_method: Optional[MatchMethod] = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool
    flags: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class RFQOutput(BaseModel):
    """The full output document for one RFQ."""

    rfq_id: str
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    lines: List[LineItem]
    non_item_rows: List[str] = Field(default_factory=list)

    def to_output_dict(self) -> dict:
        """Serialise to the exact shape written to disk.

        `note` is dropped on any line where it is None (the brief: include note
        only where needed), while other unknown values stay as JSON null.
        """
        data = self.model_dump(mode="json")
        for line in data["lines"]:
            if line.get("note") is None:
                line.pop("note", None)
        return data
