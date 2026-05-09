import sys
from pathlib import Path
import shutil
import subprocess

TESSERACT_BIN = "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

def _run_raw(path: Path, lang: str) -> bytes:
    result = subprocess.run(
        [TESSERACT_BIN, str(path), "-", "-l", lang],
        capture_output=True,
        check=False,
    )
    return result.stdout

img = Path("tests/fixtures/case_01_pigeons_yes/page_1.png")
raw_heb = _run_raw(img, "heb")

print("--- RAW HEBREW BYTES (first 100) ---")
print(raw_heb[:100])

for enc in ["utf-8", "cp1255", "cp862"]:
    try:
        decoded = raw_heb.decode(enc)
        print(f"\n--- DECODED WITH {enc} ---")
        print(decoded[:500])
    except Exception as exc:
        print(f"\n--- FAILED {enc}: {exc} ---")
