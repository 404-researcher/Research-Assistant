"""
query_translation.py — Traduction de requête et filtre de langue
v1 — Traduit la requête en anglais via le LLM local (Ollama/OpenAI/Anthropic)
     et détecte/filtre la langue des articles retournés
"""

import os
import re
from typing import Optional

try:
    from langdetect import detect, LangDetectException
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

from search import Paper


# ── Langues supportées pour le filtre ────────────────────────────────────────

LANGUAGE_OPTIONS = {
    "Any language":     None,
    "English":          "en",
    "French":           "fr",
    "German":           "de",
    "Spanish":          "es",
    "Italian":          "it",
    "Portuguese":       "pt",
    "Chinese":          "zh-cn",
    "Japanese":         "ja",
    "Korean":           "ko",
    "Arabic":           "ar",
    "Russian":          "ru",
}

LANG_CODE_TO_LABEL = {v: k for k, v in LANGUAGE_OPTIONS.items() if v}

# Langues proposées pour la rédaction des résumés/rapport (pas de "Any language" ici,
# il faut toujours une langue cible précise pour le LLM).
SUMMARY_LANGUAGE_OPTIONS = {k: v for k, v in LANGUAGE_OPTIONS.items() if v}


# ── Détection de langue ───────────────────────────────────────────────────────

def detect_language(text: str) -> Optional[str]:
    """Détecte la langue d'un texte. Retourne le code ISO 639-1 ou None."""
    if not LANGDETECT_AVAILABLE or not text or len(text) < 20:
        return None
    try:
        return detect(text[:500])
    except Exception:
        return None


def filter_by_language(papers: list[Paper], lang_code: Optional[str]) -> list[Paper]:
    """
    Filtre les articles par langue détectée (titre + abstract, pour un texte plus
    long donc une détection plus fiable que sur l'abstract seul).
    Si lang_code est None, retourne tous les articles.
    Retourne la liste réellement filtrée, y compris vide : le caller décide quoi
    afficher plutôt que de silencieusement remontrer des articles dans la mauvaise langue.
    """
    if not lang_code:
        return papers

    filtered = []
    for paper in papers:
        text = f"{paper.title} {paper.abstract}".strip()
        detected = detect_language(text)
        # Si on ne peut pas détecter → on garde l'article (bénéfice du doute)
        if detected is None or detected == lang_code:
            filtered.append(paper)

    return filtered


# ── Traduction de requête ─────────────────────────────────────────────────────

def translate_query_to_english(
    query: str,
    provider_choice: str,
    model_name: Optional[str] = None,
) -> str:
    """
    Traduit la requête en anglais via le LLM configuré.
    Si la requête est déjà en anglais, la retourne telle quelle.
    Fallback : retourne la requête originale en cas d'erreur.
    """
    # Détecter si déjà en anglais
    detected_lang = detect_language(query)
    if detected_lang == "en":
        return query

    prompt = (
        f"Translate the following scientific search query to English. "
        f"Return ONLY the translated query, nothing else. "
        f"Preserve technical terms and proper nouns.\n\n"
        f"Query: {query}\n\n"
        f"English translation:"
    )

    try:
        if provider_choice == "ollama":
            from openai import OpenAI
            import httpx
            from summarizer import get_model, LLMProvider as _LLMProvider

            http_client = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0))
            client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama", http_client=http_client)

            # Traduction = simple texte libre (pas de JSON à respecter) → on utilise
            # un petit modèle rapide dédié. S'il n'est pas installé (`ollama pull ...`),
            # on retombe automatiquement sur le modèle principal.
            fast_model = model_name or get_model(_LLMProvider.OLLAMA, "translate")
            fallback_model = get_model(_LLMProvider.OLLAMA, "analyze")

            def _call(model_to_use):
                return client.chat.completions.create(
                    model=model_to_use,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.1,
                )

            try:
                response = _call(fast_model)
            except Exception as fast_err:
                if fast_model == fallback_model:
                    raise
                print(f"[Translation] Fast model '{fast_model}' unavailable ({fast_err}) — falling back to '{fallback_model}'")
                response = _call(fallback_model)

            translated = response.choices[0].message.content.strip()

        elif provider_choice == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            model = model_name or "gpt-4o-mini"
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.1,
            )
            translated = response.choices[0].message.content.strip()

        elif provider_choice == "mistral":
            from openai import OpenAI
            client = OpenAI(base_url="https://api.mistral.ai/v1", api_key=os.getenv("MISTRAL_API_KEY"))
            model = model_name or "mistral-small-latest"
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.1,
            )
            translated = response.choices[0].message.content.strip()

        elif provider_choice == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            model = model_name or "claude-3-5-haiku-20241022"
            response = client.messages.create(
                model=model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            translated = response.content[0].text.strip()

        else:
            return query

        # Nettoyage : enlever guillemets ou préfixes parasites
        translated = re.sub(r'^["\']|["\']$', '', translated).strip()
        translated = re.sub(r'^(Translation:|English:|Query:)\s*', '', translated, flags=re.I).strip()

        return translated if translated else query

    except Exception as e:
        print(f"[Translation] Error: {e} — using original query")
        return query