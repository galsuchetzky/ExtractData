"""Generate synthetic medical-document screenshots and expected outputs.

Each fixture lives at tests/fixtures/<case_id>/ and contains:
  - page_<N>.png   one or more rendered pages (the "screenshots")
  - expected.yaml  ground-truth values + loose per-field matchers for tests

Usage:
    python tests/generate_fixtures.py            # generate all cases
    python tests/generate_fixtures.py --list     # list cases
    python tests/generate_fixtures.py --case case_01_pigeons_yes
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# ----------------------------- Font loading -----------------------------

FONT_CANDIDATES: list[tuple[str, int]] = [
    # macOS — Arial Unicode covers Hebrew AND Latin (single font, no fallback needed)
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    # macOS — Arial Hebrew (Hebrew-only; Latin will be filled by latin_font below)
    ("/System/Library/Fonts/ArialHB.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Hebrew.ttc", 0),
    # Windows — Arial and Segoe UI both ship with Hebrew + Latin
    ("C:/Windows/Fonts/arial.ttf", 0),
    ("C:/Windows/Fonts/segoeui.ttf", 0),
    # Linux
    ("/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]


def _font_has(font: ImageFont.FreeTypeFont, chars: list[str]) -> bool:
    """Render `chars` and return True iff each produces a distinct, non-empty glyph."""
    masks = []
    for ch in chars:
        img = Image.new("L", (60, 60), 255)
        ImageDraw.Draw(img).text((6, 6), ch, font=font, fill=0)
        masks.append(bytes(img.getdata()))
    if len(set(masks)) < len(chars):
        return False
    blank = bytes(Image.new("L", (60, 60), 255).getdata())
    return all(m != blank for m in masks)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Pick a font with REAL Hebrew + Latin glyphs (rejecting tofu fallbacks)."""
    tried: list[str] = []
    for path, index in FONT_CANDIDATES:
        if not Path(path).exists():
            tried.append(f"{path} (missing)")
            continue
        try:
            f = ImageFont.truetype(path, size=size, index=index)
        except Exception as exc:
            tried.append(f"{path} (load error: {exc})")
            continue
        if not _font_has(f, ["ש", "ל", "ו", "ם"]):
            tried.append(f"{path} (no Hebrew glyphs)")
            continue
        if not _font_has(f, ["A", "B", "C", "D"]):
            tried.append(f"{path} (no Latin glyphs)")
            continue
        return f
    raise RuntimeError(
        "No font with both Hebrew and Latin coverage found. Tried:\n  "
        + "\n  ".join(tried)
    )


# ----------------------------- Drawing helpers --------------------------

PAGE_W, PAGE_H = 1240, 1750  # ~A4 at 150 dpi
MARGIN = 60
LINE_GAP = 6

WHITE = "white"
BLACK = "black"
GREY = (90, 90, 90)
HEADER_BG = (235, 240, 248)


def _has_hebrew(s: str) -> bool:
    return any("֐" <= c <= "׿" for c in s)


def _shape(s: str) -> str:
    """Run BiDi reordering on Hebrew strings so PIL renders them correctly."""
    return get_display(s) if _has_hebrew(s) else s


@dataclass
class Pen:
    draw: ImageDraw.ImageDraw
    body: ImageFont.FreeTypeFont
    bold: ImageFont.FreeTypeFont
    big: ImageFont.FreeTypeFont
    small: ImageFont.FreeTypeFont
    y: int = MARGIN

    def line(
        self,
        text: str,
        font: ImageFont.FreeTypeFont | None = None,
        align: str = "left",
        color=BLACK,
    ) -> None:
        f = font or self.body
        shaped = _shape(text)
        bbox = self.draw.textbbox((0, 0), shaped, font=f)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if align == "right":
            x = PAGE_W - MARGIN - w
        elif align == "center":
            x = (PAGE_W - w) // 2
        else:
            x = MARGIN
        self.draw.text((x, self.y), shaped, font=f, fill=color)
        self.y += h + LINE_GAP

    def gap(self, n: int = 1) -> None:
        self.y += LINE_GAP * 3 * n

    def rule(self) -> None:
        self.draw.line(
            [(MARGIN, self.y), (PAGE_W - MARGIN, self.y)], fill=GREY, width=1
        )
        self.y += 8


def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (PAGE_W, PAGE_H), WHITE)
    draw = ImageDraw.Draw(img)
    return img, draw


def make_pen(draw: ImageDraw.ImageDraw) -> Pen:
    return Pen(
        draw=draw,
        body=load_font(22),
        bold=load_font(24),
        big=load_font(32),
        small=load_font(18),
    )


