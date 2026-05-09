"""Structured extraction — one LLM call per schema field.

The all-at-once variant (single prompt asking for the full 14-field JSON
object) sometimes left fields blank or muddled them when several fields
shared similar Hebrew labels. Asking for one field at a time, with a tightly
scoped prompt and a single-property JSON schema, gives noticeably better
per-field quality at the cost of N× more model calls (where N is the number
of schema fields).

Each call:
- Prompt scopes the model to ONE field (name, type, description, Hebrew
  aliases, allowed enum values, sub-shape for list_of_objects).
- `format` is a JSON schema like `{"value": <type>}` so Ollama enforces a
  valid response server-side.
- The wrapper unwraps `value` and writes it into the row dict.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from schema import LIST_TYPES, Field, Schema

log = logging.getLogger(__name__)

# MODEL = "gemma4:latest" (now passed as argument)
TIMEOUT = 600


# --------------------------------------------------------------- per-field schema

def _value_type_schema(field: Field) -> dict[str, Any]:
    """JSON-schema fragment describing the *value* type of a single field."""
    type_map: dict[str, Any] = {
        "string": {"type": ["string", "null"]},
        "integer": {"type": ["integer", "null"]},
        "number": {"type": ["number", "null"]},
        "boolean": {"type": ["boolean", "null"]},
        "date": {"type": ["string", "null"]},
        "float": {"type": ["number", "null"]},
    }
    if field.type in type_map:
        return type_map[field.type]
    if field.type == "enum":
        return {"type": "string", "enum": list(field.values)}
    if field.type == "list_of_strings":
        return {"type": "array", "items": {"type": "string"}}
    if field.type == "list_of_objects":
        item_props = {
            sub: type_map.get(sub_t, {"type": ["string", "null"]})
            for sub, sub_t in (field.item_schema or {}).items()
        }
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": item_props,
                "required": list(item_props),
            },
        }
    return {"type": ["string", "null"]}


def _envelope_schema(field: Field) -> dict[str, Any]:
    """Wrap the value-type schema in {"value": ...} so we know what to unwrap."""
    return {
        "type": "object",
        "properties": {"value": _value_type_schema(field)},
        "required": ["value"],
    }


# ----------------------------------------------------------------------- prompts

def _field_hint(field: Field) -> str:
    parts: list[str] = []
    if field.description:
        parts.append(field.description.replace("\n", " ").strip())
    if field.hebrew_aliases:
        parts.append(f"Hebrew labels to look for: {', '.join(field.hebrew_aliases)}")
    if field.type == "enum":
        parts.append(f"Allowed values: {', '.join(field.values)}")
    if field.type == "list_of_objects" and field.item_schema:
        items = ", ".join(f"{k}:{v}" for k, v in field.item_schema.items())
        parts.append(f"Each item is an object with: {items}")
    return "  - " + "\n  - ".join(parts) if parts else ""


_TYPE_PHRASE = {
    "string": "a string (or null if not in the document)",
    "integer": "an integer (or null)",
    "number": "a number (or null)",
    "boolean": "a boolean (or null)",
    "enum": "exactly one of the allowed enum values",
    "date": "a string (usually YYYY-MM-DD)",
    "float": "a number",
    "list_of_strings": "an array of strings (use [] if none)",
    "list_of_objects": "an array of objects (use [] if none)",
}


def _build_prompt(field: Field, transcript: str) -> str:
    if field.type == "enum":
        # Enum fields are usually inferred (e.g. "exposed_to_pigeons: yes" is
        # never literal in the transcript — the model reads "מגדל יונים בגג"
        # and concludes yes). The default per-field rule of "verbatim only"
        # blocks that, so enum prompts get an explicit inference instruction.
        rules = (
            "  - Choose the value that best matches the transcript content,\n"
            "    using the field description above to guide the decision.\n"
            "  - The value will normally NOT appear verbatim - you must infer\n"
            "    it from semantic content (e.g. mentions of pigeons/birds in\n"
            "    the patient's environment imply 'yes' for an exposure field).\n"
            "  - If the transcript contains no relevant information at all,\n"
            "    use 'unknown' if it is in the allowed set; otherwise pick the\n"
            "    value that best matches.\n"
        )
    else:
        rules = (
            "  - Copy the value verbatim from the transcript when present.\n"
            "  - Do NOT invent values that are not in the transcript.\n"
            "  - Use null / [] when the field is absent (it will be filled with a default 'NA' later).\n"
        )
    return (
        f"Extract ONLY the field '{field.name}' from the medical-record "
        f"transcript below.\n\n"
        f"FIELD\n"
        f"  Name: {field.name}\n"
        f"  Type: {field.type} → {_TYPE_PHRASE.get(field.type, 'a value')}\n"
        f"{_field_hint(field)}\n\n"
        f"RULES\n"
        f"{rules}"
        f"  - Return JSON of the form {{\"value\": <extracted>}}. No prose.\n\n"
        f"TRANSCRIPT\n<<<\n{transcript}\n>>>\n"
    )


# ------------------------------------------------------------------- HTTP / parse

def _call(host: str, model: str, prompt: str, json_schema: dict[str, Any]) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": json_schema,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 1024},
    }
    resp = requests.post(
        f"{host.rstrip('/')}/api/generate", json=payload, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def _try_parse(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _default_for(field: Field) -> Any:
    if field.default is not None:
        return field.default
    return [] if field.type in LIST_TYPES else None


# ----------------------------------------------------------------- per-field call

def _extract_one(
    transcript: str, field: Field, host: str, model: str
) -> tuple[Any, str | None]:
    schema = _envelope_schema(field)
    prompt = _build_prompt(field, transcript)
    raw = _call(host, model, prompt, schema)
    parsed = _try_parse(raw)
    if not parsed or "value" not in parsed:
        return _default_for(field), f"unparseable response: {raw[:200]!r}"
    
    val = parsed["value"]
    # If the model explicitly returned null or empty list, but we have a default, use it
    if val is None or val == []:
        if field.default is not None:
            return field.default, None
            
    return val, None


# ---------------------------------------------------------------------- public API

def extract_fields(
    transcript: str, schema: Schema, host: str, model: str
) -> tuple[dict[str, Any], str | None]:
    """One LLM call per schema field; assemble the result row."""
    log.info(
        "Per-field structured extraction: model=%s, fields=%d, transcript_chars=%d",
        model,
        len(schema.fields),
        len(transcript),
    )

    row = schema.empty_row()
    errors: list[str] = []
    overall_start = time.monotonic()
    for field in schema.fields:
        t0 = time.monotonic()
        try:
            value, err = _extract_one(transcript, field, host, model)
        except Exception as exc:  # noqa: BLE001
            log.error("  [%s] FAILED: %s", field.name, exc)
            errors.append(f"{field.name}: {exc}")
            continue
        dt = time.monotonic() - t0
        if err:
            log.warning("  [%s] %.1fs WARN %s", field.name, dt, err)
            errors.append(f"{field.name}: {err}")
            continue
        preview = repr(value)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        log.info("  [%s] %.1fs -> %s", field.name, dt, preview)
        row[field.name] = value

    log.info(
        "Per-field extraction done: %d fields in %.1fs",
        len(schema.fields),
        time.monotonic() - overall_start,
    )
    return row, ("; ".join(errors) if errors else None)
