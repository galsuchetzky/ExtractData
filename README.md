# ExtractData — Local Medical-Document Extractor

A small, **fully-local** Python pipeline that turns a folder of screenshots
of a single client's medical document into one structured row in a fresh
`.xlsx` file.

- **Image → text** with `qwen2.5vl:7b` via local Ollama (images downscaled to
  ≤1400 px on the long edge before transcription).
- **Text → structured JSON** with `gemma4:latest` (~9.6 GB) via local Ollama
  in **server-enforced JSON-schema mode**. The 26 B variant loops on
  schema-rich prompts; the smaller variant + a strict schema is both faster
  and more reliable.
- **JSON → .xlsx** with `openpyxl`. List/object fields are stored as JSON
  strings (`ensure_ascii=False`) in a single cell so Hebrew round-trips.
- **Privacy**: only HTTP target is `localhost:11434`. No telemetry. No cloud SDKs.

Designed to run identically on macOS (Apple Silicon) and Windows.

## Performance (M-series MacBook Pro, 32 GB)

End-to-end per input folder (vision + structured extraction):

| Fixture                     | Pages | Vision | Structured | **Total** |
|-----------------------------|-------|--------|------------|-----------|
| `case_01_pigeons_yes`       | 2     | 51.8 s | 11.7 s     | **63.5 s** |
| `case_02_pigeons_no`        | 1     | 28.1 s |  8.1 s     | **36.2 s** |
| `case_03_minimal`           | 1     | 26.1 s |  5.6 s     | **31.7 s** |

A per-folder budget of ≤5 minutes is held with a wide margin. First call after
Ollama starts is ~30 s slower because models load from disk; `keep_alive: 10m`
on every request keeps both pinned across consecutive folders.

---

## Prerequisites

1. **Python 3.10+**
2. **Ollama** running locally
   - macOS: `brew install ollama` then `ollama serve` (or open the menubar app)
   - Windows: install from <https://ollama.com/download/windows> and launch the app
3. **Models pulled**:
   ```
   ollama pull qwen2.5vl:7b
   ollama pull gemma4:latest
   # (optional, only used by the LLM-as-judge tests below — same model as
   #  extraction, so this is the only required pull beyond qwen2.5vl)
   ```

## Setup

### macOS / Linux
```bash
cd /Users/gal.suchetzky/Projects/ExtractData
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell)
```powershell
cd C:\path\to\ExtractData
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

```
python extract.py <input_folder> <schema.yaml> <out.xlsx> [--verbose] [--save-text path] [--ollama-host URL]
```

Example:

```bash
python extract.py samples/client_smoke schema.yaml out/smoke.xlsx --verbose --save-text out/smoke.txt
```

- `<input_folder>` must contain only images (`.png`, `.jpg`, `.jpeg`, `.webp`,
  `.bmp`) for **one** client / one document. Pages are sorted with `natsort`,
  so `page1, page2, ..., page10` are processed in human order.
- The output `.xlsx` is overwritten on each run.
- `--save-text` dumps the concatenated raw transcript (vision output) so you
  can inspect what the structured-extraction model received.

## Schema

Edit `schema.yaml` to add/remove fields. Supported types:

| Type              | Excel cell value                                      |
|-------------------|-------------------------------------------------------|
| `string`          | raw string                                            |
| `integer`         | raw number-as-string                                  |
| `number`          | raw number-as-string                                  |
| `boolean`         | `true` / `false` text                                 |
| `enum`            | one of the configured `values`                        |
| `list_of_strings` | JSON array string, e.g. `["dx1","dx2"]`               |
| `list_of_objects` | JSON array of objects, e.g. `[{"name":"...","dose":"..."}]` |

Each field can declare:
- `description` — short hint for the LLM
- `hebrew_aliases` — Hebrew labels the LLM should look for in the transcript
- `values` (enum only) — allowed enum values
- `item_schema` (list_of_objects only) — sub-field names and types

## Output format

- One worksheet, `extracted`.
- Row 1: schema field names in declared order.
- Row 2: extracted values. Missing scalars → blank. Missing lists → `[]`.
- If the model failed to return valid JSON after one retry, an extra
  `_errors` column at the end carries a short error string and the partial
  output (if any) is still written.

## Privacy / local-only posture

