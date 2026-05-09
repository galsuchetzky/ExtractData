# Agent Instructions - Project Organization

To maintain a clean and predictable project structure, all AI agents working on this codebase must follow these organization rules:

## 1. Directory Structure

- **Tests**: All test files (unit tests, integration tests, fixtures) MUST be located in the `tests/` directory.
- **Output**: All generated output files (Excel reports, temporary results) MUST be saved in the `out/` directory.
- **Schemas**: All data extraction schemas MUST be stored in the `schemas/` directory (e.g., `schemas/schema.yaml`, `schemas/testing_schema.yaml`).
- **Samples**: Sample input images for demonstration should be in the `samples/` directory.
- **Scratch**: Temporary scripts or research notes should be in the `scratch/` directory. This directory is ignored by git.

## 2. CLI Defaults

- When implementing new features or running tests, ensure that output defaults to the `out/` directory whenever possible.
- Avoid creating temporary files in the project root.

## 3. OCR and BiDi

- Use `bidi.algorithm.get_display()` for any Hebrew/English mixed text extracted via OCR to ensure correct logical orientation.
- Prefer Tesseract OCR for speed and local execution as defined in `vision.py`.
