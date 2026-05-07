"""qwen2.5vl:7b vision wrapper via Ollama HTTP.

Images are downscaled before being sent to the model. Raw screenshots can be
2000+ px wide and would otherwise make per-image inference take many minutes.
A long edge of ~1100 px keeps Hebrew + English readable while cutting wall
time roughly 10×.
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import requests
from PIL import Image

log = logging.getLogger(__name__)

MODEL = "qwen2.5vl:7b"
TIMEOUT = 600
MAX_LONG_EDGE = 1400  # px; downscale anything bigger before sending

PROMPT = (
    "You are a faithful OCR transcriber for a medical document.\n"
    "Transcribe ALL visible text in this image exactly as it appears.\n"
    "Rules:\n"
    "- Preserve Hebrew (RTL) and English text as written.\n"
    "- Preserve numbers, dates, and IDs character-for-character.\n"
    "- Preserve layout: keep line breaks between paragraphs, lists, table rows.\n"
    "- Do NOT translate. Do NOT summarize. Do NOT add commentary or labels.\n"
    "- Output ONLY the transcribed text. No headers like 'Transcription:'.\n"
)


def _prepare_image_b64(path: Path) -> str:
    """Open, downscale if needed, and base64-encode an image as PNG."""
    with Image.open(path) as im:
        im.load()
        long_edge = max(im.size)
        if long_edge > MAX_LONG_EDGE:
            scale = MAX_LONG_EDGE / long_edge
            new_size = (int(im.size[0] * scale), int(im.size[1] * scale))
            log.debug("  downscaling %s: %s -> %s", path.name, im.size, new_size)
            im = im.resize(new_size, Image.LANCZOS)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def transcribe_image(path: Path, host: str) -> str:
    img_b64 = _prepare_image_b64(path)
    payload = {
        "model": MODEL,
        "prompt": PROMPT,
        "images": [img_b64],
        "stream": False,
        "keep_alive": "10m",  # avoid disk reload between pages / between test fixtures
        "options": {"temperature": 0, "num_predict": 1024},
    }
    log.debug("Vision call: %s (%d b64 chars)", path.name, len(img_b64))
    resp = requests.post(
        f"{host.rstrip('/')}/api/generate", json=payload, timeout=TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("response") or "").strip()
    if not text:
        raise RuntimeError(f"Empty response from {MODEL} for {path.name}")
    log.debug("  -> %d chars", len(text))
    return text
