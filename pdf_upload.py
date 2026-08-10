"""
pdf_upload.py — Résumé d'un PDF uploadé par l'utilisateur
Extrait le texte et le traite comme un article standard
"""

import io
from search import Paper

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


def extract_text_from_upload(file_bytes: bytes, filename: str) -> str:
    """Extrait le texte d'un PDF uploadé."""
    if PYMUPDF_AVAILABLE:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(pages)
    elif PYPDF_AVAILABLE:
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
    else:
        raise ImportError("Install pymupdf or pypdf: pip install pymupdf")


def pdf_to_paper(file_bytes: bytes, filename: str) -> Paper:
    """
    Convertit un PDF uploadé en objet Paper.
    Extrait titre, auteurs et abstract de façon heuristique.
    """
    text = extract_text_from_upload(file_bytes, filename)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Heuristique : titre = première ligne non-vide significative
    title = filename.replace(".pdf", "").replace("_", " ")
    for line in lines[:10]:
        if len(line) > 20 and not line.startswith("http"):
            title = line
            break

    # Abstract : chercher le mot "abstract" dans les premières lignes
    abstract = ""
    text_lower = text.lower()
    abs_idx = text_lower.find("abstract")
    if abs_idx != -1:
        intro_idx = text_lower.find("introduction", abs_idx)
        if intro_idx != -1 and intro_idx - abs_idx < 3000:
            abstract = text[abs_idx + 8: intro_idx].strip()
        else:
            abstract = text[abs_idx + 8: abs_idx + 2000].strip()

    # Fallback : premières 1500 chars utiles
    if len(abstract) < 100:
        abstract = " ".join(lines[1:20])[:1500]

    return Paper(
        title=title,
        authors=[],
        abstract=abstract,
        year=None,
        url=None,
        source="Uploaded PDF",
    ), text  # retourne aussi le texte complet pour usage futur