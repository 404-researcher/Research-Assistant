"""
summarizer.py — Résumé par article + Synthèse globale via LLM
Fix : max_tokens ajouté pour Ollama/Mistral, imports sans suffixes de version
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Callable, Optional, Union
from pydantic import BaseModel, Field, field_validator
import instructor
from instructor import Mode
from dotenv import load_dotenv

from search import Paper

load_dotenv()


# ── Provider ──────────────────────────────────────────────────────────────────

class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


# Modèle utilisé par tâche et par provider.
# Ollama : un seul modèle (mistral) pour les trois tâches. Testé en comparatif
# (14 requêtes, 5 langues) : llama3.2:1b échoue sur 50% des traductions
# (2 échecs complets), donc pas assez fiable pour un usage par défaut. mistral
# étant déjà chargé pour l'analyse/le rapport, l'utiliser aussi pour traduire
# évite un changement de modèle en cours de recherche — sans perte de vitesse
# réelle une fois le modèle chaud en mémoire (~0.2s/traduction dans les deux cas).
TASK_MODELS = {
    LLMProvider.OLLAMA: {
        "translate": "mistral",
        "analyze":   "mistral",
        "report":    "mistral",
    },
    LLMProvider.OPENAI: {
        "translate": "gpt-4o-mini",
        "analyze":   "gpt-4o-mini",
        "report":    "gpt-4o-mini",
    },
    LLMProvider.ANTHROPIC: {
        "translate": "claude-3-5-haiku-20241022",
        "analyze":   "claude-3-5-haiku-20241022",
        "report":    "claude-3-5-haiku-20241022",
    },
}

# Conservé pour compatibilité : modèle "principal" (tâche d'analyse) par provider.
DEFAULT_MODELS = {provider: tasks["analyze"] for provider, tasks in TASK_MODELS.items()}


def get_model(provider: LLMProvider, task: str = "analyze") -> str:
    """Modèle à utiliser pour une tâche donnée (translate / analyze / report)."""
    return TASK_MODELS[provider].get(task, DEFAULT_MODELS[provider])


def get_client(provider: LLMProvider):
    if provider == LLMProvider.OPENAI:
        from openai import OpenAI
        return instructor.from_openai(OpenAI(api_key=os.getenv("OPENAI_API_KEY")))

    elif provider == LLMProvider.ANTHROPIC:
        from anthropic import Anthropic
        return instructor.from_anthropic(Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")))

    elif provider == LLMProvider.OLLAMA:
        from openai import OpenAI
        import httpx
        # httpx timeout explicite — contourne le bug de timeout Ollama 0.24.x
        http_client = httpx.Client(timeout=httpx.Timeout(
            connect=10.0,
            read=180.0,   # RTX 5060 GPU rapide mais on laisse de la marge
            write=10.0,
            pool=10.0,
        ))
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            http_client=http_client,
        )
        return instructor.from_openai(client, mode=Mode.JSON)

    raise ValueError(f"Provider inconnu : {provider}")


# ── Schémas Pydantic ──────────────────────────────────────────────────────────

class PaperSummary(BaseModel):
    title: str = Field(description="Titre de l'article")
    summary: str = Field(description="Résumé en 2-3 phrases simples")
    key_points: list[str] = Field(description="3 points clés de l'article")
    relevance_score: int = Field(
        description="Score de pertinence par rapport à la requête (0-10)",
        ge=0, le=10
    )
    relevance_reason: str = Field(description="Pourquoi cet article est (ou non) pertinent")

    @field_validator("summary", "title", "relevance_reason", mode="before")
    @classmethod
    def coerce_to_string(cls, v):
        """Mistral retourne parfois une liste au lieu d'une string — on la joint."""
        if isinstance(v, list):
            return " ".join(str(item) for item in v)
        return v


class ResearchReport(BaseModel):
    introduction: str = Field(description="Introduction générale sur le sujet (3-4 phrases)")
    main_themes: list[str] = Field(description="Grands thèmes identifiés dans les articles")
    key_findings: list[str] = Field(description="Découvertes et résultats importants")
    consensus: str = Field(description="Ce sur quoi les articles s'accordent")
    gaps: list[str] = Field(description="Lacunes ou questions ouvertes dans la littérature")
    conclusion: str = Field(description="Conclusion générale (2-3 phrases)")

    @field_validator("introduction", "consensus", "conclusion", mode="before")
    @classmethod
    def coerce_to_string(cls, v):
        if isinstance(v, list):
            return " ".join(str(item) for item in v)
        return v