# ----------------------------- Page templates ---------------------------

def render_page1(case_data: dict[str, Any]) -> Image.Image:
    img, draw = new_page()
    draw.rectangle([(0, 0), (PAGE_W, MARGIN + 40)], fill=HEADER_BG)
    pen = make_pen(draw)
    pen.y = MARGIN // 2
    pen.line(case_data["clinic"], font=pen.big, align="right")
    pen.y = MARGIN + 70

    pen.line(
        'דו"ח מפגש רפואי / Medical Encounter Report',
        font=pen.bold,
        align="center",
    )
    pen.gap()
    pen.rule()
    pen.line(f"שם המטופל / Patient name: {case_data['patient_name']}")
    pen.line(
        f"ת.ז. / Patient ID: {case_data['patient_id']}     "
        f"תאריך לידה / DOB: {case_data['date_of_birth']}"
    )
    pen.line(
        f"תאריך ביקור / Visit date: {case_data['visit_date']}     "
        f"שם הרופא / Physician: {case_data['doctor_name']}"
    )
    pen.gap()
    pen.rule()
    pen.line("סיבת הפנייה / Chief Complaint:", font=pen.bold)
    pen.line(case_data["chief_complaint"])
    pen.gap()
    pen.line("רקע רפואי / Medical History:", font=pen.bold)
    for ln in case_data.get("history", "").split("\n"):
        if ln.strip():
            pen.line(ln)
    pen.gap()
    pen.line("תסמינים / Symptoms:", font=pen.bold)
    if case_data["symptoms"]:
        for s in case_data["symptoms"]:
            pen.line(f"  • {s}")
    else:
        pen.line("  (אין / none reported)")
    pen.gap()
    pen.line("חשיפות סביבתיות / Environmental Exposures:", font=pen.bold)
    pen.line(case_data["exposure_text"])
    return img


