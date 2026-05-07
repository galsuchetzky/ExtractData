"""CLI entry point for the local medical-document extractor.

Usage:
    python extract.py <input_folder> <schema.yaml> <out.xlsx>

All inference runs against a local Ollama instance. No network calls leave
the machine.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

import pipeline

REQUIRED_MODELS = ("gemma4:latest",)  # vision is now Tesseract (no Ollama model)


def _preflight(host: str) -> None:
    try:
        resp = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(
            f"ERROR: Ollama not reachable at {host} ({exc}).\n"
            "  macOS:   `ollama serve` (or open the menubar app).\n"
            "  Windows: launch the Ollama app from the Start menu.\n"
        )
    names = {m.get("name") for m in resp.json().get("models", [])}
    missing = [m for m in REQUIRED_MODELS if m not in names]
    if missing:
        hint = "\n".join(f"  ollama pull {m}" for m in missing)
        sys.exit(f"ERROR: Missing Ollama models: {', '.join(missing)}\n{hint}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract structured fields from a folder of medical-document "
            "screenshots into a fresh .xlsx file. Fully local (Ollama)."
        )
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Folder containing all images for ONE client/document",
    )
    parser.add_argument("schema", type=Path, help="Path to schema.yaml")
    parser.add_argument(
        "out_xlsx", type=Path, help="Output .xlsx path (will be overwritten)"
    )
    parser.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama base URL (default: %(default)s)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging"
    )
    parser.add_argument(
        "--save-text",
        type=Path,
        default=None,
        help="Also write the concatenated raw transcript to this path",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _preflight(args.ollama_host)

    pipeline.run(
        input_folder=args.input_folder,
        schema_path=args.schema,
        out_xlsx=args.out_xlsx,
        ollama_host=args.ollama_host,
        save_text=args.save_text,
    )


if __name__ == "__main__":
    main()