# ── Appel LLM unifié avec retry ───────────────────────────────────────────────

def call_llm(client, provider: LLMProvider, model: str, system: str,
             prompt: str, response_model, max_retries: int = 3):
    """Appel LLM avec retry sur ConnectionError. Délai progressif : 3s, 6s, 12s."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                wait = 3 * (2 ** (attempt - 1))
                print(f"[LLM] Retry {attempt}/{max_retries-1} in {wait}s...")
                time.sleep(wait)

            if provider == LLMProvider.ANTHROPIC:
                return client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=response_model,
                )
            else:
                # OpenAI et Ollama/Mistral — max_tokens obligatoire sinon timeout
                return client.chat.completions.create(
                    model=model,
                    max_tokens=1024,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    response_model=response_model,
                )
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if "validation" in err_str or "credit" in err_str or "401" in err_str:
                raise e
            print(f"[LLM] Error attempt {attempt+1}: {e}")
    raise last_exc


# ── Fonctions principales ─────────────────────────────────────────────────────

def summarize_paper(
    paper: Paper, query: str, provider: LLMProvider, model: Optional[str] = None,
    summary_lang: str = "English",
) -> PaperSummary:
    client = get_client(provider)
    model = model or get_model(provider, "analyze")

    prompt = f"""Analyze the scientific article below.
DO NOT invent information. Base your response ONLY on the provided text.
IMPORTANT: Write your ENTIRE response in {summary_lang}, regardless of the article's original language.

=== ARTICLE ===
Title: {paper.title}
Authors: {', '.join(paper.authors[:5])}
Year: {paper.year or 'unknown'}
Abstract: {paper.abstract}
=== END ARTICLE ===

User search query: "{query}"

