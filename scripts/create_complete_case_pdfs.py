"""將完整虛構測試案件的 Markdown 卷證轉成可上傳 PDF。"""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "test_cases" / "complete_case_waste_video" / "source"
OUTPUT_DIR = PROJECT_ROOT / "output" / "pdf" / "complete_case_waste_video"
FONT_PATH = Path("/System/Library/Fonts/STHeiti Medium.ttc")
FONT_NAME = "LegalDemoHeiti"

DOCUMENTS = [
    ("01_訴願書.md", "01_訴願書.pdf", "訴願書（主文件）"),
    ("02_影像逐格檢視報告.md", "02_影像逐格檢視報告.pdf", "附件一｜影像逐格檢視報告"),
    ("03_行車紀錄與具結陳述.md", "03_行車紀錄與具結陳述.pdf", "附件二｜行車紀錄與具結陳述"),
    ("04_原行政處分書.md", "04_原行政處分書.pdf", "附件三｜原行政處分書"),
    ("05_原處分機關卷內證據.md", "05_原處分機關卷內證據.pdf", "附件四｜原處分機關卷內證據"),
    ("06_送達與訴願期間證明.md", "06_送達與訴願期間證明.pdf", "附件五｜送達與訴願期間證明"),
]


def register_fonts() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"找不到中文字型：{FONT_PATH}")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
    pdfmetrics.registerFontFamily(
        FONT_NAME,
        normal=FONT_NAME,
        bold=FONT_NAME,
        italic=FONT_NAME,
        boldItalic=FONT_NAME,
    )


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleZh",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=20,
            leading=29,
            textColor=colors.HexColor("#153247"),
            alignment=TA_CENTER,
            spaceAfter=7 * mm,
        ),
        "h2": ParagraphStyle(
            "H2Zh",
            parent=base["Heading2"],
            fontName=FONT_NAME,
            fontSize=14,
            leading=21,
            textColor=colors.HexColor("#0F7468"),
            spaceBefore=5 * mm,
            spaceAfter=2 * mm,
        ),
        "h3": ParagraphStyle(
            "H3Zh",
            parent=base["Heading3"],
            fontName=FONT_NAME,
            fontSize=12,
            leading=19,
            textColor=colors.HexColor("#24485A"),
            spaceBefore=3.5 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "BodyZh",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=18,
            textColor=colors.HexColor("#263D4B"),
            alignment=TA_LEFT,
            spaceAfter=2.5 * mm,
            wordWrap="CJK",
        ),
        "notice": ParagraphStyle(
            "NoticeZh",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=9,
            leading=15,
            textColor=colors.HexColor("#9B3A3A"),
            backColor=colors.HexColor("#FFF1F0"),
            borderColor=colors.HexColor("#E7A7A1"),
            borderWidth=0.6,
            borderPadding=7,
            spaceAfter=4 * mm,
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "TableZh",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=13,
            textColor=colors.HexColor("#263D4B"),
            wordWrap="CJK",
        ),
        "footer": ParagraphStyle(
            "FooterZh",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#71818B"),
            alignment=TA_CENTER,
        ),
    }


def inline_markup(value: str) -> str:
    value = html.escape(value.strip())
    value = re.sub(r"`([^`]+)`", r"<font color='#0F7468'>\1</font>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    return value


def parse_table(lines: list[str], start: int, table_style: ParagraphStyle):
    rows: list[list[Paragraph]] = []
    index = start
    while index < len(lines) and lines[index].strip().startswith("|"):
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            rows.append([Paragraph(inline_markup(cell), table_style) for cell in cells])
        index += 1
    col_count = max(len(row) for row in rows)
    for row in rows:
        row.extend([Paragraph("", table_style)] * (col_count - len(row)))
    widths = [(170 * mm) / col_count] * col_count
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F3F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#123A3A")),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#BED3D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table, index


def markdown_to_story(markdown: str, doc_label: str, style_map: dict[str, ParagraphStyle]):
    lines = markdown.splitlines()
    story = [Paragraph(inline_markup(doc_label), style_map["title"])]
    index = 0
    paragraph_buffer: list[str] = []
    list_buffer: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            text = "".join(item.strip() for item in paragraph_buffer)
            story.append(Paragraph(inline_markup(text), style_map["body"]))
            paragraph_buffer.clear()

    def flush_list() -> None:
        if list_buffer:
            items = [
                ListItem(Paragraph(inline_markup(item), style_map["body"]), leftIndent=3 * mm)
                for item in list_buffer
            ]
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=8 * mm,
                    bulletFontName=FONT_NAME,
                    bulletFontSize=7,
                    spaceAfter=2.5 * mm,
                )
            )
            list_buffer.clear()

    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_list()
            index += 1
            continue
        if line.startswith("# "):
            index += 1
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[3:]), style_map["h2"]))
            index += 1
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[4:]), style_map["h3"]))
            index += 1
            continue
        if line.startswith("> "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[2:]), style_map["notice"]))
            index += 1
            continue
        if line.startswith("|"):
            flush_paragraph()
            flush_list()
            table, index = parse_table(lines, index, style_map["table"])
            story.extend([table, Spacer(1, 3 * mm)])
            continue
        bullet_match = re.match(r"(?:[-*]|\d+\.)\s+(.*)", line)
        if bullet_match:
            flush_paragraph()
            list_buffer.append(bullet_match.group(1))
            index += 1
            continue
        flush_list()
        paragraph_buffer.append(raw)
        index += 1

    flush_paragraph()
    flush_list()
    return story


def draw_page(canvas, doc, doc_label: str) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#BED3D0"))
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, height - 17 * mm, width - 20 * mm, height - 17 * mm)
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#71818B"))
    canvas.drawString(20 * mm, height - 13 * mm, "法制研究平台｜虛構測試卷證")
    canvas.drawRightString(width - 20 * mm, 12 * mm, f"第 {doc.page} 頁")
    canvas.setFillColor(colors.HexColor("#B24343"))
    canvas.drawString(20 * mm, 12 * mm, "非真實案件 · 僅供系統測試")
    canvas.restoreState()


def build_pdf(source: Path, destination: Path, doc_label: str, style_map) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=24 * mm,
        bottomMargin=20 * mm,
        title=doc_label,
        author="法制研究平台測試資料",
        subject="虛構訴願案件卷證",
    )
    story = markdown_to_story(source.read_text(encoding="utf-8"), doc_label, style_map)
    doc.build(
        story,
        onFirstPage=lambda canvas, current_doc: draw_page(canvas, current_doc, doc_label),
        onLaterPages=lambda canvas, current_doc: draw_page(canvas, current_doc, doc_label),
    )


def main() -> None:
    register_fonts()
    style_map = styles()
    for source_name, output_name, label in DOCUMENTS:
        source = SOURCE_DIR / source_name
        if not source.exists():
            raise FileNotFoundError(source)
        destination = OUTPUT_DIR / output_name
        build_pdf(source, destination, label, style_map)
        print(destination)


if __name__ == "__main__":
    main()
