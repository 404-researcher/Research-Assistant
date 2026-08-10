"""
excel_export.py — Export Excel du rapport de recherche
Génère un fichier .xlsx avec deux feuilles :
  1. Summary — tableau récapitulatif des articles
  2. Report  — rapport de synthèse structuré
"""

import io
from datetime import datetime

try:
    import openpyxl
    from openpyxl.styles import (
        Font, PatternFill, Alignment, Border, Side
    )
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

from search import Paper
from summarizer import PaperSummary, ResearchReport


# ── Couleurs ──────────────────────────────────────────────────────────────────

NAVY   = "0F2942"
BLUE   = "1D6FA4"
GREEN  = "1A7A4A"
ORANGE = "C05C00"
RED    = "B91C1C"
LGRAY  = "F1F5F9"
WHITE  = "FFFFFF"


def _hex(color):
    return PatternFill("solid", fgColor=color)

def _font(bold=False, color="000000", size=10):
    return Font(bold=bold, color=color, size=size)

def _border():
    thin = Side(style="thin", color="CBD5E1")
    return Border(left=thin, right=thin, top=thin, bottom=thin)

def _wrap():
    return Alignment(wrap_text=True, vertical="top")


def generate_excel(
    query: str,
    papers_and_summaries: list[tuple[Paper, PaperSummary]],
    report: ResearchReport,
) -> bytes:
    if not OPENPYXL_AVAILABLE:
        raise ImportError("openpyxl not installed. Run: pip install openpyxl")

    wb = openpyxl.Workbook()

    # ── Feuille 1 : Articles ──────────────────────────────────────────────────

    ws1 = wb.active
    ws1.title = "Articles"

    headers = ["#", "Title", "Authors", "Year", "Source",
               "Relevance Score", "Summary", "Key Points", "Relevance Reason", "URL"]
    col_widths = [4, 40, 25, 6, 18, 8, 50, 40, 30, 35]

    # En-tête
    for col, (header, width) in enumerate(zip(headers, col_widths), 1):
        cell = ws1.cell(row=1, column=col, value=header)
        cell.font = _font(bold=True, color=WHITE, size=10)
        cell.fill = _hex(NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _border()
        ws1.column_dimensions[get_column_letter(col)].width = width

    ws1.row_dimensions[1].height = 22

    # Données
    for i, (paper, summary) in enumerate(papers_and_summaries, 1):
        row = i + 1
        score = summary.relevance_score
        score_color = GREEN if score >= 7 else ORANGE if score >= 4 else RED

        values = [
            i,
            paper.title,
            ", ".join(paper.authors[:4]) + (" et al." if len(paper.authors) > 4 else ""),
            paper.year or "",
            paper.source,
            f"{score}/10",
            summary.summary,
            "\n".join(f"• {p}" for p in summary.key_points),
            summary.relevance_reason,
            paper.url or "",
        ]

        bg = LGRAY if i % 2 == 0 else WHITE

        for col, value in enumerate(values, 1):
            cell = ws1.cell(row=row, column=col, value=value)
            cell.border = _border()
            cell.alignment = _wrap()
            cell.font = _font(size=9)

            if col == 1:  # numéro
                cell.alignment = Alignment(horizontal="center", vertical="top")
                cell.fill = _hex(bg)
            elif col == 6:  # score
                cell.fill = _hex(score_color)
                cell.font = _font(bold=True, color=WHITE, size=9)
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col == 10 and paper.url:  # URL cliquable
                ws1.cell(row=row, column=col).hyperlink = paper.url
                ws1.cell(row=row, column=col).font = Font(
                    color="1D6FA4", underline="single", size=9)
                ws1.cell(row=row, column=col).fill = _hex(bg)
            else:
                cell.fill = _hex(bg)

        ws1.row_dimensions[row].height = max(40, 15 * len(summary.key_points))

    # Freeze header
    ws1.freeze_panes = "A2"

    # ── Feuille 2 : Report ────────────────────────────────────────────────────

    ws2 = wb.create_sheet("Report")
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 80

    def section(label, values, row_start):
        # En-tête section
        ws2.merge_cells(f"A{row_start}:B{row_start}")
        cell = ws2.cell(row=row_start, column=1, value=label)
        cell.font = _font(bold=True, color=WHITE, size=11)
        cell.fill = _hex(BLUE)
        cell.alignment = Alignment(horizontal="left", vertical="center",
                                   indent=1)
        ws2.row_dimensions[row_start].height = 20
        row_start += 1

        if isinstance(values, list):
            for v in values:
                cell_a = ws2.cell(row=row_start, column=1, value="•")
                cell_a.alignment = Alignment(horizontal="center")
                cell_a.fill = _hex(LGRAY)
                cell_b = ws2.cell(row=row_start, column=2, value=v)
                cell_b.alignment = _wrap()
                cell_b.font = _font(size=10)
                cell_b.fill = _hex(LGRAY)
                ws2.row_dimensions[row_start].height = 30
                row_start += 1
        else:
            cell = ws2.cell(row=row_start, column=1, value=values)
            ws2.merge_cells(f"A{row_start}:B{row_start}")
            cell.alignment = _wrap()
            cell.font = _font(size=10)
            ws2.row_dimensions[row_start].height = 60
            row_start += 1

        return row_start + 1  # blank line

    # Titre
    ws2.merge_cells("A1:B1")
    title_cell = ws2.cell(row=1, column=1,
                          value=f"Research Report: {query}")
    title_cell.font = _font(bold=True, color=WHITE, size=13)
    title_cell.fill = _hex(NAVY)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 28

    ws2.merge_cells("A2:B2")
    date_cell = ws2.cell(row=2, column=1,
                         value=f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Articles: {len(papers_and_summaries)}")
    date_cell.font = _font(size=9, color="6B7280")
    date_cell.alignment = Alignment(horizontal="center")
    ws2.row_dimensions[2].height = 16

    row = 4
    row = section("Introduction",    report.introduction,  row)
    row = section("Main Themes",     report.main_themes,   row)
    row = section("Key Findings",    report.key_findings,  row)
    row = section("Consensus",       report.consensus,     row)
    row = section("Identified Gaps", report.gaps,          row)
    row = section("Conclusion",      report.conclusion,    row)

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()