Respond ONLY with this JSON (no text before or after):
{{
  "title": "<exact title of the article>",
  "summary": "<4-5 sentence summary in {summary_lang}, covering the main objective, methodology, and results>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "relevance_score": <integer between 0 and 10>,
  "relevance_reason": "<short explanation of relevance to the query, in {summary_lang}>"
}}"""

    system = (
        "You are a rigorous scientific research assistant. "
        "You analyze only the provided content, without inventing information. "
        f"You ALWAYS write in {summary_lang}, even if the article is in another language. "
        "You ALWAYS respond with a complete and valid JSON, no additional text outside the JSON."
    )

    return call_llm(client, provider, model, system, prompt, PaperSummary)


def summarize_papers(
    papers: list[Paper],
    query: str,
    provider: LLMProvider,
    model: Optional[str] = None,
    max_workers: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
    summary_lang: str = "English",
) -> list[Union[PaperSummary, Exception]]:
    """
    Résume une liste d'articles, dans l'ordre d'entrée.
    - OpenAI / Anthropic : appels parallélisés (ThreadPoolExecutor) — ces APIs cloud
      gèrent bien la concurrence, gain net sur le temps total.
    - Ollama : reste séquentiel. Un seul GPU local génère un token à la fois ;
      paralléliser les requêtes HTTP ne réduit pas le temps de calcul réel et ne
      ferait qu'ajouter de la contention. Le seul délai artificiel (sleep entre
      appels) a été retiré, ce qui suffit à accélérer ce cas.
    Les erreurs par article sont capturées (pas levées) pour ne pas interrompre les autres.
    """
    total = len(papers)
    results: list = [None] * total

    if provider == LLMProvider.OLLAMA:
        for i, paper in enumerate(papers):
            try:
                results[i] = summarize_paper(paper, query, provider, model, summary_lang=summary_lang)
            except Exception as e:
                results[i] = e
            if on_progress:
                on_progress(i + 1, total)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(summarize_paper, paper, query, provider, model, summary_lang=summary_lang): i
            for i, paper in enumerate(papers)
        }
        done = 0
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            try:
                results[i] = future.result()
            except Exception as e:
                results[i] = e
            done += 1
            if on_progress:
                on_progress(done, total)

    return results


class UploadedPaperSummary(BaseModel):
    """Schéma étendu pour les PDFs uploadés — résumé plus long et plus détaillé."""
    title: str = Field(description="Title of the article")
    summary: str = Field(description="Detailed summary of the article, 8-10 sentences")
    methodology: str = Field(description="Description of the methodology used, 3-4 sentences")
    key_points: list[str] = Field(description="6-8 key points, each a complete sentence")
    results: list[str] = Field(description="3-5 main results or findings")
    relevance_score: int = Field(description="Relevance score 0-10", ge=0, le=10)
    relevance_reason: str = Field(description="Detailed explanation of relevance, 3-4 sentences")
    limitations: list[str] = Field(default_factory=list, description="2-3 limitations of the study")

    @field_validator("summary", "methodology", "relevance_reason", mode="before")
    @classmethod
    def coerce_to_string(cls, v):
        if isinstance(v, list):
            return " ".join(str(item) for item in v)
        return v


def summarize_uploaded_pdf(
    paper: Paper,
    full_text: str,
    query: str,
    provider: LLMProvider,
    model: Optional[str] = None,
    summary_lang: str = "English",
) -> UploadedPaperSummary:
    """
    Analyse approfondie d'un PDF uploadé.
    Utilise le texte complet (pas juste l'abstract) pour un résumé bien plus détaillé.
    """
    client = get_client(provider)
    model = model or get_model(provider, "analyze")

    # On envoie le texte complet mais tronqué pour ne pas dépasser le contexte
    # ~6000 chars couvre titre + abstract + intro + conclusion dans la plupart des articles
    text_for_analysis = full_text[:6000] if len(full_text) > 6000 else full_text

    prompt = f"""You are an expert scientific reviewer. Analyze the full text of this article in depth.
DO NOT invent information. Base your response ONLY on the provided text.
Write your ENTIRE response in {summary_lang}.

=== ARTICLE FULL TEXT ===
Title: {paper.title}
Authors: {', '.join(paper.authors[:5]) if paper.authors else 'Unknown'}
Year: {paper.year or 'unknown'}

{text_for_analysis}
=== END ARTICLE ===

User research query: "{query}"

Provide a thorough analysis. Respond ONLY with this JSON (no text before or after):
{{
  "title": "<exact title of the article>",
  "summary": "<detailed summary of 8-10 sentences covering: research question, context, methodology, main findings, and conclusions>",
  "methodology": "<3-4 sentences describing the research methodology, experimental design, or theoretical approach used>",
  "key_points": [
    "<key point 1 — complete sentence>",
    "<key point 2 — complete sentence>",
    "<key point 3 — complete sentence>",
    "<key point 4 — complete sentence>",
    "<key point 5 — complete sentence>",
    "<key point 6 — complete sentence>"
  ],
  "results": [
    "<main result or finding 1>",
    "<main result or finding 2>",
    "<main result or finding 3>"
  ],
  "relevance_score": <integer between 0 and 10>,
  "relevance_reason": "<3-4 sentences explaining in detail how this article relates to the query '{query}', what it contributes, and why it is or is not relevant>",
  "limitations": [
    "<limitation 1>",
    "<limitation 2>"
  ]
}}"""

    system = (
        "You are an expert scientific reviewer with deep knowledge across multiple disciplines. "
        "You provide thorough, accurate analyses based strictly on the provided text. "
        f"You ALWAYS write in {summary_lang} and ALWAYS respond with a complete and valid JSON."
    )

    if provider == LLMProvider.ANTHROPIC:
        return client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            response_model=UploadedPaperSummary,
        )
    else:
        return client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_model=UploadedPaperSummary,
            max_tokens=2048,
        )


def generate_report(
    summaries: list[PaperSummary], query: str, provider: LLMProvider, model: Optional[str] = None,
    summary_lang: str = "English",
) -> ResearchReport:
    client = get_client(provider)
    model = model or get_model(provider, "report")

    summaries_text = "\n\n".join([
        f"- Title: {s.title}\n"
        f"  Summary: {s.summary}\n"
        f"  Key points: {' | '.join(s.key_points)}\n"
        f"  Relevance: {s.relevance_score}/10"
        for s in summaries
    ])

    prompt = f"""Synthesize the following articles on the topic: "{query}"

CRITICAL: You MUST write your entire report in {summary_lang}. Never use any other language.

=== ARTICLES ===
{summaries_text}
=== END ARTICLES ===

Respond ONLY with this JSON (no text before or after), all values in {summary_lang}:
{{
  "introduction": "<general introduction on the topic, 4-5 sentences>",
  "main_themes": ["<theme 1>", "<theme 2>", "<theme 3>"],
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "consensus": "<what the articles agree on, 2-3 sentences>",
  "gaps": ["<gap 1>", "<gap 2>"],
  "conclusion": "<general conclusion, 3-4 sentences>"
}}"""

    system = (
        "You are an expert researcher in bibliographic synthesis. "
        f"You MUST write exclusively in {summary_lang}. Never use any other language. "
        "You ALWAYS respond with a complete and valid JSON, no additional text."
    )

    return call_llm(client, provider, model, system, prompt, ResearchReport)