"""Scaling stress test for gemma per-field structured extraction.

Skips OCR entirely — synthesises a transcript at a target size with the real
medical-note fields ("the needle") embedded at a configurable position inside
realistic-looking medical filler text ("the haystack"). Then runs the real
extract_struct.extract_fields() against gemma4:latest and scores accuracy.

Goals:
  1. Find the transcript size at which the current architecture starts losing
     fields (silently, via context truncation, or model fatigue).
  2. Compare needle position (start / middle / end) to detect the
     "lost in the middle" effect.

Run:
  .venv/bin/python tests/scale_test.py             # default sizes [5,10,20,40] KB
  .venv/bin/python tests/scale_test.py 5 10 20 40 80 160
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import extract_struct  # noqa: E402
from schema import load_schema  # noqa: E402

HOST = "http://localhost:11434"

# Test data lives in tests/scale_data/ so it can be edited independently of
# the test code. Edit needle.txt to change which medical record we look for;
# edit filler.txt to change the surrounding noise.
DATA_DIR = Path(__file__).resolve().parent / "scale_data"
NEEDLE = (DATA_DIR / "needle.txt").read_text(encoding="utf-8")


# --- Field-level expectations (loose, tolerant of model wording) -----------

EXPECTED = {
    "patient_name":       ("contains", "ישראל"),
    "patient_id":         ("equals", "311234567"),
    "date_of_birth":      ("contains", "15/03/1962"),
    "visit_date":         ("contains", "07/05/2026"),
    "doctor_name":        ("contains", "רותם"),
    "clinic":             ("contains", "מכבי"),
    "chief_complaint":    ("contains", "שיעול"),
    "diagnosis":          ("list_substr", "neumonitis"),  # case-tolerant
    "symptoms":           ("list_min", 1),
    "medications":        ("list_min", 1),
    "procedures":         ("list_min", 1),
    "referrals":          ("list_substr", "ריאות"),
    "exposed_to_pigeons": ("equals", "yes"),
}


# Filler "haystack": unrelated medical paragraphs we can repeat to reach
# the target transcript size. Same reasoning as NEEDLE — edit the .txt file
# to tune the noise content without touching the test code.
FILLER_BLOCK = (DATA_DIR / "filler.txt").read_text(encoding="utf-8")


def build_transcript(target_bytes: int, needle_pos: float = 0.5) -> str:
    """Build a transcript of ~target_bytes with NEEDLE embedded at needle_pos."""
    needle_b = len(NEEDLE.encode("utf-8"))
    if target_bytes <= needle_b:
        return NEEDLE
    filler_needed = target_bytes - needle_b
    pre_b = int(filler_needed * needle_pos)
    post_b = filler_needed - pre_b
    block_b = len(FILLER_BLOCK.encode("utf-8"))

    def chunk(n: int) -> str:
        if n <= 0:
            return ""
        repeats = n // block_b + 1
        text = "\n\n".join(FILLER_BLOCK for _ in range(repeats))
        # Truncate by characters (approx; bytes ≈ chars for the Latin parts).
        return text[: max(0, n)]

    return f"{chunk(pre_b)}\n\n--- patient record ---\n\n{NEEDLE}\n\n--- end of record ---\n\n{chunk(post_b)}"


# --- Scoring ---------------------------------------------------------------

def _flat(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def score_row(row: dict) -> tuple[int, int, list[str]]:
    correct = 0
    total = 0
    failures: list[str] = []
    for field, (op, expected) in EXPECTED.items():
        total += 1
        actual = row.get(field)
        flat = _flat(actual).lower()
        ok = False
        if op == "equals":
            ok = str(expected).lower() == flat
        elif op == "contains":
            ok = str(expected).lower() in flat
        elif op == "list_min":
            ok = isinstance(actual, list) and len(actual) >= int(expected)
        elif op == "list_substr":
            ok = isinstance(actual, list) and any(
                str(expected).lower() in str(x).lower() for x in actual
            )
        if ok:
            correct += 1
        else:
            preview = (flat[:60] + "...") if len(flat) > 60 else flat
            failures.append(f"{field}: want {op}={expected!r}, got {preview!r}")
    return correct, total, failures


# --- Runner ----------------------------------------------------------------

def run_size(target_kb: int, needle_pos: float, schema) -> dict:
    transcript = build_transcript(target_kb * 1024, needle_pos)
    actual_chars = len(transcript)
    label = f"{target_kb}KB pos={needle_pos}"
    print(f"\n=== {label} (actual {actual_chars:,} chars) ===")
    t0 = time.monotonic()
    try:
        row, err = extract_struct.extract_fields(transcript, schema, HOST)
    except Exception as exc:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"  EXCEPTION after {dt:.1f}s: {exc}")
        return {"label": label, "kb": target_kb, "pos": needle_pos,
                "chars": actual_chars, "time": dt, "correct": 0, "total": len(EXPECTED),
                "exception": str(exc)}
    dt = time.monotonic() - t0
    correct, total, failures = score_row(row)
    print(f"  {correct}/{total} fields OK in {dt:.1f}s")
    for f in failures:
        print(f"    ✗ {f}")
    if err:
        print(f"  pipeline err: {err[:200]}")
    return {"label": label, "kb": target_kb, "pos": needle_pos,
            "chars": actual_chars, "time": dt, "correct": correct,
            "total": total, "row": row, "failures": failures}


def main(argv: list[str]) -> None:
    sizes = [int(s) for s in argv] if argv else [5, 10, 20, 40]
    schema = load_schema(ROOT / "schema.yaml")
    print(f"sizes (KB): {sizes}")
    print(f"transcript needle position: 0.5 (middle)")
    results = [run_size(kb, 0.5, schema) for kb in sizes]
    print("\n=== SUMMARY ===")
    print(f"{'label':<20}{'chars':>10}{'time(s)':>10}{'correct':>12}")
    for r in results:
        print(f"{r['label']:<20}{r['chars']:>10,}{r['time']:>10.1f}"
              f"{r['correct']}/{r['total']:>4}")


if __name__ == "__main__":
    main(sys.argv[1:])
