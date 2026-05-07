"""Structured-extraction wrapper around Ollama's JSON-schema mode.

Uses gemma4:latest (9.6 GB) by default. The 26B variant loops on schema-rich
prompts (emits duplicate keys / repeating tokens like
"_exposures_exposures_..."), so it is unreliable here. The smaller variant
combined with server-side JSON-schema enforcement is both faster and produces
structurally valid JSON every time.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from schema import Schema

log = logging.getLogger(__name__)

MODEL = "gemma4:latest"
TIMEOUT = 1200

SYSTEM = (
    "You are an information extraction system for medical visit notes.\n"
    "Use null for fields you cannot find. Use [] for list fields with no entries.\n"
    "Do NOT invent values. Copy values verbatim from the transcript when present.\n"
)


def _build_prompt(schema: Schema, transcript: str, strict: bool) -> str:
    schema_text = schema.render_for_prompt()
    extra = (
        "\nRESPOND WITH JSON ONLY. NO MARKDOWN. NO COMMENTS. NO TEXT BEFORE OR AFTER."
        if strict
        else ""
    )
    return (
        f"{SYSTEM}\n"
        f"Document language hint: {schema.language_hint}\n\n"
        f"SCHEMA (Hebrew aliases hint where to find each field):\n{schema_text}\n\n"
        f"TRANSCRIPT (Hebrew + English mixed, may contain page markers):\n"
        f"<<<\n{transcript}\n>>>\n"
        f"Extract the fields now.{extra}"
    )


def _try_parse(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _call(host: str, prompt: str, json_schema: dict[str, Any]) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": json_schema,  # full JSON schema (server-side enforcement)
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 2048},
    }
    resp = requests.post(
        f"{host.rstrip('/')}/api/generate", json=payload, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def extract_fields(
    transcript: str, schema: Schema, host: str
) -> tuple[dict[str, Any], str | None]:
    """Extract a row dict from a transcript.

    Returns (row, error). `row` always has every schema field populated with
    a default value if the model omitted it. `error` is None on success, or a
    short string describing why extraction was incomplete.
    """
    log.info(
        "Structured extraction: model=%s, transcript_chars=%d", MODEL, len(transcript)
    )

    json_schema = schema.json_schema()
    raw = _call(host, _build_prompt(schema, transcript, strict=False), json_schema)
    parsed = _try_parse(raw)
    err: str | None = None

    if parsed is None:
        log.warning("First extraction returned non-JSON; retrying with stricter prompt")
        raw = _call(
            host, _build_prompt(schema, transcript, strict=True), json_schema
        )
        parsed = _try_parse(raw)

    if parsed is None:
        err = f"Could not parse JSON from model. Raw response (first 500 chars): {raw[:500]}"
        log.error(err)
        parsed = {}

    base = schema.empty_row()
    for k, v in parsed.items():
        if k in base:
            base[k] = v
    return base, err
