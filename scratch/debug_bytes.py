import subprocess
from pathlib import Path

TESSERACT_BIN = "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
img = Path("tests/fixtures/case_01_pigeons_yes/page_1.png")

result = subprocess.run(
    [TESSERACT_BIN, str(img), "-", "-l", "heb"],
    capture_output=True,
    check=False,
)
raw = result.stdout
print(f"Length: {len(raw)}")
print(f"Hex: {raw[:50].hex(' ')}")
try:
    print(f"UTF-8: {raw[:100].decode('utf-8')}")
except Exception as e:
    print(f"UTF-8 failed: {e}")
