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

from bidi.algorithm import get_display
import re

log = logging.getLogger(__name__)

TESSERACT_BIN = "tesseract"
TIMEOUT = 120
PASSES = ("heb", "eng")  # two passes: each is clean for its own script


def _ensure_tesseract() -> None:
    global TESSERACT_BIN
    if shutil.which(TESSERACT_BIN) is not None:
        return

    # Windows registry fallback
    import platform
    if platform.system() == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Tesseract-OCR"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                if install_dir:
                    candidate = Path(install_dir) / "tesseract.exe"
                    if candidate.exists():
                        TESSERACT_BIN = str(candidate)
                        return
        except (ImportError, OSError):
            pass

    raise RuntimeError(
        "tesseract not found on PATH or in registry. \n"
        "Windows: Install from https://github.com/UB-Mannheim/tesseract/wiki\n"
        "macOS:   `brew install tesseract tesseract-lang` \n"
        "Linux:   `apt install tesseract-ocr tesseract-ocr-heb`"
    )


def _run(path: Path, lang: str) -> str:
    """Run Tesseract, writing output to a temp file to avoid Windows console pipe encoding corruption."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        out_base = os.path.join(tmpdir, "out")
        result = subprocess.run(
            [TESSERACT_BIN, str(path), out_base, "-l", lang],
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"tesseract failed for {path.name} (lang={lang}, "
                f"rc={result.returncode}): {err[:300]}"
            )
        out_txt = out_base + ".txt"
        if not os.path.exists(out_txt):
            raise RuntimeError(f"Tesseract produced no output file for {path.name}")
        with open(out_txt, encoding="utf-8") as f:
            return f.read().strip()


def _maybe_fix_bidi(text: str) -> str:
    """Heuristic: if Hebrew final letters appear mostly at word starts, it's reversed.
    
    This handles documents that are stored in 'Visual Hebrew' (already reversed in the image)
    vs 'Logical Hebrew' (characters in correct order in the image).
    """
    if not text:
        return text

    # Final letters: ם, ן, ץ, ף, ך
    final_letters = {'ם', 'ן', 'ץ', 'ף', 'ך'}
    starts = 0
    ends = 0

    # Extract all Hebrew sequences
    words = re.findall(r'[א-ת]+', text)
    for word in words:
        if not word:
            continue
        if word[0] in final_letters:
            starts += 1
        if word[-1] in final_letters:
            ends += 1

    # If we see final letters at starts but not at ends, it's a strong signal of reversal
    if starts > ends:
        log.debug("Detected reversed Hebrew (starts=%d, ends=%d), applying BiDi correction", starts, ends)
        return get_display(text)

    return text


def transcribe_image(path: Path, host: str) -> str:  # `host` kept for compat
    """Run Tesseract with mixed Hebrew+English support.

    Running separate passes often confuses the downstream LLM because the
    English pass produces gibberish shapes for Hebrew letters. A single
    combined pass allows Tesseract 5's LSTM to handle mixed scripts better.
    """
    _ensure_tesseract()
    log.debug("Tesseract OCR: %s (%d bytes)", path.name, path.stat().st_size)
    raw_text = _run(path, "heb+eng")
    return _maybe_fix_bidi(raw_text)
