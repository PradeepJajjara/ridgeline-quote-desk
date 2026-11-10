"""File-type loaders for incoming RFQs.

Each RFQ is turned into a `LoadedRFQ`:
  * kind == "text"/"pdf"  -> `text` holds the source text for the LLM path.
  * kind == "excel"       -> `excel_rows` holds parsed item rows (code path),
                             `non_item_rows` holds delivery/other non-item text.
  * kind == "photo"       -> nothing is extracted; the caller emits a stub.

`meta_text` is the region used for customer matching (email headers/signature,
or the Excel title block). The pipeline never reads answer_key/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd
import pdfplumber

from config import UNIT_MAP

Quantity = Optional[Union[int, float]]


class NoExtractableTextError(RuntimeError):
    """Raised when a PDF yields no text (would need OCR / a vision model)."""


@dataclass
class ExcelItemRow:
    """One parsed Excel item row (code path, no LLM)."""

    as_written: str
    quantity: Quantity
    unit: Optional[str]
    competitor_part: Optional[str]
    description: Optional[str]


@dataclass
class LoadedRFQ:
    rfq_id: str
    source_path: Path
    kind: str  # "text" | "pdf" | "excel" | "photo"
    text: Optional[str] = None
    excel_rows: Optional[List[ExcelItemRow]] = None
    non_item_rows: List[str] = field(default_factory=list)
    meta_text: str = ""


# --- unit helper -------------------------------------------------------------
def normalize_unit(raw: object) -> Optional[str]:
    """Map a free-text unit to the answer key's vocabulary.

    Known tokens (each/box/case/ft/...) map via config.UNIT_MAP. Unknown tokens
    are upper-cased and returned as-is (never guessed away). Empty -> None.
    """
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    key = token.lower().rstrip(".")
    if key in UNIT_MAP:
        return UNIT_MAP[key]
    return token.upper()


def _is_number(v: object) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return not (isinstance(v, float) and pd.isna(v))
    return False


def _clean_cell(v: object) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


# --- loaders -----------------------------------------------------------------
def load_text(path: Path, rfq_id: str) -> LoadedRFQ:
    text = Path(path).read_text(encoding="utf-8")
    return LoadedRFQ(rfq_id=rfq_id, source_path=Path(path), kind="text",
                     text=text, meta_text=text)


def load_pdf(path: Path, rfq_id: str) -> LoadedRFQ:
    with pdfplumber.open(path) as pdf:
        # Text, not extract_tables: tables tend to merge columns.
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    if not text.strip():
        raise NoExtractableTextError(
            f"{rfq_id}: PDF has no extractable text (needs OCR or a vision model)."
        )
    return LoadedRFQ(rfq_id=rfq_id, source_path=Path(path), kind="pdf",
                     text=text, meta_text=text)


def _find_header_row(df: pd.DataFrame) -> int:
    """Locate the header row by searching for a 'Qty'/'Quantity' cell."""
    for i, row in df.iterrows():
        for cell in row.values:
            token = _clean_cell(cell)
            if token and token.lower().rstrip(".") in ("qty", "quantity"):
                return int(i)
    raise ValueError("could not find a Qty header cell in the Excel sheet")


def _map_columns(header_row: pd.Series) -> dict:
    """Map logical fields -> column index using the header row labels."""
    mapping: dict = {}
    for col, cell in header_row.items():
        label = (_clean_cell(cell) or "").lower().rstrip(".")
        if label in ("qty", "quantity"):
            mapping["qty"] = col
        elif label in ("uom", "unit", "units"):
            mapping["unit"] = col
        elif label.startswith("desc"):
            mapping["description"] = col
        elif label.startswith("note"):
            mapping["notes"] = col
        elif label.startswith("item") or "part" in label:
            mapping["part"] = col
    return mapping


def load_excel(path: Path, rfq_id: str) -> LoadedRFQ:
    raw = pd.read_excel(path, header=None, dtype=object)
    header_idx = _find_header_row(raw)
    cols = _map_columns(raw.iloc[header_idx])

    # meta_text = everything above the header (title/bid block) for customer match.
    meta_cells = []
    for _, row in raw.iloc[:header_idx].iterrows():
        meta_cells += [c for c in (_clean_cell(v) for v in row.values) if c]
    meta_text = "\n".join(meta_cells)

    item_rows: List[ExcelItemRow] = []
    non_item_rows: List[str] = []
    for _, row in raw.iloc[header_idx + 1:].iterrows():
        qty_cell = row[cols["qty"]] if "qty" in cols else None
        part = _clean_cell(row[cols["part"]]) if "part" in cols else None
        desc = _clean_cell(row[cols["description"]]) if "description" in cols else None
        unit = normalize_unit(row[cols["unit"]]) if "unit" in cols else None

        if _is_number(qty_cell):
            # as_written mirrors the answer key: Item # + Desc joined by a space.
            as_written = " ".join(p for p in (part, desc) if p)
            qty = int(qty_cell) if float(qty_cell).is_integer() else float(qty_cell)
            item_rows.append(ExcelItemRow(
                as_written=as_written or (part or desc or ""),
                quantity=qty, unit=unit, competitor_part=part, description=desc,
            ))
        else:
            # A row with text but no quantity is a delivery/other non-item row.
            texts = [c for c in (_clean_cell(v) for v in row.values) if c]
            if texts:
                non_item_rows.append(" ".join(texts))

    return LoadedRFQ(rfq_id=rfq_id, source_path=Path(path), kind="excel",
                     excel_rows=item_rows, non_item_rows=non_item_rows,
                     meta_text=meta_text)


def load_photo(path: Path, rfq_id: str) -> LoadedRFQ:
    """No extraction: the caller emits the stub line."""
    return LoadedRFQ(rfq_id=rfq_id, source_path=Path(path), kind="photo")


# --- dispatch ----------------------------------------------------------------
_LOADERS_BY_SUFFIX = {
    ".txt": load_text,
    ".pdf": load_pdf,
    ".xlsx": load_excel,
    ".xls": load_excel,
    ".png": load_photo,
    ".jpg": load_photo,
    ".jpeg": load_photo,
}


def load_rfq(path: Path) -> LoadedRFQ:
    path = Path(path)
    rfq_id = path.stem
    loader = _LOADERS_BY_SUFFIX.get(path.suffix.lower())
    if loader is None:
        raise ValueError(f"{rfq_id}: unsupported file type {path.suffix!r}")
    return loader(path, rfq_id)
