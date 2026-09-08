"""
Research Assistant — Interface Streamlit
v24 — Imports flexibles (compatible avec ou sans suffixes de version)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io
import json
import socket
import zipfile
from pathlib import Path
import streamlit as st

from search import search_papers, ALL_SOURCE_NAMES
from summarizer import (
    summarize_papers, summarize_uploaded_pdf, generate_report, LLMProvider, get_model,
)
from pdf_export import generate_pdf
from history import add_search, get_history, delete_entry, clear_history, get_file_bytes, get_file_text
from citation import generate_citations
from excel_export import generate_excel, OPENPYXL_AVAILABLE
from pdf_upload import pdf_to_paper, PYMUPDF_AVAILABLE, PYPDF_AVAILABLE
from query_translation import (
    translate_query_to_english, filter_by_language,
    LANGUAGE_OPTIONS, SUMMARY_LANGUAGE_OPTIONS, LANG_CODE_TO_LABEL,
    detect_language, LANGDETECT_AVAILABLE
)

PREFS_FILE = Path(__file__).parent / "preferences.json"

OLLAMA_HOST, OLLAMA_PORT = "localhost", 11434


def is_ollama_running(timeout: float = 0.5) -> bool:
    """Ping rapide du port Ollama — évite d'attendre les multiples retries d'un
    appel LLM (jusqu'à ~20s par article) juste pour découvrir qu'Ollama est éteint."""
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=timeout):
            return True
    except OSError:
        return False

# ── Config page ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Research Assistant",
    page_icon="🔬",
    layout="wide",
)

# ── Session state — persiste les résultats entre les reruns Streamlit ─────────

if "search_results" not in st.session_state:
    st.session_state.search_results = None   # dict avec summaries, report, fichiers
if "compare_results" not in st.session_state:
    st.session_state.compare_results = None  # dict avec résultats A et B
if "pending_translation" not in st.session_state:
    st.session_state.pending_translation = None  # traduction en attente de validation
if "translation_review_id" not in st.session_state:
    st.session_state.translation_review_id = 0  # force le reset du champ éditable à chaque nouvelle traduction

SOURCE_ICONS = {
    "arXiv": "📚", "Semantic Scholar": "🎓", "CrossRef": "🌐",
    "OpenAlex": "🔭", "CORE": "🔓", "DOAJ": "📖",
    "Zenodo": "⚛️", "OSF Preprints": "🔬", "BASE": "🗄️",
    "PubMed": "🏥", "NIH / PubMed Central": "🧬", "Europe PMC": "🇪🇺",
    "bioRxiv/medRxiv": "🧪", "PLOS": "🌿", "PeerJ": "👥",
    "DBLP": "💻", "RePEC (Economics)": "📈",
    "HAL (CNRS)": "🇫🇷", "PERSÉE": "📜", "OpenEdition": "📰",
    "SciELO": "🇧🇷", "Redalyc": "🇲🇽", "DIALNET": "🇪🇸",
    "e-Revistas (CSIC)": "🔖",
    "J-STAGE": "🇯🇵", "CiNii": "🏛️",
    "CyberLeninka (RU)": "🇷🇺",
    "KCI (Korea)": "🇰🇷",
    "NARCIS (NL)": "🇳🇱",
    "Uploaded PDF": "📄",
}

# ── Préférences ───────────────────────────────────────────────────────────────

def load_prefs():
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}

def save_prefs(prefs):
    PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False, indent=2))

# ── Export groupé ─────────────────────────────────────────────────────────────

def build_zip(filename_base, citations_text, citation_fmt, markdown_report, pdf_bytes, xl_bytes):
    """Empaquette citations + rapport (Markdown/PDF/Excel) dans un seul .zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cite_ext = "bib" if citation_fmt == "BibTeX" else "txt"
        zf.writestr(f"citations_{filename_base}.{cite_ext}", citations_text)
        zf.writestr(f"report_{filename_base}.md", markdown_report)
        if pdf_bytes:
            zf.writestr(f"report_{filename_base}.pdf", pdf_bytes)
        if xl_bytes:
            zf.writestr(f"report_{filename_base}.xlsx", xl_bytes)
    return buf.getvalue()

prefs = load_prefs()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Parameters")

    provider_choice = st.selectbox(
        "LLM Model",
        options=["ollama", "openai", "anthropic", "mistral"],
        index=["ollama", "openai", "anthropic", "mistral"].index(prefs.get("provider", "ollama")),
        format_func=lambda x: {
            "ollama":    "🦙 Ollama (free, local)",
            "openai":    "🟢 OpenAI (paid)",
            "anthropic": "🟠 Anthropic (paid)",
            "mistral":   "🔶 Mistral (paid)",
        }[x]
    )
    provider = LLMProvider(provider_choice)
    ollama_model_analyze = get_model(LLMProvider.OLLAMA, "analyze")
    ollama_model_translate = get_model(LLMProvider.OLLAMA, "translate")
    ollama_running = None
    if provider == LLMProvider.OLLAMA:
        ollama_running = is_ollama_running()
        if ollama_running:
            st.success("🦙 Ollama is running")
        else:
            st.error("🦙 Ollama is not reachable.\n\nCommand: `ollama serve`")
        with st.expander("🦙 Ollama models per task", expanded=False):
            ollama_model_analyze = st.text_input(
                "Analysis & report model",
                value=prefs.get("ollama_model_analyze", ollama_model_analyze),
                help="Used for per-article scoring/summary and the final synthesis report. "
                     "Needs reliable JSON output — a mid-size model (e.g. mistral) works best."
            )
            ollama_model_translate = st.text_input(
                "Translation model",
                value=prefs.get("ollama_model_translate", ollama_model_translate),
                help="Used to translate your query to English. Defaults to the same model as "
                     "analysis (mistral) — tested more reliable than small models like llama3.2:1b "
                     "(50% failure rate across 14 queries in 5 languages) or qwen2.5:1.5b. "
                     "Falls back to the analysis model automatically if the model set here isn't installed."
            )

    source_choices = st.multiselect(
        "Search sources",
        options=ALL_SOURCE_NAMES,
        default=prefs.get("sources", ["arXiv"]),
        help="Select one or more sources."
    )
    if not source_choices:
        source_choices = ["arXiv"]

    max_results = st.slider("Number of articles", min_value=2, max_value=20,
                            value=prefs.get("max_results", 5))

    min_relevance = st.slider("Minimum relevance score", min_value=0, max_value=9,
                              value=prefs.get("min_relevance", 0),
                              help="Hide articles below this score")

    citation_fmt = st.selectbox("Citation format", ["APA", "BibTeX", "IEEE"],
                                index=["APA","BibTeX","IEEE"].index(prefs.get("citation_fmt","APA")))

    st.divider()
    st.markdown("**Language settings**")

    auto_translate = st.toggle(
        "Auto-translate query to English",
        value=prefs.get("auto_translate", True),
        help="Your query will be translated to English before searching — gives much better results for non-English queries"
    )

    article_lang = st.selectbox(
        "Filter articles by language",
        options=list(LANGUAGE_OPTIONS.keys()),
        index=0,
        help="Only show articles written in this language"
    )
    article_lang_code = LANGUAGE_OPTIONS[article_lang]

    if not LANGDETECT_AVAILABLE:
        st.caption("⚠️ Install langdetect for language detection:\n`pip install langdetect`")

    summary_lang = st.selectbox(
        "Summary & report language",
        options=list(SUMMARY_LANGUAGE_OPTIONS.keys()),
        index=list(SUMMARY_LANGUAGE_OPTIONS.keys()).index(prefs.get("summary_lang", "English"))
              if prefs.get("summary_lang", "English") in SUMMARY_LANGUAGE_OPTIONS else 0,
        help="Language the AI uses to write per-article summaries and the synthesis report"
    )

    st.divider()
    st.markdown("**Year filter**")
    use_year_filter = st.toggle("Enable filter", value=False)
    if use_year_filter:
        year_range = st.slider("Period", min_value=1990, max_value=2026,
                               value=(2015, 2026), step=1)
        year_min, year_max = year_range
        st.caption(f"Articles from {year_min} to {year_max}")
    else:
        year_min, year_max = None, None

    st.divider()
    if st.button("💾 Save preferences", use_container_width=True):
        save_prefs({
            "provider": provider_choice,
            "sources": source_choices,
            "max_results": max_results,
            "min_relevance": min_relevance,
            "citation_fmt": citation_fmt,
            "auto_translate": auto_translate,
            "article_lang": article_lang,
            "summary_lang": summary_lang,
            "ollama_model_analyze": ollama_model_analyze,
            "ollama_model_translate": ollama_model_translate,
        })
        st.success("Preferences saved!")

    st.divider()
    st.markdown("**Available sources (29)**")
    st.markdown("*🌍 General / Multidisciplinary*")
    st.markdown("- 📚 **arXiv** — CS, physics, maths")
    st.markdown("- 🎓 **Semantic Scholar** — all fields, 200M+")
    st.markdown("- 🌐 **CrossRef** — all fields, DOI")
    st.markdown("- 🔭 **OpenAlex** — 250M+ articles")
    st.markdown("- 🔓 **CORE** — open access, 200M+")
    st.markdown("- 📖 **DOAJ** — peer-reviewed OA journals")
    st.markdown("- ⚛️ **Zenodo** — CERN repository")
    st.markdown("- 🔬 **OSF Preprints** — social, psych, engineering")
    st.markdown("- 🗄️ **BASE** — 350M+ docs, Bielefeld")
    st.markdown("*🏥 Medicine / Biology*")
    st.markdown("- 🏥 **PubMed** — biomedical, 35M+")
    st.markdown("- 🧬 **NIH / PubMed Central** — full text OA")
    st.markdown("- 🇪🇺 **Europe PMC** — European biomedical")
    st.markdown("- 🧪 **bioRxiv/medRxiv** — preprints")
    st.markdown("- 🌿 **PLOS** — open access science")
    st.markdown("- 👥 **PeerJ** — biology, medicine, CS")
    st.markdown("*💻 Computer Science*")
    st.markdown("- 💻 **DBLP** — CS reference database")
    st.markdown("*📈 Economics*")
    st.markdown("- 📈 **RePEC** — economics papers")
    st.markdown("*🇫🇷 French*")
    st.markdown("- 🇫🇷 **HAL (CNRS)** — French open archive")
    st.markdown("- 📜 **PERSÉE** — French humanities")
    st.markdown("- 📰 **OpenEdition** — French SHS journals")
    st.markdown("*🇪🇸🇧🇷 Spanish / Portuguese*")
    st.markdown("- 🇧🇷 **SciELO** — Latin America ES/PT/EN")
    st.markdown("- 🇲🇽 **Redalyc** — Latin American OA")
    st.markdown("- 🇪🇸 **DIALNET** — largest Hispanic base")
    st.markdown("- 🔖 **e-Revistas (CSIC)** — Spanish CSIC")
    st.markdown("*🇯🇵 Japanese*")
    st.markdown("- 🇯🇵 **J-STAGE** — Japanese platform")
    st.markdown("- 🏛️ **CiNii** — NII Japan")
    st.markdown("*🇷🇺 Russian*")
    st.markdown("- 🇷🇺 **CyberLeninka** — Russian OA")
    st.markdown("*🇰🇷 Korean*")
    st.markdown("- 🇰🇷 **KCI** — Korea Citation Index")
    st.markdown("*🇳🇱 Dutch*")
    st.markdown("- 🇳🇱 **NARCIS** — Dutch research portal")


# ── Fonction principale d'affichage ──────────────────────────────────────────

def run_search_and_display(
    query, provider, provider_choice, source_choices,
    max_results, year_min, year_max,
    min_relevance=0, citation_fmt="APA",
    auto_translate=True, article_lang_code=None, summary_lang="English",
    save_to_history=True, key_prefix="",
    ollama_model_analyze=None, ollama_model_translate=None,
    pre_translated_query=None,
):
    analyze_model = ollama_model_analyze if provider == LLMProvider.OLLAMA else None
    translate_model = ollama_model_translate if provider == LLMProvider.OLLAMA else None

    # ── Traduction de la requête ──────────────────────────────────────────────
    # Si une traduction déjà validée (et potentiellement corrigée) par l'utilisateur
    # est fournie, on l'utilise directement sans retraduire.
    search_query = query
    translated_text = None
    if pre_translated_query is not None:
        search_query = pre_translated_query
        if pre_translated_query.strip().lower() != query.strip().lower():
            translated_text = pre_translated_query
    elif auto_translate:
        with st.spinner("🌐 Translating query to English..."):
            translated_query = translate_query_to_english(query, provider_choice, translate_model)
        if translated_query.lower().strip() != query.lower().strip():
            search_query = translated_query
            translated_text = search_query

    with st.spinner(f"Searching on {', '.join(source_choices)}..."):
        try:
            papers = search_papers(search_query, max_results=max_results,
                                   sources=source_choices,
                                   year_min=year_min, year_max=year_max)
        except Exception as e:
            st.error(f"Search error: {e}")
            return None

    if not papers:
        st.warning("No articles found. Try another query or source.")
        return None

    if article_lang_code:
        papers_filtered_lang = filter_by_language(papers, article_lang_code)
        nb_excluded = len(papers) - len(papers_filtered_lang)
        if nb_excluded:
            st.info(f"🌍 Language filter: {nb_excluded} article(s) excluded (not in "
                    f"{LANG_CODE_TO_LABEL.get(article_lang_code, article_lang_code)})")
        papers = papers_filtered_lang
        if not papers:
            st.warning(
                f"No article found in {LANG_CODE_TO_LABEL.get(article_lang_code, article_lang_code)}. "
                f"Try another language, broaden the sources, or disable the filter."
            )
            return None

    summaries = []
    progress = st.progress(0, text="Analyzing articles...")
    with st.spinner(f"Analyzing {len(papers)} article(s)..."):
        results = summarize_papers(
            papers, query, provider, model=analyze_model, summary_lang=summary_lang,
            on_progress=lambda done, total: progress.progress(
                done / total, text=f"Article {done}/{total} analyzed"
            ),
        )
    for paper, result in zip(papers, results):
        if isinstance(result, Exception):
            st.warning(f"Could not analyze '{paper.title[:50]}...': {result}")
        else:
            summaries.append((paper, result))
    progress.empty()

    if not summaries:
        st.error("No article could be analyzed.")
        return None

    summaries.sort(key=lambda x: x[1].relevance_score, reverse=True)
    filtered = [(p, s) for p, s in summaries if s.relevance_score >= min_relevance]

    # Si on n'a pas assez d'articles après le filtre, on avertit et on complète
    # avec les meilleurs articles restants pour atteindre le quota demandé
    if len(filtered) < max_results and min_relevance > 0:
        rejected = [(p, s) for p, s in summaries if s.relevance_score < min_relevance]
        needed = max_results - len(filtered)
        if rejected:
            st.info(
                f"⚠️ Only **{len(filtered)}** article(s) scored ≥ {min_relevance}/10. "
                f"Adding the **{min(needed, len(rejected))}** best remaining articles to reach {min(max_results, len(filtered)+len(rejected))} results. "
                f"Lower the minimum relevance score or add more sources to get strictly {max_results} articles at this threshold."
            )
            filtered = filtered + rejected[:needed]

    if not filtered:
        filtered = summaries

    paper_summaries = [s for _, s in filtered]
    with st.spinner("Generating synthesis report..."):
        try:
            report = generate_report(paper_summaries, query, provider, model=analyze_model, summary_lang=summary_lang)
        except Exception as e:
            st.error(f"Report generation error: {e}")
            return None

    citations_text = generate_citations(filtered, citation_fmt)
    filename_base  = query[:30].replace(" ", "_")

    markdown_report = f"""# Research Report: {query}

## Introduction
{report.introduction}

## Main Themes
{chr(10).join(f"- {t}" for t in report.main_themes)}

## Key Findings
{chr(10).join(f"- {f}" for f in report.key_findings)}

## Consensus
{report.consensus}

## Identified Gaps
{chr(10).join(f"- {g}" for g in report.gaps)}

## Conclusion
{report.conclusion}

---
## References ({citation_fmt})
{citations_text}

---
## Articles Analyzed
{chr(10).join(f"### {p.title} ({p.source}, {p.year or '?'}){chr(10)}{s.summary}{chr(10)}" for p, s in filtered)}
"""

    pdf_bytes = None
    xl_bytes  = None
    try:
        pdf_bytes = generate_pdf(query, filtered, report)
    except Exception:
        pass
    try:
        if OPENPYXL_AVAILABLE:
            xl_bytes = generate_excel(query, filtered, report)
    except Exception:
        pass

    if save_to_history:
        try:
            add_search(
                query=query, source=", ".join(source_choices),
                nb_articles=len(filtered), provider=provider_choice,
                report_intro=report.introduction,
                top_papers=[{"title": p.title, "score": s.relevance_score,
                             "url": p.url or "", "source": p.source}
                            for p, s in filtered[:5]],
                files={"pdf": pdf_bytes, "excel": xl_bytes,
                       "markdown": markdown_report, "citations": citations_text},
            )
        except Exception:
            pass

    # Stocker dans session_state pour persistance après rerun
    return {
        "query":           query,
        "translated_text": translated_text,
        "source_choices":  source_choices,
        "year_min":        year_min,
        "year_max":        year_max,
        "article_lang":    article_lang_code,
        "filtered":        filtered,
        "report":          report,
        "citations_text":  citations_text,
        "markdown_report": markdown_report,
        "pdf_bytes":       pdf_bytes,
        "xl_bytes":        xl_bytes,
        "filename_base":   filename_base,
        "citation_fmt":    citation_fmt,
        "min_relevance":   min_relevance,
    }


def display_results(data: dict, key_prefix: str = ""):
    """Affiche les résultats stockés dans session_state — sans relancer la recherche."""
    if data is None:
        return

    filtered       = data["filtered"]
    report         = data["report"]
    citations_text = data["citations_text"]
    markdown_report= data["markdown_report"]
    pdf_bytes      = data["pdf_bytes"]
    xl_bytes       = data["xl_bytes"]
    filename_base  = data["filename_base"]
    citation_fmt   = data["citation_fmt"]
    min_relevance  = data["min_relevance"]

    # Bandeau de traduction
    if data.get("translated_text"):
        st.info(f"🌐 Query translated to English: **\"{data['translated_text']}\"**")

    filter_str = f" (period {data['year_min']}–{data['year_max']})" if (data.get("year_min") or data.get("year_max")) else ""
    lang_str   = f" · {data['article_lang']}" if data.get("article_lang") else ""
    nb_hidden  = 0
    st.success(f"✅ {len(filtered)} articles displayed via {', '.join(data['source_choices'])}{filter_str}{lang_str}")

    # ── Articles ──────────────────────────────────────────────────────────────
    st.subheader(f"📄 Articles analyzed ({len(filtered)})")
    for paper, summary in filtered:
        color    = "🟢" if summary.relevance_score >= 7 else "🟡" if summary.relevance_score >= 4 else "🔴"
        src_icon = SOURCE_ICONS.get(paper.source, "📄")
        nb_sources = len(paper.duplicate_sources)
        dup_badge  = f" · found in {nb_sources} databases" if nb_sources > 1 else ""
        with st.expander(
            f"{color} [{summary.relevance_score}/10] {paper.title}{dup_badge}",
            expanded=summary.relevance_score >= 7,
        ):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.markdown("**Summary**")
                st.write(summary.summary)
                st.markdown("**Key points**")
                for point in summary.key_points:
                    st.markdown(f"- {point}")
                st.markdown(f"*Relevance: {summary.relevance_reason}*")
            with col_b:
                st.markdown("**Source**")
                st.markdown(f"{src_icon} **{paper.source}**")
                if nb_sources > 1:
                    others = [s for s in paper.duplicate_sources if s != paper.source]
                    st.caption(f"📚 Also found in: {', '.join(others)}")
                if paper.authors:
                    st.markdown("**Authors**")
                    st.caption(", ".join(paper.authors[:3]))
                if paper.year:
                    st.markdown(f"**Year**: {paper.year}")
                if paper.url:
                    st.link_button("📎 Read article", paper.url)

    # ── Rapport ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Research Report")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Introduction")
        st.write(report.introduction)
        st.markdown("#### Main Themes")
        for t in report.main_themes: st.markdown(f"- {t}")
        st.markdown("#### Key Findings")
        for f in report.key_findings: st.markdown(f"- {f}")
    with col2:
        st.markdown("#### Consensus")
        st.write(report.consensus)
        st.markdown("#### Identified Gaps")
        for g in report.gaps: st.markdown(f"- {g}")
        st.markdown("#### Conclusion")
        st.write(report.conclusion)

    # ── Citations ─────────────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"📝 Citations ({citation_fmt})")
    st.code(citations_text, language="text")
    st.download_button(
        label=f"⬇️ Download citations",
        data=citations_text,
        file_name=f"citations_{filename_base}.{'bib' if citation_fmt=='BibTeX' else 'txt'}",
        mime="text/plain",
        use_container_width=True,
        key=f"{key_prefix}_cite",
    )

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("⬇️ Download Report")

    zip_bytes = build_zip(filename_base, citations_text, citation_fmt, markdown_report, pdf_bytes, xl_bytes)
    st.download_button(
        label="📦 Download All (ZIP)",
        data=zip_bytes,
        file_name=f"research_{filename_base}.zip",
        mime="application/zip",
        use_container_width=True,
        key=f"{key_prefix}_zip",
        type="primary",
    )

    col_md, col_pdf, col_xl = st.columns(3)

    with col_md:
        st.download_button(
            label="📄 Markdown", data=markdown_report,
            file_name=f"report_{filename_base}.md", mime="text/markdown",
            use_container_width=True, key=f"{key_prefix}_md",
        )
    with col_pdf:
        if pdf_bytes:
            st.download_button(
                label="📕 PDF", data=pdf_bytes,
                file_name=f"report_{filename_base}.pdf", mime="application/pdf",
                use_container_width=True, key=f"{key_prefix}_pdf",
            )
        else:
            st.caption("PDF generation failed")
    with col_xl:
        if xl_bytes:
            st.download_button(
                label="📊 Excel", data=xl_bytes,
                file_name=f"report_{filename_base}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key=f"{key_prefix}_xl",
            )
        elif not OPENPYXL_AVAILABLE:
            st.caption("Install openpyxl:\n`pip install openpyxl`")



# ── Alerte Ollama (haut de page, visible sur tous les onglets) ────────────────

if provider == LLMProvider.OLLAMA and ollama_running is False:
    st.error(
        "🦙 **Ollama is not running.** Start it with `ollama serve` in a terminal, "
        "then reload this page — or switch to OpenAI/Anthropic in the sidebar."
    )

# ── Navigation ────────────────────────────────────────────────────────────────

tab_search, tab_upload, tab_compare, tab_history = st.tabs(
    ["🔍 Search", "📄 Upload PDF", "⚖️ Compare", "🕘 History"]
)


# ══════════════════════════════════════════════════════════════════════════════
# ONGLET 1 — RECHERCHE
# ══════════════════════════════════════════════════════════════════════════════

with tab_search:
    st.title("🔬 Research Assistant")
    st.caption("Automatic search and synthesis of scientific articles")

    col1, col2 = st.columns([4, 1])
    with col1:
        query = st.text_input("Search topic",
                              placeholder="ex: transformer architecture NLP",
                              label_visibility="collapsed")
    with col2:
        search_btn = st.button("🔍 Search", use_container_width=True, type="primary")

    # Nouvelle recherche → si auto-traduction, on s'arrête d'abord sur une étape
    # de relecture (la traduction sémantique peut dériver silencieusement) avant
    # de lancer la recherche proprement dite.
    if search_btn and query:
        st.session_state.search_results = None
        if auto_translate:
            translate_model = ollama_model_translate if provider == LLMProvider.OLLAMA else None
            with st.spinner("🌐 Translating query to English..."):
                translated = translate_query_to_english(query, provider_choice, translate_model)
            if translated.strip().lower() != query.strip().lower():
                st.session_state.translation_review_id += 1
                st.session_state.pending_translation = {"original": query, "translated": translated}
            else:
                st.session_state.pending_translation = None
                result = run_search_and_display(
                    query, provider, provider_choice, source_choices,
                    max_results, year_min, year_max,
                    min_relevance=min_relevance, citation_fmt=citation_fmt,
                    auto_translate=auto_translate, article_lang_code=article_lang_code, summary_lang=summary_lang,
                    save_to_history=True, key_prefix="main",
                    ollama_model_analyze=ollama_model_analyze, ollama_model_translate=ollama_model_translate,
                    pre_translated_query=query,
                )
                if result:
                    st.session_state.search_results = result
        else:
            st.session_state.pending_translation = None
            result = run_search_and_display(
                query, provider, provider_choice, source_choices,
                max_results, year_min, year_max,
                min_relevance=min_relevance, citation_fmt=citation_fmt,
                auto_translate=auto_translate, article_lang_code=article_lang_code, summary_lang=summary_lang,
                save_to_history=True, key_prefix="main",
                ollama_model_analyze=ollama_model_analyze, ollama_model_translate=ollama_model_translate,
                pre_translated_query=query,
            )
            if result:
                st.session_state.search_results = result
    elif search_btn:
        st.warning("Please enter a search topic.")

    # ── Relecture de la traduction avant de lancer la recherche ───────────────
    if st.session_state.pending_translation:
        pending = st.session_state.pending_translation
        st.info(f"🌐 Original query: **\"{pending['original']}\"**")
        edited_query = st.text_input(
            "Translated query — edit if the translation looks off, then confirm",
            value=pending["translated"],
            key=f"translated_edit_{st.session_state.translation_review_id}",
        )
        col_confirm, col_cancel = st.columns([3, 1])
        with col_confirm:
            confirm_btn = st.button("✅ Confirm & Search", type="primary",
                                    use_container_width=True, key="confirm_translation")
        with col_cancel:
            cancel_btn = st.button("✖ Cancel", use_container_width=True, key="cancel_translation")

        if confirm_btn:
            original_query = pending["original"]
            st.session_state.pending_translation = None
            result = run_search_and_display(
                original_query, provider, provider_choice, source_choices,
                max_results, year_min, year_max,
                min_relevance=min_relevance, citation_fmt=citation_fmt,
                auto_translate=auto_translate, article_lang_code=article_lang_code, summary_lang=summary_lang,
                save_to_history=True, key_prefix="main",
                ollama_model_analyze=ollama_model_analyze, ollama_model_translate=ollama_model_translate,
                pre_translated_query=edited_query,
            )
            if result:
                st.session_state.search_results = result
                display_results(result, key_prefix="main")
        elif cancel_btn:
            st.session_state.pending_translation = None
            st.rerun()

    # Rerun suivant (ex: clic téléchargement) → on réaffiche depuis session_state
    elif st.session_state.search_results:
        display_results(st.session_state.search_results, key_prefix="main")


# ══════════════════════════════════════════════════════════════════════════════
# ONGLET 2 — UPLOAD PDF
# ══════════════════════════════════════════════════════════════════════════════

with tab_upload:
    st.title("📄 Analyze a PDF")
    st.caption("Upload a scientific article PDF to get an AI-powered summary, key points and citation")

    if not (PYMUPDF_AVAILABLE or PYPDF_AVAILABLE):
        st.error("📦 Missing dependency. Run in terminal:\n```\npip install pymupdf\n```")
        st.stop()

    uploaded = st.file_uploader("Drop your PDF here", type=["pdf"],
                                help="The PDF is processed locally — nothing is sent to external servers except the extracted text to the LLM.")

    if uploaded is not None:
        file_bytes = uploaded.read()

        # Extraction automatique dès l'upload
        with st.spinner("📖 Extracting text from PDF..."):
            try:
                paper, full_text = pdf_to_paper(file_bytes, uploaded.name)
            except Exception as e:
                st.error(f"Extraction error: {e}")
                st.stop()

        # Afficher les infos extraites et laisser l'utilisateur les corriger
        st.success(f"✅ PDF loaded — {len(full_text):,} characters extracted from **{uploaded.name}**")

        with st.expander("📋 Extracted metadata (click to edit)", expanded=True):
            col_t, col_y = st.columns([4, 1])
            with col_t:
                paper.title = st.text_input(
                    "Title", value=paper.title,
                    help="Auto-detected — correct if needed"
                )
            with col_y:
                year_input = st.text_input("Year", value="", placeholder="e.g. 2024")
                if year_input.strip().isdigit():
                    paper.year = int(year_input.strip())

            authors_input = st.text_input(
                "Authors (comma separated)",
                value=", ".join(paper.authors) if paper.authors else "",
                help="Auto-detected — correct if needed"
            )
            paper.authors = [a.strip() for a in authors_input.split(",") if a.strip()]

            st.markdown("**Abstract preview** (used for analysis)")
            abstract_edit = st.text_area(
                "Abstract", value=paper.abstract, height=150,
                help="Auto-extracted — edit if the extraction missed something"
            )
            paper.abstract = abstract_edit

        # Requête de recherche pour scorer la pertinence
        query_pdf = st.text_input(
            "🔍 Your research topic (for relevance scoring)",
            placeholder="ex: bionic hand prosthesis neural control",
            help="Helps the AI evaluate how relevant this article is to your research"
        )

        analyze_btn = st.button("🤖 Analyze with AI", type="primary", use_container_width=True)

        if analyze_btn:
            if len(paper.abstract) < 50:
                st.error("The extracted abstract is too short. Please paste the abstract manually in the field above.")
                st.stop()

            effective_query = query_pdf if query_pdf.strip() else paper.title

            with st.spinner("🤖 AI is performing a deep analysis of the article..."):
                try:
                    upload_model = ollama_model_analyze if provider == LLMProvider.OLLAMA else None
                    summary = summarize_uploaded_pdf(paper, full_text, effective_query, provider, model=upload_model, summary_lang=summary_lang)
                except Exception as e:
                    st.error(f"Analysis error: {e}")
                    st.stop()

            # ── Résultats étendus ─────────────────────────────────────────────
            color = "🟢" if summary.relevance_score >= 7 else "🟡" if summary.relevance_score >= 4 else "🔴"
            st.divider()
            st.markdown(f"## {color} Deep Analysis Result")

            col_score, col_source = st.columns([1, 3])
            with col_score:
                st.metric("Relevance Score", f"{summary.relevance_score}/10")
            with col_source:
                meta_parts = []
                if paper.authors:
                    meta_parts.append(", ".join(paper.authors[:3]))
                if paper.year:
                    meta_parts.append(str(paper.year))
                meta_parts.append("Uploaded PDF")
                st.caption(" | ".join(meta_parts))

            # Summary détaillé
            st.markdown("### 📝 Summary")
            st.write(summary.summary)

            # Méthodologie
            if summary.methodology:
                st.markdown("### 🔬 Methodology")
                st.write(summary.methodology)

            # Key points
            st.markdown("### 🎯 Key Points")
            for point in summary.key_points:
                st.markdown(f"- {point}")

            # Résultats
            if summary.results:
                st.markdown("### 📊 Main Results")
                for result in summary.results:
                    st.markdown(f"- {result}")

            # Limitations
            if summary.limitations:
                st.markdown("### ⚠️ Limitations")
                for lim in summary.limitations:
                    st.markdown(f"- {lim}")

            # Pertinence
            st.markdown("### 🔗 Relevance to your query")
            st.info(summary.relevance_reason)

            # Citation (on réutilise PaperSummary pour la fonction generate_citations)
            from summarizer import PaperSummary
            paper_summary_compat = PaperSummary(
                title=summary.title,
                summary=summary.summary,
                key_points=summary.key_points,
                relevance_score=summary.relevance_score,
                relevance_reason=summary.relevance_reason,
            )
            st.divider()
            st.markdown(f"### 📎 Citation ({citation_fmt})")
            cite = generate_citations([(paper, paper_summary_compat)], citation_fmt)
            st.code(cite, language="text")
            st.download_button(
                label=f"⬇️ Download citation",
                data=cite,
                file_name=f"citation_{paper.title[:20].replace(' ','_')}.{'bib' if citation_fmt=='BibTeX' else 'txt'}",
                mime="text/plain",
            )


# ══════════════════════════════════════════════════════════════════════════════
# ONGLET 3 — COMPARAISON
# ══════════════════════════════════════════════════════════════════════════════

with tab_compare:
    st.title("⚖️ Compare Two Queries")
    st.caption("Run two searches side by side and compare results")

    col_q1, col_q2 = st.columns(2)
    with col_q1:
        query_a = st.text_input("Query A", placeholder="ex: deep learning image classification",
                                key="cmp_qa")
    with col_q2:
        query_b = st.text_input("Query B", placeholder="ex: transformer vision models",
                                key="cmp_qb")

    compare_btn = st.button("⚖️ Compare", type="primary", use_container_width=True)

    if compare_btn and query_a and query_b:
        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown(f"### 🅰 {query_a}")
            st.divider()
            res_a = run_search_and_display(
                query_a, provider, provider_choice, source_choices,
                max_results, year_min, year_max,
                min_relevance=min_relevance, citation_fmt=citation_fmt,
                auto_translate=auto_translate, article_lang_code=article_lang_code, summary_lang=summary_lang,
                save_to_history=True, key_prefix="cmp_a",
                ollama_model_analyze=ollama_model_analyze, ollama_model_translate=ollama_model_translate,
            )
            if res_a:
                display_results(res_a, key_prefix="cmp_a")
        with col_right:
            st.markdown(f"### 🅱 {query_b}")
            st.divider()
            res_b = run_search_and_display(
                query_b, provider, provider_choice, source_choices,
                max_results, year_min, year_max,
                min_relevance=min_relevance, citation_fmt=citation_fmt,
                auto_translate=auto_translate, article_lang_code=article_lang_code, summary_lang=summary_lang,
                save_to_history=True, key_prefix="cmp_b",
                ollama_model_analyze=ollama_model_analyze, ollama_model_translate=ollama_model_translate,
            )
            if res_b:
                display_results(res_b, key_prefix="cmp_b")
        st.session_state.compare_results = {"a": res_a, "b": res_b,
                                            "query_a": query_a, "query_b": query_b}

    elif compare_btn:
        st.warning("Please enter both queries.")

    # Rerun suivant → réafficher depuis session_state
    elif st.session_state.compare_results:
        cmp = st.session_state.compare_results
        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown(f"### 🅰 {cmp['query_a']}")
            st.divider()
            display_results(cmp["a"], key_prefix="cmp_a")
        with col_right:
            st.markdown(f"### 🅱 {cmp['query_b']}")
            st.divider()
            display_results(cmp["b"], key_prefix="cmp_b")


# ══════════════════════════════════════════════════════════════════════════════
# ONGLET 4 — HISTORIQUE
# ══════════════════════════════════════════════════════════════════════════════

with tab_history:
    st.title("🕘 Search History")

    history = get_history()

    if not history:
        st.info("No searches recorded yet. Run a search in the 🔍 Search tab!")
    else:
        col_title, col_clear = st.columns([5, 1])
        with col_title:
            st.caption(f"{len(history)} search(es) recorded")
        with col_clear:
            if st.button("🗑️ Clear all", type="secondary"):
                clear_history()
                st.rerun()

        st.divider()

        for entry in history:
            saved_files = entry.get("files", {})
            file_icons = ""
            if saved_files.get("pdf"):      file_icons += " 📕"
            if saved_files.get("excel"):    file_icons += " 📊"
            if saved_files.get("markdown"): file_icons += " 📄"
            if saved_files.get("citations"):file_icons += " 📝"

            with st.expander(
                f"🔍 **{entry['query']}** — {entry['date']}  ·  "
                f"{entry['nb_articles']} articles  ·  {entry['source']}{file_icons}",
                expanded=False,
            ):
                st.markdown("**Synthesis summary**")
                st.write(entry.get("report_intro", "—"))

                top = entry.get("top_papers", [])
                if top:
                    st.markdown("**Most relevant articles**")
                    for p in top:
                        score    = p.get("score", "?")
                        color    = "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"
                        title    = p.get("title", "—")
                        url      = p.get("url", "")
                        source   = p.get("source", "")
                        src_icon = SOURCE_ICONS.get(source, "📄")
                        label    = f"{color} [{score}/10] {src_icon} {source} — {title}"
                        st.markdown(f"[{label}]({url})" if url else label)

                # ── Téléchargements sauvegardés ───────────────────────────────
                if saved_files:
                    st.markdown("**📥 Download saved files**")
                    dl_cols = st.columns(4)

                    if saved_files.get("pdf"):
                        data = get_file_bytes(saved_files["pdf"])
                        if data:
                            with dl_cols[0]:
                                st.download_button(
                                    "📕 PDF", data=data,
                                    file_name=f"report_{entry['id']}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True,
                                    key=f"hist_pdf_{entry['id']}",
                                )

                    if saved_files.get("excel"):
                        data = get_file_bytes(saved_files["excel"])
                        if data:
                            with dl_cols[1]:
                                st.download_button(
                                    "📊 Excel", data=data,
                                    file_name=f"report_{entry['id']}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    use_container_width=True,
                                    key=f"hist_xl_{entry['id']}",
                                )

                    if saved_files.get("markdown"):
                        data = get_file_text(saved_files["markdown"])
                        if data:
                            with dl_cols[2]:
                                st.download_button(
                                    "📄 Markdown", data=data,
                                    file_name=f"report_{entry['id']}.md",
                                    mime="text/markdown",
                                    use_container_width=True,
                                    key=f"hist_md_{entry['id']}",
                                )

                    if saved_files.get("citations"):
                        data = get_file_text(saved_files["citations"])
                        if data:
                            with dl_cols[3]:
                                st.download_button(
                                    "📝 Citations", data=data,
                                    file_name=f"citations_{entry['id']}.txt",
                                    mime="text/plain",
                                    use_container_width=True,
                                    key=f"hist_cite_{entry['id']}",
                                )

                col_meta, col_del = st.columns([4, 1])
                with col_meta:
                    st.caption(
                        f"Provider: {entry.get('provider','?')}  |  "
                        f"Sources: {entry.get('source','?')}  |  "
                        f"ID: {entry.get('id','?')}"
                    )
                with col_del:
                    if st.button("🗑️ Delete", key=f"del_{entry['id']}"):
                        delete_entry(entry["id"])
                        st.rerun()