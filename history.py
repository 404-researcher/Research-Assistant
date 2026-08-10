"""
Module d'historique des recherches
v2 — Sauvegarde aussi les fichiers générés (PDF, Excel, Markdown, citations)
"""

import json
import base64
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "history.json"
FILES_DIR    = Path(__file__).parent / "history_files"
MAX_ENTRIES  = 50


def _empty_history() -> dict:
    return {"searches": []}


def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return _empty_history()
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _empty_history()


def save_history(data: dict) -> None:
    HISTORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── API publique ──────────────────────────────────────────────────────────────

def add_search(
    query: str,
    source: str,
    nb_articles: int,
    provider: str,
    report_intro: str,
    top_papers: list[dict],
    files: dict | None = None,  # {"pdf": bytes, "excel": bytes, "markdown": str, "citations": str}
) -> str:
    """Ajoute une recherche à l'historique. Retourne l'ID de l'entrée."""
    FILES_DIR.mkdir(exist_ok=True)

    entry_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Sauvegarde des fichiers
    saved_files = {}
    if files:
        entry_dir = FILES_DIR / entry_id
        entry_dir.mkdir(exist_ok=True)
        safe_query = query[:25].replace(" ", "_").replace("/", "_")

        if files.get("pdf"):
            path = entry_dir / f"report_{safe_query}.pdf"
            path.write_bytes(files["pdf"])
            saved_files["pdf"] = str(path)

        if files.get("excel"):
            path = entry_dir / f"report_{safe_query}.xlsx"
            path.write_bytes(files["excel"])
            saved_files["excel"] = str(path)

        if files.get("markdown"):
            path = entry_dir / f"report_{safe_query}.md"
            path.write_text(files["markdown"], encoding="utf-8")
            saved_files["markdown"] = str(path)

        if files.get("citations"):
            path = entry_dir / f"citations_{safe_query}.txt"
            path.write_text(files["citations"], encoding="utf-8")
            saved_files["citations"] = str(path)

    data = load_history()
    entry = {
        "id":           entry_id,
        "date":         datetime.now().strftime("%d/%m/%Y %H:%M"),
        "query":        query,
        "source":       source,
        "nb_articles":  nb_articles,
        "provider":     provider,
        "report_intro": report_intro[:400] + "..." if len(report_intro) > 400 else report_intro,
        "top_papers":   top_papers[:5],
        "files":        saved_files,
    }

    data["searches"].insert(0, entry)
    data["searches"] = data["searches"][:MAX_ENTRIES]
    save_history(data)
    return entry_id


def get_history() -> list[dict]:
    return load_history().get("searches", [])


def delete_entry(entry_id: str) -> None:
    """Supprime une entrée et ses fichiers associés."""
    data = load_history()
    entry = next((s for s in data["searches"] if s.get("id") == entry_id), None)

    # Supprimer les fichiers
    if entry and entry.get("files"):
        entry_dir = FILES_DIR / entry_id
        if entry_dir.exists():
            import shutil
            shutil.rmtree(entry_dir, ignore_errors=True)

    data["searches"] = [s for s in data["searches"] if s.get("id") != entry_id]
    save_history(data)


def clear_history() -> None:
    """Vide tout l'historique et tous les fichiers."""
    import shutil
    if FILES_DIR.exists():
        shutil.rmtree(FILES_DIR, ignore_errors=True)
    save_history(_empty_history())


def get_file_bytes(filepath: str) -> bytes | None:
    """Lit un fichier sauvegardé et retourne ses bytes."""
    try:
        return Path(filepath).read_bytes()
    except Exception:
        return None


def get_file_text(filepath: str) -> str | None:
    """Lit un fichier texte sauvegardé."""
    try:
        return Path(filepath).read_text(encoding="utf-8")
    except Exception:
        return None