def render_page2(case_data: dict[str, Any]) -> Image.Image:
    img, draw = new_page()
    draw.rectangle([(0, 0), (PAGE_W, MARGIN + 40)], fill=HEADER_BG)
    pen = make_pen(draw)
    pen.y = MARGIN // 2
    pen.line(case_data["clinic"], font=pen.big, align="right")
    pen.y = MARGIN + 70

    pen.line("המשך / continued", font=pen.small, align="center", color=GREY)
    pen.gap()
    pen.line("אבחנה / Diagnosis:", font=pen.bold)
    for d in case_data["diagnosis"]:
        pen.line(f"  • {d}")
    pen.gap()
    pen.line("תרופות / Medications:", font=pen.bold)
    if case_data["medications"]:
        for m in case_data["medications"]:
            pen.line(f"  • {m['name']} {m['dose']} - {m['frequency']}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.line("פרוצדורות / Procedures:", font=pen.bold)
    if case_data["procedures"]:
        for p in case_data["procedures"]:
            pen.line(f"  • {p}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.line("הפניות / Referrals:", font=pen.bold)
    if case_data["referrals"]:
        for r in case_data["referrals"]:
            pen.line(f"  • {r}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.gap()
    pen.line(
        f'חתימת הרופא / Doctor signature: {case_data["doctor_name"]}',
        font=pen.small,
        color=GREY,
    )
    return img


def render_single_page(case_data: dict[str, Any]) -> Image.Image:
    """One-page layout: identity + clinical + treatment all on one sheet."""
    img, draw = new_page()
    draw.rectangle([(0, 0), (PAGE_W, MARGIN + 40)], fill=HEADER_BG)
    pen = make_pen(draw)
    pen.y = MARGIN // 2
    pen.line(case_data["clinic"], font=pen.big, align="right")
    pen.y = MARGIN + 70

    pen.line(
        'דו"ח מפגש רפואי / Medical Encounter Report',
        font=pen.bold,
        align="center",
    )
    pen.gap()
    pen.rule()
    pen.line(f"שם המטופל / Patient name: {case_data['patient_name']}")
    pen.line(
        f"ת.ז. / Patient ID: {case_data['patient_id']}     "
        f"תאריך לידה / DOB: {case_data['date_of_birth']}"
    )
    pen.line(
        f"תאריך ביקור / Visit date: {case_data['visit_date']}     "
        f"שם הרופא / Physician: {case_data['doctor_name']}"
    )
    pen.gap()
    pen.rule()
    pen.line("סיבת הפנייה / Chief Complaint:", font=pen.bold)
    pen.line(case_data["chief_complaint"])
    pen.gap()
    pen.line("רקע רפואי / Medical History:", font=pen.bold)
    for ln in case_data.get("history", "").split("\n"):
        if ln.strip():
            pen.line(ln)
    pen.gap()
    pen.line("תסמינים / Symptoms:", font=pen.bold)
    if case_data["symptoms"]:
        for s in case_data["symptoms"]:
            pen.line(f"  • {s}")
    else:
        pen.line("  (אין / none reported)")
    pen.gap()
    pen.line("חשיפות סביבתיות / Environmental Exposures:", font=pen.bold)
    pen.line(case_data["exposure_text"])
    pen.gap()
    pen.rule()
    pen.line("אבחנה / Diagnosis:", font=pen.bold)
    for d in case_data["diagnosis"]:
        pen.line(f"  • {d}")
    pen.gap()
    pen.line("תרופות / Medications:", font=pen.bold)
    if case_data["medications"]:
        for m in case_data["medications"]:
            pen.line(f"  • {m['name']} {m['dose']} - {m['frequency']}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.line("פרוצדורות / Procedures:", font=pen.bold)
    if case_data["procedures"]:
        for p in case_data["procedures"]:
            pen.line(f"  • {p}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.line("הפניות / Referrals:", font=pen.bold)
    if case_data["referrals"]:
        for r in case_data["referrals"]:
            pen.line(f"  • {r}")
    else:
        pen.line("  (אין / none)")
    pen.gap()
    pen.line(
        f'חתימת הרופא / Doctor signature: {case_data["doctor_name"]}',
        font=pen.small,
        color=GREY,
    )
    return img


# ----------------------------- Cases ------------------------------------

@dataclass
class Case:
    id: str
    pages: int
    notes: str
    data: dict[str, Any]
    exposed_to_pigeons: str  # 'yes' | 'no' | 'unknown'
    expected: dict[str, dict[str, Any]] = field(default_factory=dict)

    def ground_truth(self) -> dict[str, Any]:
        """Schema-shaped ground truth derived from the data we rendered.

        Keeping this auto-derived (rather than hand-curated) guarantees the
        judge never penalises the pipeline for extracting a field whose
        ground-truth value the test author forgot to populate.
        """
        d = self.data
        return {
            "patient_name": d["patient_name"],
            "patient_id": d["patient_id"],
            "date_of_birth": d["date_of_birth"],
            "visit_date": d["visit_date"],
            "doctor_name": d["doctor_name"],
            "clinic": d["clinic"],
            "chief_complaint": d["chief_complaint"],
            "diagnosis": list(d["diagnosis"]),
            "symptoms": list(d["symptoms"]),
            "medical_history_summary": d.get("history", ""),
            "medications": [dict(m) for m in d["medications"]],
            "procedures": list(d["procedures"]),
            "referrals": list(d["referrals"]),
            "exposed_to_pigeons": self.exposed_to_pigeons,
        }


CASES: list[Case] = [
    Case(
        id="case_01_pigeons_yes",
        pages=2,
        notes="Suspected Bird Fancier's Lung / Hypersensitivity Pneumonitis. Explicit pigeon exposure.",
        exposed_to_pigeons="yes",
        data={
            "patient_name": "ישראל ישראלי",
            "patient_id": "311234567",
            "date_of_birth": "15/03/1962",
            "visit_date": "07/05/2026",
            "doctor_name": 'ד"ר רותם כהן',
            "clinic": "מכבי שירותי בריאות - סניף הרצליה",
            "chief_complaint": "שיעול יבש מתמשך כשלושה שבועות, עם החמרה לאחרונה",
            "history": (
                "המטופל בריא בדרך כלל. ללא רקע של אסטמה. לא מעשן.\n"
                "עובד כעצמאי בתחום הנגרות."
            ),
            "symptoms": ["שיעול יבש", "קוצר נשימה במאמץ", "עייפות"],
            "diagnosis": ["Hypersensitivity Pneumonitis - Bird Fancier's Lung suspected"],
            "medications": [
                {"name": "Prednisone", "dose": "40mg", "frequency": "פעם ביום"},
                {"name": "Ventolin", "dose": "100mcg", "frequency": "לפי הצורך"},
            ],
            "procedures": ["צילום חזה", "CT חזה ברזולוציה גבוהה (HRCT)"],
            "referrals": ["מרפאת ריאות"],
            "exposure_text": (
                "המטופל מחזיק מגדל יונים בגג הבית מעל 10 שנים, חשיפה יומיומית."
            ),
        },
        expected={
            "patient_name": {"contains": ["ישראל"]},
            "patient_id": {"equals": "311234567"},
            "visit_date": {"contains": ["07/05/2026", "2026"]},
            "exposed_to_pigeons": {"equals": "yes"},
            "diagnosis": {
                "list_contains_any": [
                    "pneumonitis",
                    "Pneumonitis",
                    "Fancier",
                    "ריאות",
                ]
            },
            "medications": {"list_min_length": 1},
            "symptoms": {"list_min_length": 1},
        },
    ),
    Case(
        id="case_02_pigeons_no",
        pages=1,
        notes="Acute viral pharyngitis. Exposure section explicitly says no birds/pigeons.",
        exposed_to_pigeons="no",
        data={
            "patient_name": "מיכל לוי",
            "patient_id": "208765432",
            "date_of_birth": "22/11/1985",
            "visit_date": "03/05/2026",
            "doctor_name": 'ד"ר נועם בר-און',
            "clinic": "כללית - מרפאת רמת השרון",
            "chief_complaint": "כאב גרון וחום מזה יומיים",
            "history": "ללא רקע משמעותי. מטופלת בריאה.",
            "symptoms": ["כאב גרון", "חום 38.4", "כאבי שרירים"],
            "diagnosis": ["Acute pharyngitis - viral"],
            "medications": [
                {"name": "Acamol", "dose": "500mg", "frequency": "כל 6 שעות לפי הצורך"},
            ],
            "procedures": ["בדיקת סטרפ מהירה - שלילית"],
            "referrals": [],
            "exposure_text": "ללא חשיפה לבעלי חיים, ללא חשיפה ליונים או ציפורים.",
        },
        expected={
            "patient_name": {"contains": ["מיכל"]},
            "patient_id": {"equals": "208765432"},
            "exposed_to_pigeons": {"equals": "no"},
            "diagnosis": {
                "list_contains_any": ["pharyngitis", "Pharyngitis", "viral"]
            },
            "symptoms": {"list_min_length": 1},
        },
    ),
    Case(
        id="case_03_minimal",
        pages=1,
        notes="Routine checkup. No mention of birds at all → exposed_to_pigeons should be 'unknown'.",
        exposed_to_pigeons="unknown",
        data={
            "patient_name": "דניאל שמש",
            "patient_id": "045678901",
            "date_of_birth": "08/06/1990",
            "visit_date": "01/05/2026",
            "doctor_name": 'ד"ר יעל גולן',
            "clinic": "לאומית - בית שמש",
            "chief_complaint": "ביקורת שגרתית",
            "history": "מטופל בריא. ללא תלונות.",
            "symptoms": [],
            "diagnosis": ["Routine check-up - no acute findings"],
            "medications": [],
            "procedures": ["בדיקות דם שגרתיות"],
            "referrals": [],
            "exposure_text": "אורח חיים פעיל. אין מידע נוסף על חשיפות.",
        },
        expected={
            "patient_name": {"contains": ["דניאל"]},
            "patient_id": {"equals": "045678901"},
            "exposed_to_pigeons": {"is_one_of": ["unknown", "no"]},
            "diagnosis": {
                "list_contains_any": [
                    "check-up",
                    "checkup",
                    "Routine",
                    "ביקורת",
                ]
            },
        },
    ),
]


# ----------------------------- Generation -------------------------------

def _render_pages(case: Case) -> list[Image.Image]:
    if case.pages == 1:
        return [render_single_page(case.data)]
    if case.pages == 2:
        return [render_page1(case.data), render_page2(case.data)]
    raise ValueError(f"Unsupported page count {case.pages} for {case.id}")


def generate_case(case: Case) -> Path:
    out_dir = FIXTURES_DIR / case.id
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page_*.png"):
        old.unlink()

    for n, img in enumerate(_render_pages(case), start=1):
        img.save(out_dir / f"page_{n}.png", format="PNG", optimize=True)

    payload = {
        "id": case.id,
        "notes": case.notes,
        "ground_truth": case.ground_truth(),
        "fields": case.expected,
    }
    (out_dir / "expected.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--case", help="Generate only the named case id")
    ap.add_argument("--list", action="store_true", help="List available cases and exit")
    args = ap.parse_args(argv)

    if args.list:
        for c in CASES:
            print(f"{c.id} ({c.pages} page{'s' if c.pages != 1 else ''}) - {c.notes}")
        return 0

    selected = [c for c in CASES if not args.case or c.id == args.case]
    if not selected:
        print(f"No case matching {args.case!r}", file=sys.stderr)
        return 1

    for c in selected:
        out = generate_case(c)
        print(f"Wrote {out} ({c.pages} page{'s' if c.pages != 1 else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