- Only HTTP destination is `--ollama-host` (default `http://localhost:11434`).
- No telemetry, no cloud SDKs in `requirements.txt`.
- `--verbose` enables DEBUG logs that include text snippets for
  troubleshooting; default INFO logs only counts/timings.
- Generated `.xlsx` and `--save-text` files are local. `out/` is gitignored.

## Tests

There are two test layers — fast unit tests and slow integration tests.

### Setup
```bash
.venv/bin/pip install -r requirements-dev.txt
```

### Unit tests (no Ollama, < 1s)
Cover schema loading, Excel writing (Hebrew + JSON list cells round-trip),
and the loose JSON parser used to recover from messy LLM output.
```bash
.venv/bin/python -m pytest tests/test_unit.py -v
```

### Integration tests (real Ollama, ~5 min for 9 tests)

Generate synthetic Hebrew/English doctor-note screenshots with Pillow +
python-bidi, run the full pipeline against each, and validate the output via
**three independent test types** that share a single pipeline run per fixture
(via a session-scoped `pipeline_outputs` fixture in `conftest.py`):

1. **`test_pipeline_matchers`** — fast, deterministic substring/length
   assertions against the extracted xlsx (e.g. `exposed_to_pigeons == "yes"`,
   `diagnosis` contains "pneumonitis").
2. **`test_pipeline_judge_transcript`** — uses `gemma4:latest` as a judge to
   ask, for every ground-truth field, "is this value present in the OCR
   transcript?" Pass threshold: ≥60% present-or-partial. Tells you whether
   **vision** did its job.
3. **`test_pipeline_judge_extraction`** — uses `gemma4:latest` to score each
   extracted field against the ground truth with semantic equivalence
   (Hebrew/English bilingual, "HP" ≡ "Hypersensitivity Pneumonitis" ≡
   "דלקת ריאות"). Pass threshold: ≤30% of fields incorrect/missing. Tells
   you whether **structured extraction** did its job.

Run:
```bash
# 1) Generate fixtures (PNG screenshots + ground-truth/matchers)
.venv/bin/python tests/generate_fixtures.py

# 2) Run all 9 tests (3 fixtures × 3 test types)
.venv/bin/python -m pytest tests/test_pipeline.py -v
```

Latest run: **9 / 9 passed in 4 m 27 s** on a 32 GB M-series MacBook Pro.

Three cases ship by default:
- `case_01_pigeons_yes` — 2-page note with explicit pigeon exposure (HP /
  Bird Fancier's Lung). Expects `exposed_to_pigeons == "yes"`.
- `case_02_pigeons_no` — 1-page viral pharyngitis with explicit "no exposure
  to pigeons or birds". Expects `exposed_to_pigeons == "no"`.
- `case_03_minimal` — 1-page routine check-up with no mention of birds.
  Expects `exposed_to_pigeons` ∈ {`"unknown"`, `"no"`}.

Each generated `expected.yaml` carries:
- `ground_truth`: the schema-shaped ground truth (auto-derived from the data
  rendered into the PNG, so all 14 fields are always populated — this is
  what the LLM judge compares against).
- `fields`: per-field matchers (`equals`, `contains`, `is_one_of`,
  `list_min_length`, `list_contains_any`) used by the matcher test.
  Matchers are intentionally loose — LLMs are non-deterministic, so we check
  presence and structural shape rather than exact strings.

Generated PNGs live under `tests/fixtures/` and are gitignored.

### Adding a new test case

Edit the `CASES` list in [tests/generate_fixtures.py](tests/generate_fixtures.py)
and re-run the generator. Each case declares `data` (rendered into the PNG),
`exposed_to_pigeons` (the screening enum), and `expected` (matchers consumed
by [tests/test_pipeline.py](tests/test_pipeline.py)). Ground truth is
auto-derived from `data`, so you don't have to keep it in sync manually.

## Troubleshooting

- **`Ollama not reachable`**: start Ollama (see Prerequisites) and confirm
  `curl http://localhost:11434/api/tags` responds.
- **`Missing Ollama models`**: `ollama pull qwen2.5vl:7b` /
  `ollama pull gemma4:26b`.
- **Empty output / lots of nulls**: run with `--verbose --save-text out/raw.txt`
  and inspect the saved transcript. If the vision step is producing garbled
  text, increase image resolution or zoom on the source screenshots.
- **Hebrew shows as `\u05d…` in cells**: should not happen — list fields use
  `ensure_ascii=False`. If you see it, please share the file.
