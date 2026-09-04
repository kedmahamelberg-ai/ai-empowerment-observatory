#!/usr/bin/env python3
"""Generate a three-page public AI Empowerment Pulse PDF from the latest data."""

from __future__ import annotations

import json
import math
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "data" / "releases" / "current.json"
METHODOLOGY = ROOT / "data" / "methodology" / "latest.json"
REPORT_DIR = ROOT / "reports"
REPORT_PATH = REPORT_DIR / "ai-empowerment-pulse-latest.pdf"
META_PATH = ROOT / "data" / "reports" / "latest.json"

PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#0d223d")
TEAL = colors.HexColor("#176f78")
BLUE = colors.HexColor("#7ea6f6")
GOLD = colors.HexColor("#d7a944")
ROSE = colors.HexColor("#b95750")
INK = colors.HexColor("#172533")
MUTED = colors.HexColor("#61717d")
LIGHT = colors.HexColor("#eef2f3")
WHITE = colors.white


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def ascii_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def signed(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    number = float(value)
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:.{digits}f}"


def percent(value: Any) -> str:
    return f"{float(value or 0) * 100:.1f}%"


def collect_dates(events: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    values: list[datetime] = []

    def parse(raw: Any) -> datetime | None:
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    for event in events.get("events", []):
        value = parse(event.get("event_date"))
        if value:
            values.append(value)
        for source in event.get("sources", []):
            value = parse(source.get("published_at"))
            if value:
                values.append(value)

    if not values:
        return None, None
    values.sort()
    return values[0], values[-1]


def date_label(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "Current observation window"
    return f"{start.day} {start.strftime('%b %Y')} - {end.day} {end.strftime('%b %Y')}"


def dominant(distribution: dict[str, Any]) -> tuple[str, float]:
    if not distribution:
        return "unclear", 0.0
    key, value = max(distribution.items(), key=lambda item: float(item[1] or 0))
    return key, float(value or 0)


def takeaway_text(coverage: dict[str, Any], event: dict[str, Any], amp: dict[str, Any]) -> list[tuple[str, str]]:
    event_index = float(event.get("empowerment_index") or 0)
    gap = float(amp.get("directional_amplification_gap") or 0)
    ratio = float(amp.get("coverage_event_ratio") or 0)
    narrative, narrative_share = dominant(coverage.get("narrative_distribution") or {})

    direction = (
        "close to neutral"
        if abs(event_index) < 5
        else ("expansion-oriented" if event_index > 0 else "contraction-oriented")
    )
    gap_text = (
        "Article repetition barely changes the direction of the signal."
        if abs(gap) < 1
        else (
            "Article volume makes the public picture more expansion-oriented."
            if gap > 0
            else "Article volume makes the public picture more contraction-oriented."
        )
    )
    ratio_text = (
        "Most coverage units represent distinct developments in this release."
        if ratio < 1.1
        else "Repeated coverage materially exceeds unique-event volume."
    )
    narrative_label = narrative.replace("_", " ").title()

    return [
        ("Overall direction", f"The unique-event signal is {direction} ({signed(event_index)})."),
        ("Amplification", f"{gap_text} Gap: {signed(gap)} points."),
        ("Coverage volume", f"{ratio_text} Coverage/Event ratio: {ratio:.2f}."),
        ("Narrative climate", f"{narrative_label} is the largest article-weighted frame ({narrative_share * 100:.1f}%)."),
    ]


def draw_header(canvas: Canvas, page_no: int, title: str) -> None:
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 26 * mm, PAGE_W, 26 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(17 * mm, PAGE_H - 16 * mm, "AI EMPOWERMENT OBSERVATORY")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - 17 * mm, PAGE_H - 16 * mm, f"{title}  |  {page_no}/3")


def draw_footer(canvas: Canvas, window: str) -> None:
    canvas.setStrokeColor(colors.HexColor("#d8e0e4"))
    canvas.line(17 * mm, 16 * mm, PAGE_W - 17 * mm, 16 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(17 * mm, 10 * mm, f"Observation window: {ascii_text(window)}")
    canvas.drawRightString(PAGE_W - 17 * mm, 10 * mm, "observatory.hamelberg-ai.com")


def draw_paragraph(canvas: Canvas, text: str, x: float, y: float, width: float, style: ParagraphStyle) -> float:
    paragraph = Paragraph(ascii_text(text), style)
    _, height = paragraph.wrap(width, PAGE_H)
    paragraph.drawOn(canvas, x, y - height)
    return y - height


def draw_metric_card(canvas: Canvas, x: float, y: float, w: float, h: float, label: str, value: str, note: str, accent) -> None:
    canvas.setFillColor(WHITE)
    canvas.setStrokeColor(colors.HexColor("#d8e0e4"))
    canvas.roundRect(x, y - h, w, h, 5 * mm, fill=1, stroke=1)
    canvas.setFillColor(accent)
    canvas.roundRect(x, y - h, 4 * mm, h, 2 * mm, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawString(x + 9 * mm, y - 9 * mm, ascii_text(label).upper())
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(x + 9 * mm, y - 22 * mm, ascii_text(value))
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.2)
    canvas.drawString(x + 9 * mm, y - 31 * mm, ascii_text(note)[:62])


def draw_bar(canvas: Canvas, x: float, y: float, w: float, label: str, value: float, max_value: float, color, suffix: str = "") -> None:
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(x, y, ascii_text(label))
    canvas.setFillColor(LIGHT)
    canvas.roundRect(x + 38 * mm, y - 1.5 * mm, w - 55 * mm, 5 * mm, 2.5 * mm, fill=1, stroke=0)
    width = 0 if max_value <= 0 else (w - 55 * mm) * max(0, min(value / max_value, 1))
    canvas.setFillColor(color)
    canvas.roundRect(x + 38 * mm, y - 1.5 * mm, width, 5 * mm, 2.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(x + w, y, f"{value:.1f}{suffix}")


def build_pdf(release: dict[str, Any], methodology: dict[str, Any] | None) -> dict[str, Any]:
    coverage = (release.get("lenses") or {}).get("coverage") or {}
    event = (release.get("lenses") or {}).get("event") or {}
    release_amp = release.get("amplification") or {}
    amp = {
        "directional_amplification_gap": release_amp.get("directional_gap"),
        "coverage_event_ratio": release_amp.get("coverage_event_ratio"),
    }
    start_value = str(release.get("period_start") or "")
    end_value = str(release.get("period_end") or "")
    try:
        start = datetime.fromisoformat(start_value).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_value).replace(tzinfo=timezone.utc)
    except ValueError:
        start = end = None
    window = date_label(start, end)
    generated = datetime.now(timezone.utc)
    title = "AI Empowerment Pulse"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)

    canvas = Canvas(str(REPORT_PATH), pagesize=A4)
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=INK,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=8.2,
        leading=12,
        textColor=MUTED,
    )

    # Page 1
    draw_header(canvas, 1, title)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 28)
    canvas.drawString(17 * mm, PAGE_H - 48 * mm, title)
    canvas.setFillColor(TEAL)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(17 * mm, PAGE_H - 57 * mm, ascii_text(f"CURRENT-WINDOW BRIEF  |  {window}"))
    y = PAGE_H - 66 * mm
    y = draw_paragraph(
        canvas,
        "A concise public brief on what current AI developments indicate for human empowerment - and how the picture changes when article volume is separated from unique real-world events.",
        17 * mm,
        y,
        176 * mm,
        body,
    )

    card_y = y - 9 * mm
    card_w = 55 * mm
    gap_w = 5.5 * mm
    draw_metric_card(canvas, 17 * mm, card_y, card_w, 39 * mm, "Coverage Index", signed(coverage.get("empowerment_index")), f"{coverage.get('unit_count_ai_relevant', 0)} AI-relevant articles", BLUE)
    draw_metric_card(canvas, 17 * mm + card_w + gap_w, card_y, card_w, 39 * mm, "Event Index", signed(event.get("empowerment_index")), f"{event.get('unit_count_ai_relevant', 0)} unique AI events", TEAL)
    draw_metric_card(canvas, 17 * mm + 2 * (card_w + gap_w), card_y, card_w, 39 * mm, "Amplification Gap", signed(amp.get("directional_amplification_gap")), "Coverage minus Event", GOLD)

    section_y = card_y - 52 * mm
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(17 * mm, section_y, "Four takeaways")
    section_y -= 9 * mm

    takeaways = takeaway_text(coverage, event, amp)
    box_w = 85 * mm
    box_h = 36 * mm
    for index, (label, text) in enumerate(takeaways):
        col = index % 2
        row = index // 2
        x = 17 * mm + col * (box_w + 6 * mm)
        top = section_y - row * (box_h + 6 * mm)
        canvas.setFillColor(colors.HexColor("#f5f7f7"))
        canvas.roundRect(x, top - box_h, box_w, box_h, 4 * mm, fill=1, stroke=0)
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(x + 5 * mm, top - 8 * mm, ascii_text(label).upper())
        draw_paragraph(canvas, text, x + 5 * mm, top - 13 * mm, box_w - 10 * mm, small)

    draw_footer(canvas, window)
    canvas.showPage()

    # Page 2
    draw_header(canvas, 2, title)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 23)
    canvas.drawString(17 * mm, PAGE_H - 45 * mm, "Attention versus unique developments")
    y = PAGE_H - 57 * mm
    y = draw_paragraph(
        canvas,
        "Coverage Lens retains article repetition. Event Lens gives each resolved development one weight. Their difference reveals whether media volume shifts the aggregate direction.",
        17 * mm,
        y,
        176 * mm,
        body,
    )

    coverage_n = float(coverage.get("unit_count_ai_relevant") or 0)
    event_n = float(event.get("unit_count_ai_relevant") or 0)
    max_n = max(coverage_n, event_n, 1)
    y -= 14 * mm
    draw_bar(canvas, 17 * mm, y, 176 * mm, "Coverage articles", coverage_n, max_n, BLUE)
    y -= 12 * mm
    draw_bar(canvas, 17 * mm, y, 176 * mm, "Unique events", event_n, max_n, TEAL)
    y -= 18 * mm

    canvas.setFont("Helvetica-Bold", 14)
    canvas.setFillColor(INK)
    canvas.drawString(17 * mm, y, "Narrative climate")
    y -= 10 * mm
    narrative_keys = [
        ("opportunity", "Opportunity", TEAL),
        ("threat", "Threat", ROSE),
        ("contested", "Contested", GOLD),
        ("descriptive_neutral", "Descriptive/neutral", BLUE),
    ]
    for key, label, color in narrative_keys:
        cov_value = float((coverage.get("narrative_distribution") or {}).get(key) or 0) * 100
        evt_value = float((event.get("narrative_distribution") or {}).get(key) or 0) * 100
        draw_bar(canvas, 17 * mm, y, 85 * mm, f"C: {label}", cov_value, 100, color, "%")
        draw_bar(canvas, 108 * mm, y, 85 * mm, f"E: {label}", evt_value, 100, color, "%")
        y -= 9.5 * mm

    y -= 8 * mm
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(17 * mm, y, "Event Lens empowerment mix")
    y -= 10 * mm
    status_colors = {
        "expanding": TEAL,
        "contracting": ROSE,
        "mixed": GOLD,
        "non_empowerment": BLUE,
        "unclear": MUTED,
    }
    for key in ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]:
        value = float((event.get("status_distribution") or {}).get(key) or 0) * 100
        draw_bar(canvas, 17 * mm, y, 176 * mm, key.replace("_", " ").title(), value, 100, status_colors[key], "%")
        y -= 9.5 * mm

    draw_footer(canvas, window)
    canvas.showPage()

    # Page 3
    draw_header(canvas, 3, title)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 23)
    canvas.drawString(17 * mm, PAGE_H - 45 * mm, "How to read the signal")
    y = PAGE_H - 58 * mm

    methods = [
        ("Coverage Lens", "Each observed article receives one weight. Repetition measures attention and framing."),
        ("Event Lens", "Each resolved real-world development receives one weight."),
        ("Unit score", "Expanding = +degree/3; contracting = -degree/3; mixed and non-empowerment = 0; unclear is excluded."),
        ("Amplification Gap", "Coverage Empowerment Index minus Event Empowerment Index."),
        ("Country rule", "Search market is not event country. Country signals are released only when evidence thresholds are met."),
        ("Human governance", "Automated coding is versioned and audited. Human review corrects high-risk cases and periodic stratified samples."),
    ]

    for label, text in methods:
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(17 * mm, y, ascii_text(label).upper())
        y = draw_paragraph(canvas, text, 55 * mm, y + 2 * mm, 138 * mm, small) - 5 * mm

    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(17 * mm, y, "Evidence examples")
    y -= 9 * mm

    top_events = sorted(
        (release.get("units") or {}).get("event_records", []) or [],
        key=lambda item: (
            int(item.get("member_article_count") or len(item.get("sources", [])) or 0),
            str(item.get("event_date") or ""),
        ),
        reverse=True,
    )[:3]

    for index, event_item in enumerate(top_events, start=1):
        title_text = ascii_text(event_item.get("event_title") or "Untitled event")[:110]
        source_count = int(event_item.get("member_article_count") or len(event_item.get("sources", [])) or 1)
        canvas.setFillColor(colors.HexColor("#f5f7f7"))
        canvas.roundRect(17 * mm, y - 25 * mm, 176 * mm, 22 * mm, 3 * mm, fill=1, stroke=0)
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(22 * mm, y - 9 * mm, f"EVENT {index}  |  {source_count} SOURCE ARTICLE(S)")
        draw_paragraph(canvas, title_text, 22 * mm, y - 13 * mm, 165 * mm, small)
        y -= 28 * mm

    y -= 3 * mm
    canvas.setFillColor(NAVY)
    canvas.roundRect(17 * mm, y - 32 * mm, 176 * mm, 29 * mm, 5 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(23 * mm, y - 13 * mm, "Bring the Observatory into your organisation")
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(23 * mm, y - 22 * mm, "In-company talks, executive briefings, and training: kedma@hamelberg-ai.com")

    draw_footer(canvas, window)
    canvas.save()

    meta = {
        "slug": "ai-empowerment-pulse-latest",
        "title": title,
        "edition": (
            "Human-audited weekly brief"
            if str(((release.get("reliability") or {}).get("governance") or {}).get("audit_status") or "").lower() == "complete"
            else "Current weekly evidence brief"
        ),
        "release_id": release.get("release_id"),
        "release_revision": int(release.get("revision") or 1),
        "period_start": release.get("period_start"),
        "period_end": release.get("period_end"),
        "source_of_truth": "/data/releases/current.json",
        "file": "/reports/ai-empowerment-pulse-latest.pdf",
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "observation_window": window,
        "coverage_units": int(coverage_n),
        "event_units": int(event_n),
        "coverage_index": coverage.get("empowerment_index"),
        "event_index": event.get("empowerment_index"),
        "directional_amplification_gap": amp.get("directional_amplification_gap"),
        "coverage_event_ratio": amp.get("coverage_event_ratio"),
        "pages": 3,
    }
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main() -> int:
    release = load_json(RELEASE)
    methodology = load_json(METHODOLOGY) if METHODOLOGY.exists() else None
    meta = build_pdf(release, methodology)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
