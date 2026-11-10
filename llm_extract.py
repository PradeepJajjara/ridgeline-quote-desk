"""LLM extraction for the email/PDF/text RFQs.

Uses Claude Haiku via the Anthropic SDK with forced tool-use so the model must
return schema-valid JSON, which is then validated by the Pydantic
ExtractionResult. Temperature 0. The API key is loaded from .env and never
printed or logged.

Matching is NOT done here: the model extracts lines only and never invents part
numbers, quantities, or prices.
"""
from __future__ import annotations

import os

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from config import LLM_MAX_TOKENS, LLM_TEMPERATURE, MODEL
from models import ExtractionResult

load_dotenv()

# If the configured MODEL id is unknown to the API, fall back to this one.
FALLBACK_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You extract line items from an industrial-supply Request for \
Quotation (RFQ). Return ONLY structured data via the tool. Follow these rules exactly:

GENERAL
- Extract only what is actually written. If a value is unknown, use null. Never \
guess, infer, or invent part numbers, quantities, or specs.
- Ignore prices entirely. Do not output any price.
- Ignore greetings, signatures, phone numbers, subject lines, and email headers. \
They are not line items.

as_written (CRITICAL)
- Copy as_written VERBATIM from the source: it must be an exact substring of one \
source line. Do not paraphrase, reorder, normalize, or merge lines.
- If the item shows a supplier/part number next to its description (common in \
tables), INCLUDE that part number in as_written exactly as written (e.g. \
"APX-BB-6206ZZ Ball bearing 6206 ZZ shielded"). Omit only a leading line number \
and the separate quantity/unit columns.

QUANTITY & UNIT
- Convert number words to digits ("two" -> 2).
- If a quantity is vague/ambiguous ("a few", "some", "several"), set quantity to \
null and add the flag "ambiguous_quantity".
- Set unit only if a unit is actually stated. Normalize units to this vocabulary: \
each/ea/pcs/pieces -> EA, box/boxes -> BX, case/cases -> CS, feet/ft -> FT, \
pair/pairs -> PR, roll/rolls -> RL, set -> SET. If no unit is stated, use null.

FORWARDED / AMENDED EMAIL CHAINS
- The operative request is the customer's FINAL intent. When a later message \
amends an earlier line (changes a quantity or a spec), emit ONE line per final \
item. Do NOT emit the superseded version.
- For an amended line: set as_written to the VERBATIM original span, set quantity \
to the amended quantity, and write a short note describing the change (e.g. \
"Amended: gaskets quantity raised to 40"). If the amendment changes the product \
SPEC (e.g. motor 3HP -> 5HP), put the resolved spec in effective_description \
(e.g. "5 HP TEFC motor, 1800 rpm, 3 phase"); otherwise leave effective_description null.

LINES THAT REFERENCE ANOTHER LINE
- If a line says "same as above but ..." and the resolved spec is clear from the \
referenced line, put the resolved spec in effective_description. Otherwise leave \
it null. Still copy as_written verbatim.

NON-ITEM ROWS
- Put delivery/shipping/logistics instructions and other non-item content into \
non_item_rows as verbatim strings. Do not put greetings or signatures there.
"""

EXTRACT_TOOL = {
    "name": "emit_rfq_lines",
    "description": "Return the extracted RFQ line items and non-item rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "as_written": {
                            "type": "string",
                            "description": "Exact verbatim substring of a source line.",
                        },
                        "quantity": {"type": ["number", "null"]},
                        "unit": {"type": ["string", "null"]},
                        "effective_description": {
                            "type": ["string", "null"],
                            "description": "Resolved spec if amended/referenced; else null.",
                        },
                        "note": {"type": ["string", "null"]},
                        "flags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["as_written"],
                },
            },
            "non_item_rows": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["lines"],
    },
}


class LLMExtractor:
    """Thin wrapper that lazily creates the client and handles model fallback."""

    def __init__(self) -> None:
        self._client: Anthropic | None = None
        self.model = MODEL
        self.fallback_used = False

    @property
    def client(self) -> Anthropic:
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (check .env).")
            self._client = Anthropic(api_key=api_key)
        return self._client

    def _call(self, text: str, model: str):
        return self.client.messages.create(
            model=model,
            max_tokens=LLM_MAX_TOKENS,
            temperature=LLM_TEMPERATURE,
            system=SYSTEM_PROMPT,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": EXTRACT_TOOL["name"]},
            messages=[{
                "role": "user",
                "content": f"Extract the line items from this RFQ:\n\n{text}",
            }],
        )

    def _is_unknown_model_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return "model" in msg and ("not_found" in msg or "not found" in msg
                                   or "does not exist" in msg or "invalid" in msg)

    def extract(self, text: str, rfq_id: str) -> ExtractionResult:
        try:
            resp = self._call(text, self.model)
        except Exception as err:  # noqa: BLE001 - we inspect and selectively fall back
            if self.model != FALLBACK_MODEL and self._is_unknown_model_error(err):
                self.model = FALLBACK_MODEL
                self.fallback_used = True
                resp = self._call(text, self.model)
            else:
                raise

        tool_input = next(
            (block.input for block in resp.content if block.type == "tool_use"),
            None,
        )
        if tool_input is None:
            raise RuntimeError(f"{rfq_id}: model returned no tool_use block.")

        try:
            return ExtractionResult.model_validate(tool_input)
        except ValidationError:
            # One corrective retry: re-send asking for strict schema adherence.
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=LLM_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                system=SYSTEM_PROMPT,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": EXTRACT_TOOL["name"]},
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract the line items from this RFQ. Return strictly valid "
                        f"data matching the tool schema.\n\n{text}"
                    ),
                }],
            )
            tool_input = next(
                (b.input for b in resp.content if b.type == "tool_use"), None
            )
            return ExtractionResult.model_validate(tool_input or {"lines": []})
