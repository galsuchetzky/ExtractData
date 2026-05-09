"""Orchestrate the vision -> concat -> structured-extraction -> xlsx pipeline."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from natsort import natsorted

import excel_writer
import extract_struct
import vision
from schema import load_schema

log = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _list_images(folder: Path) -> list[Path]:
    images = [
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    return natsorted(images, key=lambda p: p.name)


def run(
    input_folder: Path,
    schema_path: Path | None,
    out_xlsx: Path | None,
    ollama_host: str,
    ollama_model: str,
    save_text: Path | None = None,
    vision_only: bool = False,
) -> None:
    if not input_folder.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    if not vision_only:
        if schema_path is None:
            raise ValueError("schema_path is required when not in vision-only mode")
        if out_xlsx is None:
            raise ValueError("out_xlsx is required when not in vision-only mode")
        schema = load_schema(schema_path)
        log.info("Schema loaded: %d fields from %s", len(schema.fields), schema_path)

    images = _list_images(input_folder)
    if not images:
        raise FileNotFoundError(
            f"No images in {input_folder} (looked for {sorted(IMAGE_EXTS)})"
        )
    log.info("Found %d images in %s", len(images), input_folder)

    overall_start = time.monotonic()
    transcripts: list[str] = []
    vision_total = 0.0
    for idx, img in enumerate(images, start=1):
        log.info("[%d/%d] Transcribing %s", idx, len(images), img.name)
        t0 = time.monotonic()
        try:
            text = vision.transcribe_image(img, ollama_host)
        except Exception as exc:  # noqa: BLE001
            log.error("  vision FAILED for %s: %s", img.name, exc)
            text = f"[page {idx}: extraction failed: {exc}]"
        dt = time.monotonic() - t0
        vision_total += dt
        log.info("  vision done in %.1fs (%d chars)", dt, len(text))
        transcripts.append(f"--- page {idx} ({img.name}) ---\n{text}")

    full_text = "\n\n".join(transcripts)
    log.info(
        "Concatenated transcript: %d chars across %d pages (vision total %.1fs)",
        len(full_text),
        len(images),
        vision_total,
    )

    if save_text is not None:
        save_text.parent.mkdir(parents=True, exist_ok=True)
        save_text.write_text(full_text, encoding="utf-8")
        log.info("Saved concatenated transcript to %s", save_text)

    if vision_only:
        log.info("Vision-only mode: skipping structured extraction.")
        return

    t0 = time.monotonic()
    row, err = extract_struct.extract_fields(full_text, schema, ollama_host, ollama_model)
    struct_dt = time.monotonic() - t0
    log.info("Structured extraction done in %.1fs", struct_dt)

    excel_writer.write_workbook(row, schema, out_xlsx, error=err)
    total = time.monotonic() - overall_start
    log.info(
        "Wrote %s | TOTAL %.1fs (vision %.1fs + structured %.1fs)",
        out_xlsx,
        total,
        vision_total,
        struct_dt,
    )
    if err:
        log.warning("Extraction completed with errors; see _errors column in the xlsx")
