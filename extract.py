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
from typing import Any

import requests
import yaml

import pipeline

# Defaults that can be overridden by config.yaml or CLI args
DEFAULT_MODEL = "gemma4:latest"
DEFAULT_HOST = "http://localhost:11434"

def load_config() -> dict[str, Any]:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"Warning: Failed to load config.yaml: {exc}")
        return {}

def get_config_val(config: dict[str, Any], key: str, default: Any) -> Any:
    # Check environment variable first (e.g. OLLAMA_MODEL)
    env_val = os.environ.get(f"OLLAMA_{key.upper()}")
    if env_val:
        val = env_val
    else:
        # Then check config file
        val = config.get("ollama", {}).get(key, default)
    
    # Special handling for 'host': ensure scheme and port, and fix 0.0.0.0 on Windows
    if key == "host" and val:
        if val == "0.0.0.0":
            val = "localhost"
        if not val.startswith("http"):
            val = f"http://{val}"
        if ":" not in val.split("//")[-1]:
            val = f"{val}:11434"
        # Final safety check for 0.0.0.0 inside a URL
        val = val.replace("//0.0.0.0", "//localhost")
    return val


def _preflight(host: str, model: str) -> None:
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
    if model not in names:
        sys.exit(f"ERROR: Missing Ollama model: {model}\n  Run: ollama pull {model}")


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
    parser.add_argument("schema", type=Path, nargs="?", help="Path to schema.yaml")
    parser.add_argument(
        "out_xlsx",
        type=Path,
        nargs="?",
        help="Output .xlsx path (will be overwritten)",
    )
    config = load_config()

    parser.add_argument(
        "--ollama-host",
        default=get_config_val(config, "host", DEFAULT_HOST),
        help="Ollama base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--ollama-model",
        default=get_config_val(config, "model", DEFAULT_MODEL),
        help="Ollama model name (default: %(default)s)",
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
    parser.add_argument(
        "--vision-only",
        action="store_true",
        help="Only run the vision (OCR) step and skip LLM extraction",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.vision_only:
        if not args.save_text:
             args.save_text = Path("transcript.txt")
             print(f"Vision-only mode: output will be saved to {args.save_text}")
    else:
        if not args.schema or not args.out_xlsx:
            parser.error("schema and out_xlsx are required unless --vision-only is used")
        _preflight(args.ollama_host, args.ollama_model)

    pipeline.run(
        input_folder=args.input_folder,
        schema_path=args.schema,
        out_xlsx=args.out_xlsx,
        ollama_host=args.ollama_host,
        ollama_model=args.ollama_model,
        save_text=args.save_text,
        vision_only=args.vision_only,
    )


if __name__ == "__main__":
    main()
