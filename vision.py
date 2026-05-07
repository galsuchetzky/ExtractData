"""Vision step using Tesseract OCR (heb+eng) instead of qwen2.5vl.

Why Tesseract: typed EHR-style screenshots are exactly Tesseract's wheelhouse,
and on the fixtures we have it both runs ~70× faster (~0.4s per page vs
~25-30s) and produces noticeably more accurate Hebrew (e.g.
"מכבי שירותי" instead of qwen2.5vl's "מכלבי שיווטי", "שיעול" instead of
"שיגול"). The cost is weaker layout reconstruction: when Hebrew and English
share a line, Tesseract sometimes joins or reorders the segments via the
BiDi algorithm. The downstream gemma4:latest extractor tolerates this well.

Public surface (`transcribe_image(path, host) -> str`) is identical to the
qwen-based wrapper so the rest of the pipeline doesn't need to change. The
`host` argument is unused (kept for signature compatibility) — Tesseract
runs locally as a subprocess.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

TESSERACT_BIN = "tesseract"
TIMEOUT = 120
PASSES = ("heb", "eng")  # two passes: each is clean for its own script


def _ensure_tesseract() -> None:
    if shutil.which(TESSERACT_BIN) is None:
        raise RuntimeError(
            "tesseract not found on PATH. Install with `brew install tesseract "
            "tesseract-lang` (macOS) or `apt install tesseract-ocr "
            "tesseract-ocr-heb` (Debian/Ubuntu)."
        )


def _run(path: Path, lang: str) -> str:
    result = subprocess.run(
        [TESSERACT_BIN, str(path), "-", "-l", lang],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"tesseract failed for {path.name} (lang={lang}, "
            f"rc={result.returncode}): {result.stderr.strip()[:300]}"
        )
    return result.stdout.strip()


def transcribe_image(path: Path, host: str) -> str:  # `host` kept for compat
    """Run Tesseract twice (Hebrew-only, then English-only) and concatenate.

    A single `-l heb+eng` pass produces BiDi-mushed output on lines that mix
    scripts (numbers, English labels, and Hebrew labels jumbled together with
    invisible direction marks). Two separate passes give us, for each line:
    a clean Hebrew rendering with English garbled, and a clean English
    rendering with Hebrew garbled. The downstream LLM picks whichever is
    correct per field. Total cost: ~0.4-0.8s per page (still ≪1s).
    """
    _ensure_tesseract()
    log.debug("Tesseract OCR: %s (%d bytes)", path.name, path.stat().st_size)
    chunks: list[str] = []
    for lang in PASSES:
        text = _run(path, lang)
        if text:
            chunks.append(f"--- pass: {lang} ---\n{text}")
    if not chunks:
        raise RuntimeError(f"Empty Tesseract output for {path.name}")
    out = "\n\n".join(chunks)
    log.debug("  -> %d chars across %d passes", len(out), len(chunks))
    return out
