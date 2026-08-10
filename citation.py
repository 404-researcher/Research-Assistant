"""
citation.py — Génération automatique de citations scientifiques
Formats supportés : APA, BibTeX, IEEE
"""

from search import Paper


def _clean(text: str) -> str:
    return (text or "").strip()


def to_apa(paper: Paper) -> str:
    """Format APA 7e édition."""
    authors = paper.authors[:6]
    if not authors:
        author_str = "Unknown Author"
    elif len(authors) == 1:
        parts = authors[0].rsplit(" ", 1)
        author_str = f"{parts[-1]}, {parts[0][0]}." if len(parts) > 1 else authors[0]
    else:
        formatted = []
        for a in authors:
            parts = a.rsplit(" ", 1)
            formatted.append(f"{parts[-1]}, {parts[0][0]}." if len(parts) > 1 else a)
        if len(paper.authors) > 6:
            author_str = ", ".join(formatted) + ", ... et al."
        else:
            author_str = ", ".join(formatted[:-1]) + f", & {formatted[-1]}"

    year = f"({paper.year})" if paper.year else "(n.d.)"
    title = _clean(paper.title)
    source = _clean(paper.source)
    url_part = f" {paper.url}" if paper.url else ""

    return f"{author_str} {year}. {title}. *{source}*.{url_part}"


def to_bibtex(paper: Paper) -> str:
    """Format BibTeX."""
    # Clé : premier auteur + année
    first_author = (paper.authors[0].rsplit(" ", 1)[-1] if paper.authors else "unknown").lower()
    first_author = "".join(c for c in first_author if c.isalnum())
    year = paper.year or "0000"
    key = f"{first_author}{year}"

    # Auteurs
    authors_bib = " and ".join(paper.authors[:6]) if paper.authors else "Unknown"
    if len(paper.authors) > 6:
        authors_bib += " and others"

    lines = [
        f"@article{{{key},",
        f"  author  = {{{authors_bib}}},",
        f"  title   = {{{_clean(paper.title)}}},",
        f"  year    = {{{year}}},",
        f"  journal = {{{_clean(paper.source)}}},",
    ]
    if paper.url:
        lines.append(f"  url     = {{{paper.url}}},")
    lines.append("}")
    return "\n".join(lines)


def to_ieee(paper: Paper) -> str:
    """Format IEEE."""
    authors = paper.authors[:6]
    if not authors:
        author_str = "Unknown"
    else:
        formatted = []
        for a in authors:
            parts = a.rsplit(" ", 1)
            if len(parts) > 1:
                formatted.append(f"{parts[0][0]}. {parts[1]}")
            else:
                formatted.append(a)
        author_str = ", ".join(formatted)
        if len(paper.authors) > 6:
            author_str += " et al."

    year = paper.year or "n.d."
    title = _clean(paper.title)
    source = _clean(paper.source)
    url_part = f". [Online]. Available: {paper.url}" if paper.url else ""

    return f'{author_str}, "{title}," *{source}*, {year}{url_part}.'


def generate_citations(papers_and_summaries, fmt: str) -> str:
    """Génère toutes les citations dans le format demandé."""
    lines = []
    for i, (paper, _) in enumerate(papers_and_summaries, 1):
        if fmt == "APA":
            lines.append(f"{i}. {to_apa(paper)}")
        elif fmt == "BibTeX":
            lines.append(to_bibtex(paper))
            lines.append("")
        elif fmt == "IEEE":
            lines.append(f"[{i}] {to_ieee(paper)}")
    return "\n".join(lines)