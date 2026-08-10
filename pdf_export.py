"""
Module d'export PDF du rapport de recherche
Utilise reportlab pour générer un PDF structuré et lisible
"""

import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

from summarizer import PaperSummary, ResearchReport
from search import Paper


# ── Couleurs ──────────────────────────────────────────────────────────────────

BLUE_DARK  = colors.HexColor("#1a3a5c")
BLUE_MID   = colors.HexColor("#2563eb")
BLUE_LIGHT = colors.HexColor("#dbeafe")
GRAY_LIGHT = colors.HexColor("#f1f5f9")
GRAY_MID   = colors.HexColor("#94a3b8")
GREEN      = colors.HexColor("#16a34a")
ORANGE     = colors.HexColor("#ea580c")
RED        = colors.HexColor("#dc2626")
WHITE      = colors.white
BLACK      = colors.black


# ── Styles ────────────────────────────────────────────────────────────────────

def build_styles():
    base = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            fontSize=24, fontName="Helvetica-Bold",
            textColor=BLUE_DARK, spaceAfter=6,
            alignment=TA_CENTER,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            fontSize=11, fontName="Helvetica",
            textColor=GRAY_MID, spaceAfter=20,
            alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "SectionHeader",
            fontSize=14, fontName="Helvetica-Bold",
            textColor=BLUE_DARK, spaceBefore=18, spaceAfter=8,
            borderPad=4,
        ),
        "subsection": ParagraphStyle(
            "SubSection",
            fontSize=11, fontName="Helvetica-Bold",
            textColor=BLUE_MID, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body",
            fontSize=10, fontName="Helvetica",
            textColor=BLACK, spaceAfter=6,
            alignment=TA_JUSTIFY, leading=14,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontSize=10, fontName="Helvetica",
            textColor=BLACK, spaceAfter=3,
            leftIndent=14, leading=13,
        ),
        "paper_title": ParagraphStyle(
            "PaperTitle",
            fontSize=11, fontName="Helvetica-Bold",
            textColor=BLUE_DARK, spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "Meta",
            fontSize=9, fontName="Helvetica",
            textColor=GRAY_MID, spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "Small",
            fontSize=9, fontName="Helvetica-Oblique",
            textColor=GRAY_MID, spaceAfter=6,
        ),
    }
    return styles


# ── Score badge ───────────────────────────────────────────────────────────────

def score_color(score: int):
    if score >= 7:
        return GREEN
    elif score >= 4:
        return ORANGE
    return RED


def score_label(score: int) -> str:
    if score >= 7:
        return "Pertinent"
    elif score >= 4:
        return "Moyen"
    return "Hors sujet"


# ── Génération PDF ────────────────────────────────────────────────────────────

def generate_pdf(
    query: str,
    papers_and_summaries: list[tuple[Paper, PaperSummary]],
    report: ResearchReport,
) -> bytes:
    """
    Génère le rapport complet en PDF et retourne les bytes.
    Compatible avec st.download_button.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )

    styles = build_styles()
    story = []

    # ── Page de titre ─────────────────────────────────────────────────────────

    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("Research Assistant", styles["title"]))
    story.append(Paragraph("Rapport de synthèse automatique", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE_MID, spaceAfter=12))

    # Infos rapport
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    nb_articles = len(papers_and_summaries)
    meta_data = [
        ["Requête", query],
        ["Date", date_str],
        ["Articles analysés", str(nb_articles)],
        ["Source", papers_and_summaries[0][0].source if papers_and_summaries else "—"],
    ]
    meta_table = Table(meta_data, colWidths=[4*cm, 12*cm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), BLUE_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), BLUE_DARK),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, GRAY_LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, GRAY_MID),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, GRAY_MID),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(meta_table)
    story.append(PageBreak())

    # ── Rapport de synthèse ───────────────────────────────────────────────────

    story.append(Paragraph("Rapport de synthese", styles["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BLUE_LIGHT, spaceAfter=8))

    story.append(Paragraph("Introduction", styles["subsection"]))
    story.append(Paragraph(report.introduction, styles["body"]))

    story.append(Paragraph("Themes principaux", styles["subsection"]))
    for theme in report.main_themes:
        story.append(Paragraph(f"- {theme}", styles["bullet"]))

    story.append(Paragraph("Decouvertes cles", styles["subsection"]))
    for finding in report.key_findings:
        story.append(Paragraph(f"- {finding}", styles["bullet"]))

    story.append(Paragraph("Consensus", styles["subsection"]))
    story.append(Paragraph(report.consensus, styles["body"]))

    story.append(Paragraph("Lacunes identifiees", styles["subsection"]))
    for gap in report.gaps:
        story.append(Paragraph(f"- {gap}", styles["bullet"]))

    story.append(Paragraph("Conclusion", styles["subsection"]))
    story.append(Paragraph(report.conclusion, styles["body"]))

    story.append(PageBreak())

    # ── Articles analysés ─────────────────────────────────────────────────────

    story.append(Paragraph("Articles analyses", styles["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BLUE_LIGHT, spaceAfter=8))

    for i, (paper, summary) in enumerate(papers_and_summaries, 1):

        # En-tête article : titre + badge score
        score_data = [[
            Paragraph(f"{i}. {paper.title}", styles["paper_title"]),
            Paragraph(
                f"<font color='white'><b>{summary.relevance_score}/10</b></font>",
                ParagraphStyle("badge", fontSize=10, fontName="Helvetica-Bold",
                               alignment=TA_CENTER, textColor=WHITE)
            ),
        ]]
        score_tbl = Table(score_data, colWidths=[13.5*cm, 2.5*cm])
        score_tbl.setStyle(TableStyle([
            ("BACKGROUND", (1, 0), (1, 0), score_color(summary.relevance_score)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(score_tbl)

        # Métadonnées
        authors_str = ", ".join(paper.authors[:4]) + (" et al." if len(paper.authors) > 4 else "")
        meta_str = f"{authors_str} | {paper.year or 'Annee inconnue'} | {paper.source}"
        story.append(Paragraph(meta_str, styles["meta"]))

        # Résumé
        story.append(Paragraph(summary.summary, styles["body"]))

        # Points clés
        if summary.key_points:
            for point in summary.key_points:
                story.append(Paragraph(f"- {point}", styles["bullet"]))

        # Pertinence
        story.append(Paragraph(
            f"Pertinence : {summary.relevance_reason}",
            styles["small"]
        ))

        # Lien
        if paper.url:
            story.append(Paragraph(
                f'<link href="{paper.url}">{paper.url}</link>',
                styles["small"]
            ))

        story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_LIGHT, spaceAfter=6))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()