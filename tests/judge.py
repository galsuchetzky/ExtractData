"""LLM-as-judge using gemma4:26b to score the pipeline's outputs.

Two evaluators:

- judge_extraction(ground_truth, extracted, host)
    Compares each extracted field to its ground-truth value with semantic
    equivalence (English/Hebrew bilingual, minor transcription errors OK).
    Returns per-field verdicts plus a summary count.

- judge_transcript(ground_truth, transcript, host)
    For each ground-truth field, asks whether the value (or a clear equivalent)
    is present in the OCR transcript. Tells us whether failures originated in
    the vision step or in the structured extraction step.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

JUDGE_MODEL = "gemma4:latest"
TIMEOUT = 600

EXTRACTION_VERDICTS = {"correct", "partial", "incorrect", "missing", "na"}
TRANSCRIPT_VERDICTS = {"present", "partial", "missing", "na"}


# --------------------------------------------------------------------- helpers

def _build_judge_schema(
    field_names: list[str], allowed_verdicts: list[str]
) -> dict[str, Any]:
    """Per-field {verdict, reasoning} object, one entry per ground-truth key.

    Without this, gemma4:* in plain JSON mode loops (emits the same field
    repeatedly with corrupted reasoning strings). A strict schema is what
    forces the model to terminate cleanly.
    """
    field_obj = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(allowed_verdicts)},
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "reasoning"],
    }
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": {n: field_obj for n in field_names},
                "required": list(field_names),
            }
        },
        "required": ["fields"],
    }


def _post(
    host: str,
    prompt: str,
    json_schema: dict[str, Any],
    num_ctx: int = 16384,
) -> str:
    payload = {
        "model": JUDGE_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": json_schema,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": 2048},
    }
    resp = requests.post(
        f"{host.rstrip('/')}/api/generate", json=payload, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def _parse_json(raw: str) -> dict | None:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _coerce_field_dict(
    raw: dict | None, allowed_verdicts: set[str], expected_keys: list[str]
) -> dict[str, dict[str, str]]:
    """Pick a fields-dict out of the model's response and keep only known keys."""
    out: dict[str, dict[str, str]] = {}
    if raw is None:
        return out
    fields = raw.get("fields") if "fields" in raw else raw
    if not isinstance(fields, dict):
        return out
    for k in expected_keys:
        v = fields.get(k)
        if isinstance(v, dict):
            verdict = str(v.get("verdict", "")).lower().strip()
            reasoning = str(v.get("reasoning", "")).strip()
        elif isinstance(v, str):
            verdict, reasoning = v.lower().strip(), ""
        else:
            continue
        if verdict in allowed_verdicts:
            out[k] = {"verdict": verdict, "reasoning": reasoning}
    return out


def _summarize(
    verdicts: dict[str, dict[str, str]], allowed: set[str]
) -> dict[str, int]:
    summary = {v: 0 for v in allowed}
    summary["total"] = 0
    for v in verdicts.values():
        summary[v["verdict"]] = summary.get(v["verdict"], 0) + 1
        summary["total"] += 1
    return summary


# ------------------------------------------------------------------- extraction

EXTRACTION_RULES = """\
You are a strict evaluator scoring a medical-record information-extraction system.
For each field, compare the EXTRACTED value to the GROUND TRUTH value and return
a verdict from this exact set:

- correct  : same meaning. Different wording or language is fine.
- partial  : captures the gist but is incomplete or has minor errors.
- incorrect: wrong information, hallucinated content, or wrong patient.
- missing  : extracted is null/empty/[] but ground truth has a value.
- na       : ground truth is null/empty/[] (no value to check).

Bilingual rules:
- "Hypersensitivity Pneumonitis" == "דלקת ריאות בהיפר-רגישות" == "HP" == correct.
- Date "07/05/2026" == "7/5/2026" == "May 7 2026" == correct.
- Hebrew/English translation of the same medical term is correct, not partial.

Be strict on:
- Wrong patient ID, wrong patient name, wrong DOB → incorrect, never partial.
- Enum values (yes/no/unknown) → must match in meaning. \
yes != no, no != unknown.
- Hallucinations (extracted lists items not in ground truth) → incorrect.
"""


def judge_extraction(
    ground_truth: dict[str, Any],
    extracted: dict[str, Any],
    host: str,
) -> dict[str, Any]:
    """Score each extracted field against the ground truth.

    Returns:
        {
            "fields": {field_name: {"verdict": str, "reasoning": str}, ...},
            "summary": {"correct": N, "partial": N, "incorrect": N,
                        "missing": N, "na": N, "total": N},
            "raw": <model response string for debugging>,
        }
    """
    keys = sorted(set(ground_truth) | set(extracted))
    schema = _build_judge_schema(keys, sorted(EXTRACTION_VERDICTS))
    prompt = (
        EXTRACTION_RULES
        + "\nFor every key listed below, output a verdict and a short reasoning.\n\n"
        f"GROUND TRUTH:\n{json.dumps(ground_truth, ensure_ascii=False, indent=2)}\n\n"
        f"EXTRACTED:\n{json.dumps(extracted, ensure_ascii=False, indent=2)}\n\n"
        f"FIELDS TO EVALUATE (one verdict per name): {', '.join(keys)}\n"
    )
    raw = _post(host, prompt, schema)
    parsed = _parse_json(raw)
    fields = _coerce_field_dict(parsed, EXTRACTION_VERDICTS, keys)
    return {
        "fields": fields,
        "summary": _summarize(fields, EXTRACTION_VERDICTS),
        "raw": raw,
    }


# ------------------------------------------------------------------- transcript

TRANSCRIPT_RULES = """\
You are evaluating an OCR transcript of a medical document image.
For each ground-truth field, decide if the value (or a clear equivalent) appears
in the transcript text. Return a verdict from this exact set:

- present : the value appears verbatim or as a clear semantic equivalent
            (Hebrew/English translation counts as present).
- partial : a recognizable fragment appears but the full value does not.
- missing : the value is not detectable in the transcript at all.
- na      : ground truth has no value for this field (skip).

Be strict: a single matching keyword is not enough for "present" if most of the
ground-truth phrase is absent.
"""


def judge_transcript(
    ground_truth: dict[str, Any],
    transcript: str,
    host: str,
) -> dict[str, Any]:
    """Per-field 'is this value present in the OCR transcript?' verdicts."""
    keys = sorted(ground_truth)
    schema = _build_judge_schema(keys, sorted(TRANSCRIPT_VERDICTS))
    prompt = (
        TRANSCRIPT_RULES
        + "\nFor every key listed below, output a verdict and short reasoning.\n\n"
        f"GROUND TRUTH:\n{json.dumps(ground_truth, ensure_ascii=False, indent=2)}\n\n"
        "TRANSCRIPT:\n<<<\n"
        + transcript
        + "\n>>>\n\n"
        f"FIELDS TO EVALUATE: {', '.join(keys)}\n"
    )
    raw = _post(host, prompt, schema)
    parsed = _parse_json(raw)
    fields = _coerce_field_dict(parsed, TRANSCRIPT_VERDICTS, keys)
    return {
        "fields": fields,
        "summary": _summarize(fields, TRANSCRIPT_VERDICTS),
        "raw": raw,
    }
