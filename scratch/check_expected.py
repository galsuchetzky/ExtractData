from pathlib import Path
p = Path("tests/fixtures/case_01_pigeons_yes/expected.yaml")
raw = p.read_bytes()
print(f"Total length: {len(raw)}")
print(f"Hex start: {raw[:100].hex(' ')}")
try:
    txt = raw.decode("utf-8")
    print("\nUTF-8 DECODE SUCCESS")
    print(txt[:200])
except Exception as e:
    print(f"\nUTF-8 DECODE FAILED: {e}")
