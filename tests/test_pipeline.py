"""Integration tests for the full pipeline.

Three test types per fixture, all sharing one pipeline run via the
`pipeline_outputs` session fixture (defined in conftest.py):

1. test_pipeline_matchers
   - Old-style assertions: equals/contains/list_min_length/list_contains_any
     applied to the extracted row. Fast and deterministic.

2. test_pipeline_judge_transcript
   - Uses gemma4:26b as a judge to verify which ground-truth fields are
     present in the OCR transcript. Tells you whether vision did its job.

3. test_pipeline_judge_extraction
   - Uses gemma4:26b as a judge to score each extracted field against the
     ground truth with semantic equivalence (Hebrew/English bilingual).
     Tells you whether the structured-extraction step did its job.

All three are skipped automatically if Ollama isn't reachable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from schema import LIST_TYPES, load_schema
from tests.judge import judge_extraction, judge_transcript

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _list_fixture_names() -> list[str]:
    if not FIXTURES.is_dir():
        return []
    return sorted(
        p.name
        for p in FIXTURES.iterdir()
        if p.is_dir() and (p / "expected.yaml").is_file() and any(p.glob("page_*.png"))
    )


_FIXTURE_NAMES = _list_fixture_names()


def _flatten(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------- 1) matchers

def _check_field(actual: Any, rules: dict[str, Any], schema_type: str) -> list[str]:
    failures: list[str] = []
    flat = _flatten(actual)
    actual_lower = flat.lower()

    if "equals" in rules:
        expected = str(rules["equals"]).lower()
        if expected != actual_lower:
            failures.append(f"equals: want {rules['equals']!r}, got {actual!r}")

    if "is_one_of" in rules:
        allowed = [str(v).lower() for v in rules["is_one_of"]]
        if actual_lower not in allowed:
            failures.append(f"is_one_of: {actual!r} not in {rules['is_one_of']!r}")

    if "contains" in rules:
        opts = rules["contains"]
        if isinstance(opts, str):
            opts = [opts]
        if not any(opt.lower() in actual_lower for opt in opts):
            failures.append(f"contains: none of {opts!r} in {flat!r}")

    if "list_min_length" in rules:
        if schema_type not in LIST_TYPES:
            failures.append("list_min_length used on non-list field")
        else:
            if isinstance(actual, list):
                length = len(actual)
            else:
                try:
                    parsed = json.loads(flat) if flat else []
                except json.JSONDecodeError:
                    parsed = []
                length = len(parsed) if isinstance(parsed, list) else 0
            if length < int(rules["list_min_length"]):
                failures.append(
                    f"list_min_length: have {length}, want >={rules['list_min_length']}"
                )

    if "list_contains_any" in rules:
        opts = rules["list_contains_any"]
        if not any(opt.lower() in actual_lower for opt in opts):
            failures.append(f"list_contains_any: none of {opts!r} in {flat!r}")

    return failures


@pytest.mark.integration
@pytest.mark.skipif(not _FIXTURE_NAMES, reason="No fixtures generated. Run tests/generate_fixtures.py first.")
@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_pipeline_matchers(fixture_name: str, pipeline_outputs):
    schema = load_schema(ROOT / "schema.yaml")
    bundle = pipeline_outputs[fixture_name]
    actual = bundle["row"]
    expected = bundle["expected"]
    type_by_name = {f.name: f.type for f in schema.fields}

    failures: list[str] = []
    for field, rules in (expected.get("fields") or {}).items():
        if field not in type_by_name:
            failures.append(f"{field}: not in schema")
            continue
        f = _check_field(actual.get(field), rules, type_by_name[field])
        if f:
            failures.append(f"  {field}: {'; '.join(f)}")

    if failures:
        pytest.fail(
            f"{fixture_name} matcher failures:\n"
            + "\n".join(failures)
            + "\n\nActual row:\n"
            + json.dumps(actual, ensure_ascii=False, indent=2)
            + "\n\nTranscript head:\n"
            + bundle["transcript"][:1500]
        )


# --------------------------------------------------- 2) judge: transcript step

# Vision quality: at least this fraction of ground-truth fields must be
# 'present' or 'partial' in the OCR transcript (na fields are ignored).
TRANSCRIPT_MIN_PRESENT_FRACTION = 0.6


@pytest.mark.integration
@pytest.mark.skipif(not _FIXTURE_NAMES, reason="No fixtures generated.")
@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_pipeline_judge_transcript(
    fixture_name: str, pipeline_outputs, ollama_host: str
):
    bundle = pipeline_outputs[fixture_name]
    gt = bundle["expected"].get("ground_truth") or {}
    transcript = bundle["transcript"]
    if not gt:
        pytest.skip(f"{fixture_name}: no ground_truth in expected.yaml")

    verdict = judge_transcript(gt, transcript, ollama_host)
    summary = verdict["summary"]
    fields = verdict["fields"]

    judged = sum(
        1 for v in fields.values() if v["verdict"] in ("present", "partial", "missing")
    )
    if judged == 0:
        pytest.fail(
            f"{fixture_name}: judge returned no usable verdicts.\n"
            f"Raw response: {verdict['raw'][:1500]}\n"
            f"Transcript head:\n{transcript[:1500]}"
        )

    present = summary.get("present", 0) + summary.get("partial", 0)
    fraction = present / judged if judged else 0.0
    if fraction < TRANSCRIPT_MIN_PRESENT_FRACTION:
        details = "\n".join(
            f"  {k}: {v['verdict']} - {v['reasoning']}" for k, v in fields.items()
        )
        pytest.fail(
            f"{fixture_name} OCR transcript coverage too low: "
            f"{present}/{judged} = {fraction:.0%} "
            f"(threshold {TRANSCRIPT_MIN_PRESENT_FRACTION:.0%}).\n"
            f"Per-field verdicts:\n{details}\n\n"
            f"Transcript head:\n{transcript[:1500]}"
        )


# -------------------------------------------------- 3) judge: extraction step

# Structured-extraction quality: at most this fraction of fields with a
# ground-truth value may be 'incorrect' or 'missing'.
EXTRACTION_MAX_BAD_FRACTION = 0.30


@pytest.mark.integration
@pytest.mark.skipif(not _FIXTURE_NAMES, reason="No fixtures generated.")
@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_pipeline_judge_extraction(
    fixture_name: str, pipeline_outputs, ollama_host: str
):
    bundle = pipeline_outputs[fixture_name]
    gt = bundle["expected"].get("ground_truth") or {}
    extracted = bundle["row"]
    if not gt:
        pytest.skip(f"{fixture_name}: no ground_truth in expected.yaml")

    verdict = judge_extraction(gt, extracted, ollama_host)
    summary = verdict["summary"]
    fields = verdict["fields"]

    judged = summary["total"] - summary.get("na", 0)
    if judged == 0:
        pytest.fail(
            f"{fixture_name}: judge returned no usable verdicts.\n"
            f"Raw response: {verdict['raw'][:1500]}"
        )
    bad = summary.get("incorrect", 0) + summary.get("missing", 0)
    bad_fraction = bad / judged if judged else 0.0
    if bad_fraction > EXTRACTION_MAX_BAD_FRACTION:
        details = "\n".join(
            f"  {k}: {v['verdict']} - {v['reasoning']}" for k, v in fields.items()
        )
        pytest.fail(
            f"{fixture_name} extraction quality too low: "
            f"{bad}/{judged} bad fields = {bad_fraction:.0%} "
            f"(threshold {EXTRACTION_MAX_BAD_FRACTION:.0%}).\n"
            f"Per-field verdicts:\n{details}\n\n"
            f"Extracted row:\n{json.dumps(extracted, ensure_ascii=False, indent=2)}"
        )
