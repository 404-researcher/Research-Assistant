"""
Étape 2 — Connexion aux APIs de recherche scientifique
v3 — Multi-sources, nouvelles sources (PubMed, CrossRef, CORE), fix rate limit arXiv
"""

import threading
import time
import re
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import zip_longest
from pydantic import BaseModel, Field
from typing import Optional


class Paper(BaseModel):
    title: str = Field(description="Title of the article")
    authors: list[str] = Field(description="List of authors")
    abstract: str = Field(description="Abstract of the article")
    year: Optional[int] = Field(default=None)
    url: Optional[str] = Field(default=None)
    source: str = Field(description="Source name")
    duplicate_sources: list[str] = Field(
        default_factory=list,
        description="Autres sources ayant aussi retourné cet article (dédoublonnage inter-sources)",
    )


HEADERS = {"User-Agent": "ResearchAssistant/1.0 (educational project)"}


# ── Rate limiting réel (au lieu de sleeps fixes avant chaque appel) ────────────
# On n'attend que le temps réellement manquant depuis le dernier appel à CETTE
# source, par thread-safety un lock global. Comme les sources tournent maintenant
# en parallèle (voir search_papers), ceci reste nécessaire par-source mais ne
# bloque plus les autres sources entre elles.

_rate_lock = threading.Lock()
_last_call_time: dict = {}


def _rate_limit(key: str, min_interval: float):
    with _rate_lock:
        now = time.time()
        wait = min_interval - (now - _last_call_time.get(key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call_time[key] = time.time()


def _get_with_retry(url, params, headers=HEADERS, timeout=60, max_retries=3, base_wait=5):
    """Requête HTTP avec retry automatique sur erreur 429."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 429:
                wait = base_wait * (2 ** attempt)  # 5s, 10s, 20s
                print(f"Rate limit 429 — attente {wait}s avant retry {attempt+1}/{max_retries}")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                time.sleep(base_wait * (2 ** attempt))
            else:
                raise e
    raise Exception(f"Échec après {max_retries} tentatives")


# ── arXiv ─────────────────────────────────────────────────────────────────────

def search_arxiv(query, max_results=5, year_min=None, year_max=None):
    fetch_count = min(max_results * 2 if (year_min or year_max) else max_results, 25)
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": fetch_count,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    _rate_limit("arxiv", 3.5)  # respect arXiv rate limit (min 3s recommandé) — n'attend que si nécessaire
    response = _get_with_retry("https://export.arxiv.org/api/query", params=params, base_wait=6, timeout=60)

    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        title    = entry.find("atom:title", ns).text.strip().replace("\n", " ")
        abstract = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
        authors  = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]
        url      = entry.find("atom:id", ns).text.strip()
        pub      = entry.find("atom:published", ns)
        year     = int(pub.text[:4]) if pub is not None else None

        if year_min and year and year < year_min: continue
        if year_max and year and year > year_max: continue

        papers.append(Paper(title=title, authors=authors, abstract=abstract,
                            year=year, url=url, source="arXiv"))
        if len(papers) >= max_results:
            break
    return papers


# ── Semantic Scholar ──────────────────────────────────────────────────────────

def search_semantic_scholar(query, max_results=5, year_min=None, year_max=None):
    params = {"query": query, "limit": max_results,
              "fields": "title,authors,abstract,year,url"}
    if year_min or year_max:
        params["year"] = f"{year_min or ''}-{year_max or ''}"

    _rate_limit("semantic_scholar", 1.2)  # éviter le rate limit — n'attend que si nécessaire
    response = _get_with_retry(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params=params,
        base_wait=5,
    )
    papers = []
    for item in response.json().get("data", []):
        abstract = item.get("abstract") or ""
        if not abstract:
            continue
        papers.append(Paper(
            title=item.get("title", "Unknown title"),
            authors=[a["name"] for a in item.get("authors", [])],
            abstract=abstract, year=item.get("year"),
            url=item.get("url"), source="Semantic Scholar",
        ))
    return papers


# ── PubMed ────────────────────────────────────────────────────────────────────

def search_pubmed(query, max_results=5, year_min=None, year_max=None):
    search_params = {"db": "pubmed", "term": query,
                     "retmax": max_results, "retmode": "json", "sort": "relevance"}
    if year_min or year_max:
        search_params.update({"datetype": "pdat",
                               "mindate": str(year_min or 1900),
                               "maxdate": str(year_max or 2026)})
    r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                     params=search_params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    r2 = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                      params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
                      headers=HEADERS, timeout=30)
    r2.raise_for_status()
    root = ET.fromstring(r2.text)
    papers = []
    for article in root.findall(".//PubmedArticle"):
        title_el = article.find(".//ArticleTitle")
        title = (title_el.text or "Unknown title") if title_el is not None else "Unknown title"
        abstract = " ".join(
            (el.text or "") for el in article.findall(".//AbstractText") if el.text
        ).strip()
        if not abstract:
            continue
        authors = []
        for a in article.findall(".//Author"):
            last = a.find("LastName")
            first = a.find("ForeName")
            if last is not None:
                authors.append(f"{last.text} {first.text if first is not None else ''}".strip())
        year_el = article.find(".//PubDate/Year")
        year = int(year_el.text) if year_el is not None and year_el.text else None
        pmid_el = article.find(".//PMID")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_el.text}/" if pmid_el is not None else None
        papers.append(Paper(title=title, authors=authors, abstract=abstract,
                            year=year, url=url, source="PubMed"))
    return papers


# ── CrossRef ──────────────────────────────────────────────────────────────────

def search_crossref(query, max_results=5, year_min=None, year_max=None):
    params = {"query": query, "rows": max_results,
              "select": "title,author,abstract,published,URL"}
    filters = []
    if year_min: filters.append(f"from-pub-date:{year_min}")
    if year_max: filters.append(f"until-pub-date:{year_max}")
    if filters:  params["filter"] = ",".join(filters)

    try:
        response = requests.get("https://api.crossref.org/works",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
    except Exception:
        return []

    papers = []
    for item in items:
        abstract = (item.get("abstract") or "").replace("<jats:p>", "").replace("</jats:p>", " ").strip()
        if len(abstract) < 50:
            continue
        title = (item.get("title") or ["Unknown title"])[0]
        authors = [f"{a.get('family', '')} {a.get('given', '')}".strip()
                   for a in item.get("author", []) if a.get("family")]
        date_parts = item.get("published", {}).get("date-parts", [[]])[0]
        year = date_parts[0] if date_parts else None
        papers.append(Paper(title=title, authors=authors, abstract=abstract,
                            year=year, url=item.get("URL"), source="CrossRef"))
    return papers[:max_results]


# ── CORE ─────────────────────────────────────────────────────────────────────

def search_core(query, max_results=5, year_min=None, year_max=None):
    params = {"q": query, "limit": max_results, "offset": 0}
    if year_min: params["yearFrom"] = year_min
    if year_max: params["yearTo"]   = year_max
    try:
        response = requests.get("https://api.core.ac.uk/v3/search/works",
                                params=params, headers=HEADERS, timeout=30)
        if response.status_code in (401, 403):
            return []
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    papers = []
    for item in results:
        abstract = item.get("abstract") or ""
        if len(abstract) < 50:
            continue
        year_raw = item.get("yearPublished")
        papers.append(Paper(
            title=item.get("title", "Unknown title"),
            authors=[a.get("name", "") for a in item.get("authors", [])],
            abstract=abstract,
            year=int(year_raw) if year_raw else None,
            url=item.get("downloadUrl"),
            source="CORE",
        ))
    return papers[:max_results]


# ── OpenAlex ──────────────────────────────────────────────────────────────────

def search_openalex(query, max_results=5, year_min=None, year_max=None):
    """OpenAlex — 250M+ articles, remplace Microsoft Academic. Gratuit, sans clé."""
    params = {
        "search": query,
        "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi,primary_location",
        "sort": "relevance_score:desc",
    }
    filters = []
    if year_min: filters.append(f"publication_year:>{year_min - 1}")
    if year_max: filters.append(f"publication_year:<{year_max + 1}")
    if filters:  params["filter"] = ",".join(filters)

    try:
        response = requests.get("https://api.openalex.org/works",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    papers = []
    for item in results:
        # OpenAlex stocke l'abstract comme index inversé — on le reconstruit
        inv_index = item.get("abstract_inverted_index") or {}
        if not inv_index:
            continue
        # Reconstruction : {mot: [positions]} → liste triée par position
        word_positions = []
        for word, positions in inv_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        abstract = " ".join(w for _, w in sorted(word_positions))
        if len(abstract) < 50:
            continue

        authors = [
            a.get("author", {}).get("display_name", "")
            for a in item.get("authorships", [])[:6]
        ]
        doi = item.get("doi") or ""
        url = doi if doi else (item.get("primary_location") or {}).get("landing_page_url")

        papers.append(Paper(
            title=item.get("title") or "Unknown title",
            authors=[a for a in authors if a],
            abstract=abstract,
            year=item.get("publication_year"),
            url=url,
            source="OpenAlex",
        ))
    return papers[:max_results]


# ── Europe PMC ────────────────────────────────────────────────────────────────

def search_europepmc(query, max_results=5, year_min=None, year_max=None):
    """Europe PMC — biomédicale + sciences de la vie, plus large que PubMed. Gratuit."""
    q = query
    if year_min: q += f" AND FIRST_PDATE:[{year_min}-01-01 TO *]"
    if year_max: q += f" AND FIRST_PDATE:[* TO {year_max}-12-31]"

    params = {
        "query": q,
        "format": "json",
        "pageSize": max_results,
        "resultType": "core",
        "sort": "RELEVANCE",
    }
    try:
        response = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("resultList", {}).get("result", [])
    except Exception:
        return []

    papers = []
    for item in results:
        abstract = item.get("abstractText") or ""
        if len(abstract) < 50:
            continue
        authors_raw = item.get("authorString") or ""
        authors = [a.strip() for a in authors_raw.split(",") if a.strip()][:6]
        pmid = item.get("pmid") or item.get("id") or ""
        url = f"https://europepmc.org/article/{item.get('source', 'MED')}/{pmid}" if pmid else None

        papers.append(Paper(
            title=item.get("title") or "Unknown title",
            authors=authors,
            abstract=abstract,
            year=item.get("pubYear"),
            url=url,
            source="Europe PMC",
        ))
    return papers[:max_results]


# ── DOAJ ──────────────────────────────────────────────────────────────────────

def search_doaj(query, max_results=5, year_min=None, year_max=None):
    """DOAJ — Directory of Open Access Journals. Gratuit, peer-reviewed uniquement."""
    params = {
        "q": query,
        "pageSize": max_results,
        "page": 1,
    }
    if year_min or year_max:
        y_from = year_min or 1900
        y_to   = year_max or 2026
        params["q"] += f" AND year:[{y_from} TO {y_to}]"

    try:
        response = requests.get("https://doaj.org/api/search/articles/" + requests.utils.quote(query),
                                params={"pageSize": max_results, "page": 1},
                                headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    papers = []
    for item in results:
        bibjson = item.get("bibjson") or {}
        abstract = bibjson.get("abstract") or ""
        if len(abstract) < 50:
            continue
        authors = [a.get("name", "") for a in bibjson.get("author", [])[:6]]
        year_raw = bibjson.get("year")
        links = bibjson.get("link") or []
        url = next((l.get("url") for l in links if l.get("type") == "fulltext"), None)

        papers.append(Paper(
            title=bibjson.get("title") or "Unknown title",
            authors=[a for a in authors if a],
            abstract=abstract,
            year=int(year_raw) if year_raw else None,
            url=url,
            source="DOAJ",
        ))
    return papers[:max_results]


# ── HAL (CNRS) ───────────────────────────────────────────────────────────────

def search_hal(query, max_results=5, year_min=None, year_max=None):
    """HAL — archive ouverte française, CNRS et universités. Gratuit, API ouverte."""
    params = {
        "q": query,
        "rows": max_results,
        "fl": "title_s,authFullName_s,abstract_s,producedDateY_i,uri_s",
        "sort": "score desc",
        "wt": "json",
    }
    filters = ["docType_s:ART"]  # articles uniquement
    if year_min: filters.append(f"producedDateY_i:[{year_min} TO *]")
    if year_max: filters.append(f"producedDateY_i:[* TO {year_max}]")
    params["fq"] = " AND ".join(filters)

    try:
        response = requests.get("https://api.archives-ouvertes.fr/search/",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
    except Exception:
        return []

    papers = []
    for item in docs:
        abstract_list = item.get("abstract_s") or []
        abstract = abstract_list[0] if isinstance(abstract_list, list) and abstract_list else str(abstract_list)
        if len(abstract) < 50:
            continue
        title_list = item.get("title_s") or ["Unknown title"]
        title = title_list[0] if isinstance(title_list, list) else title_list
        authors = item.get("authFullName_s") or []

        papers.append(Paper(
            title=title,
            authors=authors[:6],
            abstract=abstract,
            year=item.get("producedDateY_i"),
            url=item.get("uri_s"),
            source="HAL (CNRS)",
        ))
    return papers[:max_results]


# ── bioRxiv / medRxiv ─────────────────────────────────────────────────────────

def search_biorxiv(query, max_results=5, year_min=None, year_max=None):
    """bioRxiv + medRxiv — preprints en biologie et médecine. Gratuit."""
    # L'API bioRxiv retourne des résultats par intervalle de dates
    from datetime import date
    date_to   = f"{year_max}-12-31"   if year_max else date.today().isoformat()
    date_from = f"{year_min}-01-01"   if year_min else "2013-01-01"

    papers = []
    for server in ["biorxiv", "medrxiv"]:
        if len(papers) >= max_results:
            break
        try:
            url = f"https://api.biorxiv.org/details/{server}/{date_from}/{date_to}/0/json"
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            collection = response.json().get("collection", [])
        except Exception:
            continue

        # Filtrer par mots-clés dans titre + abstract
        query_words = query.lower().split()
        for item in collection:
            abstract = item.get("abstract") or ""
            title    = item.get("title") or ""
            text     = (title + " " + abstract).lower()
            if not all(w in text for w in query_words):
                continue
            if len(abstract) < 50:
                continue
            authors_raw = item.get("authors") or ""
            authors = [a.strip() for a in authors_raw.split(";") if a.strip()][:6]
            doi = item.get("doi") or ""

            papers.append(Paper(
                title=title,
                authors=authors,
                abstract=abstract,
                year=int(item.get("date", "2000")[:4]) if item.get("date") else None,
                url=f"https://doi.org/{doi}" if doi else None,
                source="bioRxiv/medRxiv",
            ))
            if len(papers) >= max_results:
                break

    return papers[:max_results]


# ── NIH National Library of Medicine ─────────────────────────────────────────

def search_nih(query, max_results=5, year_min=None, year_max=None):
    """NIH NLM — base de données médicale officielle US. Gratuit, sans clé."""
    # Utilise l'API E-utilities avec terme de recherche étendu
    term = query
    if year_min or year_max:
        y_from = year_min or 1900
        y_to   = year_max or 2026
        term += f" AND {y_from}:{y_to}[dp]"

    try:
        # Recherche sur PMC (PubMed Central = full text open access)
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pmc", "term": term, "retmax": max_results,
                    "retmode": "json", "sort": "relevance"},
            headers=HEADERS, timeout=30,
        )
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        r2 = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pmc", "id": ",".join(ids), "retmode": "xml"},
            headers=HEADERS, timeout=30,
        )
        r2.raise_for_status()
        root = ET.fromstring(r2.text)
    except Exception:
        return []

    papers = []
    for article in root.findall(".//article"):
        # Titre
        title_el = article.find(".//article-title")
        title = ("".join(title_el.itertext())).strip() if title_el is not None else "Unknown title"

        # Abstract
        abstract_parts = article.findall(".//abstract//p")
        abstract = " ".join("".join(p.itertext()) for p in abstract_parts).strip()
        if not abstract:
            abstract_el = article.find(".//abstract")
            abstract = ("".join(abstract_el.itertext())).strip() if abstract_el is not None else ""
        if len(abstract) < 50:
            continue

        # Auteurs
        authors = []
        for contrib in article.findall(".//contrib[@contrib-type='author']"):
            surname = contrib.find(".//surname")
            given   = contrib.find(".//given-names")
            if surname is not None:
                name = surname.text or ""
                if given is not None:
                    name += f" {given.text}"
                authors.append(name.strip())

        # Année
        year_el = article.find(".//pub-date/year")
        year = int(year_el.text) if year_el is not None and year_el.text else None

        # URL
        pmc_id = article.find(".//article-id[@pub-id-type='pmc']")
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id.text}/" if pmc_id is not None else None

        papers.append(Paper(
            title=title, authors=authors[:6], abstract=abstract,
            year=year, url=url, source="NIH / PubMed Central",
        ))

    return papers[:max_results]


# ── Zenodo (CERN) ─────────────────────────────────────────────────────────────

def search_zenodo(query, max_results=5, year_min=None, year_max=None):
    """Zenodo — dépôt open access du CERN, tous domaines. Gratuit, sans clé."""
    params = {
        "q": query,
        "size": max_results,
        "type": "publication",
        "sort": "bestmatch",
    }
    if year_min or year_max:
        y_from = year_min or 1900
        y_to   = year_max or 2026
        params["q"] += f" AND publication_date:[{y_from}-01-01 TO {y_to}-12-31]"

    try:
        response = requests.get("https://zenodo.org/api/records",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
    except Exception:
        return []

    papers = []
    for item in hits:
        metadata = item.get("metadata", {})
        abstract = metadata.get("description") or ""
        # Zenodo peut retourner du HTML dans la description
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
        if len(abstract) < 50:
            continue
        authors = [
            f"{a.get('name', '')}" for a in metadata.get("creators", [])[:6]
        ]
        year_raw = metadata.get("publication_date", "")
        year = int(year_raw[:4]) if year_raw and year_raw[:4].isdigit() else None
        doi = metadata.get("doi")
        url = f"https://doi.org/{doi}" if doi else item.get("links", {}).get("html")

        papers.append(Paper(
            title=metadata.get("title", "Unknown title"),
            authors=authors,
            abstract=abstract,
            year=year,
            url=url,
            source="Zenodo",
        ))
    return papers[:max_results]


# ── PLOS ONE ──────────────────────────────────────────────────────────────────

def search_plos(query, max_results=5, year_min=None, year_max=None):
    """PLOS — Public Library of Science, peer-reviewed open access. Gratuit."""
    params = {
        "q": query,
        "rows": max_results,
        "fl": "title,author_display,abstract,publication_date,id",
        "wt": "json",
        "sort": "score desc",
    }
    if year_min or year_max:
        y_from = f"{year_min or 1900}-01-01T00:00:00Z"
        y_to   = f"{year_max or 2026}-12-31T23:59:59Z"
        params["fq"] = f"publication_date:[{y_from} TO {y_to}]"

    try:
        response = requests.get("https://api.plos.org/search",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
    except Exception:
        return []

    papers = []
    for item in docs:
        abstract_list = item.get("abstract") or []
        abstract = " ".join(abstract_list) if isinstance(abstract_list, list) else str(abstract_list)
        if len(abstract) < 50:
            continue
        year_raw = item.get("publication_date", "")
        year = int(year_raw[:4]) if year_raw and year_raw[:4].isdigit() else None
        doi = item.get("id", "")
        url = f"https://doi.org/{doi}" if doi else None

        papers.append(Paper(
            title=item.get("title", "Unknown title"),
            authors=item.get("author_display", [])[:6],
            abstract=abstract,
            year=year,
            url=url,
            source="PLOS",
        ))
    return papers[:max_results]


# ── PeerJ ─────────────────────────────────────────────────────────────────────

def search_peerj(query, max_results=5, year_min=None, year_max=None):
    """PeerJ — biologie, médecine, informatique. Gratuit via CrossRef."""
    # PeerJ n'a pas d'API propre mais tous ses articles sont sur CrossRef
    # On filtre par publisher "PeerJ"
    params = {
        "query": query,
        "rows": max_results,
        "filter": "publisher:PeerJ",
        "select": "title,author,abstract,published,URL",
    }
    if year_min: params["filter"] += f",from-pub-date:{year_min}"
    if year_max: params["filter"] += f",until-pub-date:{year_max}"

    try:
        response = requests.get("https://api.crossref.org/works",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
    except Exception:
        return []

    papers = []
    for item in items:
        abstract = (item.get("abstract") or "").replace("<jats:p>", "").replace("</jats:p>", " ").strip()
        if len(abstract) < 50:
            continue
        title = (item.get("title") or ["Unknown title"])[0]
        authors = [f"{a.get('family', '')} {a.get('given', '')}".strip()
                   for a in item.get("author", []) if a.get("family")]
        date_parts = item.get("published", {}).get("date-parts", [[]])[0]
        year = date_parts[0] if date_parts else None

        papers.append(Paper(
            title=title, authors=authors[:6], abstract=abstract,
            year=year, url=item.get("URL"), source="PeerJ",
        ))
    return papers[:max_results]


# ── OSF Preprints (Open Science Framework) ────────────────────────────────────

def search_osf(query, max_results=5, year_min=None, year_max=None):
    """OSF Preprints — plateforme multi-domaines (SocArXiv, PsyArXiv, etc.). Gratuit."""
    params = {
        "q": query,
        "page[size]": max_results,
        "filter[sources]": "socarxiv,psyarxiv,engrxiv,eartharxiv,agrixiv",
    }
    try:
        response = requests.get("https://api.osf.io/v2/preprints/",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", [])
    except Exception:
        return []

    papers = []
    for item in data:
        attrs = item.get("attributes", {})
        abstract = attrs.get("description") or ""
        if len(abstract) < 50:
            continue
        title = attrs.get("title") or "Unknown title"
        date_created = attrs.get("date_created") or ""
        year = int(date_created[:4]) if date_created and date_created[:4].isdigit() else None
        doi = attrs.get("doi") or ""
        url = f"https://doi.org/{doi}" if doi else f"https://osf.io/{item.get('id','')}"

        if year_min and year and year < year_min: continue
        if year_max and year and year > year_max: continue

        papers.append(Paper(
            title=title, authors=[], abstract=abstract,
            year=year, url=url, source="OSF Preprints",
        ))
    return papers[:max_results]


# ── J-STAGE (Japon) ───────────────────────────────────────────────────────────

def search_jstage(query, max_results=5, year_min=None, year_max=None):
    """J-STAGE — plateforme japonaise officielle, articles en japonais et anglais. Gratuit."""
    params = {
        "text": query,
        "count": max_results,
        "start": 1,
        "format": "json",
        "sortorder": "score",
    }
    if year_min: params["pubyearfrom"] = year_min
    if year_max: params["pubyearto"]   = year_max

    try:
        response = requests.get("https://api.jstage.jst.go.jp/searchapi/do",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        items = response.json().get("feed", {}).get("entry", [])
        if isinstance(items, dict):
            items = [items]
    except Exception:
        return []

    papers = []
    for item in items:
        abstract = item.get("jstage:article-abstract", "") or item.get("summary", {}).get("#text", "")
        if len(abstract) < 30:
            abstract = item.get("jstage:article-title", "")  # fallback titre comme abstract
        if len(abstract) < 10:
            continue
        title    = item.get("title", {})
        title    = title.get("#text", title) if isinstance(title, dict) else str(title)
        authors_raw = item.get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]
        year_raw = item.get("updated", item.get("published", ""))
        year = int(year_raw[:4]) if year_raw and year_raw[:4].isdigit() else None
        url  = item.get("link", {}).get("@href") or item.get("id", "")

        papers.append(Paper(
            title=str(title), authors=authors[:6], abstract=str(abstract),
            year=year, url=str(url), source="J-STAGE",
        ))
    return papers[:max_results]


# ── SciELO (Espagnol / Portugais) ─────────────────────────────────────────────

def search_scielo(query, max_results=5, year_min=None, year_max=None):
    """SciELO — réseau latino-américain, articles en espagnol, portugais, anglais. Gratuit."""
    params = {
        "q":    query,
        "count": max_results,
        "from":  0,
        "format": "json",
        "lang": "en,es,pt",
    }
    if year_min: params["filter[year_cluster][]"] = year_min
    try:
        response = requests.get("https://search.scielo.org/api/v1/article/",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
    except Exception:
        return []

    papers = []
    for item in hits:
        src = item.get("_source", {})
        abstract_dict = src.get("ab", {})
        abstract = ""
        for lang_key in ["en", "es", "pt", "fr"]:
            val = abstract_dict.get(lang_key, "")
            if isinstance(val, list): val = " ".join(val)
            if len(val) > len(abstract):
                abstract = val
        if len(abstract) < 50:
            continue

        title_dict = src.get("ti", {})
        title = title_dict.get("en") or title_dict.get("es") or title_dict.get("pt") or "Unknown title"
        if isinstance(title, list): title = title[0]

        authors = [a.get("n", "") for a in src.get("au", [])[:6] if isinstance(a, dict)]
        year = src.get("py")
        doi  = src.get("doi", "")
        url  = f"https://doi.org/{doi}" if doi else None

        papers.append(Paper(
            title=str(title), authors=authors, abstract=abstract,
            year=int(year) if year else None, url=url, source="SciELO",
        ))
    return papers[:max_results]


# ── Redalyc (Espagnol / Amérique latine) ─────────────────────────────────────

def search_redalyc(query, max_results=5, year_min=None, year_max=None):
    """Redalyc — revues scientifiques latino-américaines en open access. Via OpenAlex filter."""
    # Redalyc n'a pas d'API JSON directe publique — on passe par OpenAlex filtré par source
    params = {
        "search": query,
        "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi",
        "sort": "relevance_score:desc",
        "filter": "primary_location.source.host_organization_lineage:I4210164504",  # Redalyc org ID
    }
    try:
        response = requests.get("https://api.openalex.org/works",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    papers = []
    for item in results:
        inv_index = item.get("abstract_inverted_index") or {}
        if not inv_index:
            continue
        word_positions = [(pos, word) for word, positions in inv_index.items() for pos in positions]
        abstract = " ".join(w for _, w in sorted(word_positions))
        if len(abstract) < 50:
            continue
        year = item.get("publication_year")
        if year_min and year and year < year_min: continue
        if year_max and year and year > year_max: continue
        authors = [a.get("author", {}).get("display_name", "") for a in item.get("authorships", [])[:6]]
        doi = item.get("doi") or ""
        url = f"https://doi.org/{doi.replace('https://doi.org/','')}" if doi else None

        papers.append(Paper(
            title=item.get("title") or "Unknown title", authors=[a for a in authors if a],
            abstract=abstract, year=year, url=url, source="Redalyc",
        ))
    return papers[:max_results]


# ── PERSÉE (Français — Sciences Humaines) ────────────────────────────────────

def search_persee(query, max_results=5, year_min=None, year_max=None):
    """PERSÉE — revues françaises de sciences humaines et sociales numérisées. Gratuit."""
    params = {
        "q":      query,
        "rows":   max_results,
        "wt":     "json",
        "fl":     "title_s,author_s,abstract_s,date_dt,url_s",
        "sort":   "score desc",
    }
    filters = ["type_s:article"]
    if year_min: filters.append(f"date_dt:[{year_min}-01-01T00:00:00Z TO *]")
    if year_max: filters.append(f"date_dt:[* TO {year_max}-12-31T23:59:59Z]")
    params["fq"] = " AND ".join(filters)

    try:
        response = requests.get("https://www.persee.fr/search",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
    except Exception:
        return []

    papers = []
    for item in docs:
        abstract_raw = item.get("abstract_s") or []
        abstract = " ".join(abstract_raw) if isinstance(abstract_raw, list) else str(abstract_raw)
        if len(abstract) < 30:
            continue
        title_raw = item.get("title_s") or ["Unknown title"]
        title = title_raw[0] if isinstance(title_raw, list) else title_raw
        authors_raw = item.get("author_s") or []
        authors = authors_raw if isinstance(authors_raw, list) else [authors_raw]
        date_raw = item.get("date_dt") or ""
        year = int(date_raw[:4]) if date_raw and date_raw[:4].isdigit() else None
        url = item.get("url_s") or ""

        papers.append(Paper(
            title=str(title), authors=authors[:6], abstract=abstract,
            year=year, url=str(url), source="PERSÉE",
        ))
    return papers[:max_results]


# ── CyberLeninka (Russe) ──────────────────────────────────────────────────────

def search_cyberleninka(query, max_results=5, year_min=None, year_max=None):
    """CyberLeninka — articles scientifiques russes en open access. Via OpenAlex."""
    params = {
        "search": query,
        "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi,primary_location",
        "sort": "relevance_score:desc",
        "filter": "primary_location.source.country_code:RU,open_access.is_oa:true",
    }
    if year_min or year_max:
        filters = []
        if year_min: filters.append(f"publication_year:>{year_min-1}")
        if year_max: filters.append(f"publication_year:<{year_max+1}")
        params["filter"] += "," + ",".join(filters)

    try:
        response = requests.get("https://api.openalex.org/works",
                                params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    papers = []
    for item in results:
        inv_index = item.get("abstract_inverted_index") or {}
        if not inv_index:
            continue
        word_positions = [(pos, word) for word, positions in inv_index.items() for pos in positions]
        abstract = " ".join(w for _, w in sorted(word_positions))
        if len(abstract) < 50:
            continue
        year = item.get("publication_year")
        authors = [a.get("author", {}).get("display_name", "") for a in item.get("authorships", [])[:6]]
        doi = item.get("doi") or ""
        url = doi if doi else (item.get("primary_location") or {}).get("landing_page_url")

        papers.append(Paper(
            title=item.get("title") or "Unknown title", authors=[a for a in authors if a],
            abstract=abstract, year=year, url=url, source="CyberLeninka (RU)",
        ))
    return papers[:max_results]


# ── KCI (Coréen) ─────────────────────────────────────────────────────────────

def search_kci(query, max_results=5, year_min=None, year_max=None):
    """KCI — Korea Citation Index, articles coréens. API REST publique."""
    params = {
        "apiKey":  "guest",
        "query":   query,
        "displayCount": max_results,
        "startPosition": 1,
    }
    if year_min: params["yearFrom"] = year_min
    if year_max: params["yearTo"]   = year_max

    try:
        response = requests.get(
            "https://www.kci.go.kr/kciportal/rest/openapi/articleSearch.kci",
            params=params, headers=HEADERS, timeout=30,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception:
        return []

    papers = []
    for article in root.findall(".//article"):
        title_el    = article.find(".//title[@lang='English']") or article.find(".//title")
        abstract_el = article.find(".//abstract[@lang='English']") or article.find(".//abstract")
        title    = title_el.text.strip()    if title_el    is not None and title_el.text    else "Unknown title"
        abstract = abstract_el.text.strip() if abstract_el is not None and abstract_el.text else ""
        if len(abstract) < 30:
            continue
        year_el = article.find(".//year")
        year    = int(year_el.text) if year_el is not None and year_el.text else None
        authors = [a.text.strip() for a in article.findall(".//author") if a.text]
        url_el  = article.find(".//url")
        url     = url_el.text if url_el is not None else None

        papers.append(Paper(
            title=title, authors=authors[:6], abstract=abstract,
            year=year, url=url, source="KCI (Korea)",
        ))
    return papers[:max_results]


# ── CiNii (Japon — NII) ───────────────────────────────────────────────────────

def search_cinii(query, max_results=5, year_min=None, year_max=None):
    """CiNii — National Institute of Informatics Japan. Articles japonais + anglais."""
    params = {"q": query, "count": max_results, "start": 1, "format": "json"}
    if year_min: params["from"] = year_min
    if year_max: params["until"] = year_max
    try:
        r = requests.get("https://cir.nii.ac.jp/opensearch/articles",
                         params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception:
        return []
    papers = []
    for item in items:
        abstract = item.get("description", "") or item.get("title", "")
        if len(abstract) < 10: continue
        title = item.get("title", "Unknown title")
        authors = [a.get("name","") for a in item.get("creator",[])[:6] if isinstance(a,dict)]
        pub = item.get("pubDate") or item.get("publicationDate") or ""
        year = int(pub[:4]) if pub and pub[:4].isdigit() else None
        url = item.get("@id") or item.get("link","")
        papers.append(Paper(title=str(title), authors=authors, abstract=str(abstract),
                            year=year, url=str(url), source="CiNii"))
    return papers[:max_results]


# ── DIALNET (Espagnol) ────────────────────────────────────────────────────────

def search_dialnet(query, max_results=5, year_min=None, year_max=None):
    """DIALNET — plus grande base hispanophone via OpenAlex (ES filter)."""
    params = {
        "search": query, "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi",
        "sort": "relevance_score:desc",
        "filter": "primary_location.source.country_code:ES,language:es",
    }
    if year_min: params["filter"] += f",publication_year:>{year_min-1}"
    if year_max: params["filter"] += f",publication_year:<{year_max+1}"
    try:
        r = requests.get("https://api.openalex.org/works", params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []
    papers = []
    for item in results:
        inv = item.get("abstract_inverted_index") or {}
        if not inv: continue
        abstract = " ".join(w for _,w in sorted([(p,w) for w,ps in inv.items() for p in ps]))
        if len(abstract) < 50: continue
        authors = [a.get("author",{}).get("display_name","") for a in item.get("authorships",[])[:6]]
        doi = item.get("doi") or ""
        papers.append(Paper(title=item.get("title") or "Unknown title",
                            authors=[a for a in authors if a], abstract=abstract,
                            year=item.get("publication_year"), url=doi or None, source="DIALNET"))
    return papers[:max_results]


# ── OpenEdition (Français SHS) ────────────────────────────────────────────────

def search_openedition(query, max_results=5, year_min=None, year_max=None):
    """OpenEdition — revues françaises SHS en open access."""
    params = {"q": query, "rows": max_results, "wt": "json",
              "fl": "title_s,author_s,abstract_s,date_dt,url_s"}
    fq = ["doctype_s:article"]
    if year_min: fq.append(f"date_dt:[{year_min}-01-01T00:00:00Z TO *]")
    if year_max: fq.append(f"date_dt:[* TO {year_max}-12-31T23:59:59Z]")
    params["fq"] = " AND ".join(fq)
    try:
        r = requests.get("https://data.openedition.org/api/v1/search",
                         params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception:
        return []
    papers = []
    for item in docs:
        abstract_raw = item.get("abstract_s") or []
        abstract = " ".join(abstract_raw) if isinstance(abstract_raw, list) else str(abstract_raw)
        if len(abstract) < 30: continue
        title_raw = item.get("title_s") or ["Unknown title"]
        title = title_raw[0] if isinstance(title_raw, list) else title_raw
        authors_raw = item.get("author_s") or []
        authors = authors_raw if isinstance(authors_raw, list) else [authors_raw]
        date_raw = item.get("date_dt") or ""
        year = int(date_raw[:4]) if date_raw and date_raw[:4].isdigit() else None
        papers.append(Paper(title=str(title), authors=authors[:6], abstract=abstract,
                            year=year, url=item.get("url_s",""), source="OpenEdition"))
    return papers[:max_results]


# ── e-Revistas / CSIC (Espagnol) ─────────────────────────────────────────────

def search_erevistas(query, max_results=5, year_min=None, year_max=None):
    """e-Revistas — revues scientifiques espagnoles du CSIC via OpenAlex."""
    params = {
        "search": query, "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi",
        "sort": "relevance_score:desc",
        "filter": "primary_location.source.host_organization.lineage:I4210119307",
    }
    if year_min: params["filter"] += f",publication_year:>{year_min-1}"
    if year_max: params["filter"] += f",publication_year:<{year_max+1}"
    try:
        r = requests.get("https://api.openalex.org/works", params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []
    papers = []
    for item in results:
        inv = item.get("abstract_inverted_index") or {}
        if not inv: continue
        abstract = " ".join(w for _,w in sorted([(p,w) for w,ps in inv.items() for p in ps]))
        if len(abstract) < 50: continue
        authors = [a.get("author",{}).get("display_name","") for a in item.get("authorships",[])[:6]]
        doi = item.get("doi") or ""
        papers.append(Paper(title=item.get("title") or "Unknown title",
                            authors=[a for a in authors if a], abstract=abstract,
                            year=item.get("publication_year"), url=doi or None,
                            source="e-Revistas (CSIC)"))
    return papers[:max_results]


# ── NARCIS (Pays-Bas) ─────────────────────────────────────────────────────────

def search_narcis(query, max_results=5, year_min=None, year_max=None):
    """NARCIS — portail de la recherche néerlandaise via OpenAlex (NL filter)."""
    params = {
        "search": query, "per-page": max_results,
        "select": "title,authorships,abstract_inverted_index,publication_year,doi",
        "sort": "relevance_score:desc",
        "filter": "primary_location.source.country_code:NL,open_access.is_oa:true",
    }
    if year_min: params["filter"] += f",publication_year:>{year_min-1}"
    if year_max: params["filter"] += f",publication_year:<{year_max+1}"
    try:
        r = requests.get("https://api.openalex.org/works", params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []
    papers = []
    for item in results:
        inv = item.get("abstract_inverted_index") or {}
        if not inv: continue
        abstract = " ".join(w for _,w in sorted([(p,w) for w,ps in inv.items() for p in ps]))
        if len(abstract) < 50: continue
        authors = [a.get("author",{}).get("display_name","") for a in item.get("authorships",[])[:6]]
        doi = item.get("doi") or ""
        papers.append(Paper(title=item.get("title") or "Unknown title",
                            authors=[a for a in authors if a], abstract=abstract,
                            year=item.get("publication_year"), url=doi or None,
                            source="NARCIS (NL)"))
    return papers[:max_results]


# ── DBLP (Informatique) ───────────────────────────────────────────────────────

def search_dblp(query, max_results=5, year_min=None, year_max=None):
    """DBLP — référence en informatique théorique et appliquée. Gratuit."""
    params = {"q": query, "format": "json", "h": max_results, "f": 0}
    try:
        r = requests.get("https://dblp.org/search/publ/api",
                         params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception:
        return []
    papers = []
    for item in hits:
        info = item.get("info", {})
        title = info.get("title", "Unknown title")
        year_raw = info.get("year")
        year = int(year_raw) if year_raw else None
        if year_min and year and year < year_min: continue
        if year_max and year and year > year_max: continue
        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict): authors_raw = [authors_raw]
        authors = [a.get("text","") if isinstance(a,dict) else str(a) for a in authors_raw[:6]]
        url = info.get("url") or info.get("ee") or ""
        if isinstance(url, list): url = url[0]
        abstract = f"Computer science publication: {title}."
        if info.get("venue"): abstract += f" Published in: {info['venue']}."
        papers.append(Paper(title=str(title), authors=authors, abstract=abstract,
                            year=year, url=str(url), source="DBLP"))
    return papers[:max_results]


# ── RePEC (Économie) ──────────────────────────────────────────────────────────

def search_repec(query, max_results=5, year_min=None, year_max=None):
    """RePEC — Research Papers in Economics. Plus grande base en économie."""
    params = {"query": query, "start": 0, "max": max_results, "fmt": "json2"}
    try:
        r = requests.get("https://ideas.repec.org/cgi-bin/htsearch",
                         params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        items = r.json().get("results", [])
    except Exception:
        return []
    papers = []
    for item in items:
        abstract = item.get("abstract") or item.get("description") or item.get("title","")
        if len(abstract) < 20: continue
        year_raw = str(item.get("year") or item.get("date") or "")
        year = int(year_raw[:4]) if year_raw[:4].isdigit() else None
        if year_min and year and year < year_min: continue
        if year_max and year and year > year_max: continue
        authors_raw = item.get("authors") or []
        authors = authors_raw if isinstance(authors_raw, list) else [authors_raw]
        papers.append(Paper(title=str(item.get("title","Unknown")), authors=authors[:6],
                            abstract=str(abstract), year=year,
                            url=str(item.get("url","")), source="RePEC (Economics)"))
    return papers[:max_results]


# ── BASE (Bielefeld) ──────────────────────────────────────────────────────────

def search_base(query, max_results=5, year_min=None, year_max=None):
    """BASE — 350M+ documents, Bielefeld University. Gratuit."""
    params = {"query": query, "hits": max_results, "offset": 0,
              "format": "json", "boost": "oa"}
    if year_min: params["filter_yearMin"] = year_min
    if year_max: params["filter_yearMax"] = year_max
    try:
        r = requests.get(
            "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception:
        return []
    papers = []
    for item in docs:
        abstract = item.get("dcabstract") or item.get("dcdescription") or ""
        if isinstance(abstract, list): abstract = " ".join(abstract)
        if len(abstract) < 30: continue
        title_raw = item.get("dctitle") or "Unknown title"
        title = title_raw[0] if isinstance(title_raw, list) else title_raw
        authors_raw = item.get("dccreator") or []
        authors = authors_raw if isinstance(authors_raw, list) else [authors_raw]
        year_raw = str(item.get("dcyear") or item.get("dcdate") or "")
        year = int(year_raw[:4]) if year_raw[:4].isdigit() else None
        url = item.get("dclink") or item.get("dcidentifier") or ""
        if isinstance(url, list): url = url[0]
        papers.append(Paper(title=str(title), authors=authors[:6], abstract=str(abstract),
                            year=year, url=str(url), source="BASE"))
    return papers[:max_results]


# ── Dédoublonnage inter-sources ───────────────────────────────────────────────
# Clé de dédoublonnage : DOI si on peut l'extraire de l'URL (fiable, indépendant
# de la casse/ponctuation du titre selon la source), sinon titre normalisé.
# Quand plusieurs sources retournent le même article, on garde la version la plus
# complète (année + auteurs + abstract les plus riches) et on liste les autres
# sources dans `duplicate_sources` — signal de pertinence affiché dans l'UI.

import re as _re

_DOI_RE = _re.compile(r"doi\.org/(10\.\S+)", _re.IGNORECASE)


def _extract_doi(url):
    if not url:
        return None
    m = _DOI_RE.search(url)
    if not m:
        return None
    return m.group(1).lower().rstrip("/.,")


def _normalize_title(title):
    t = title.lower().strip()
    t = _re.sub(r"[^\w\s]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t[:100]


def _completeness(paper):
    return (1 if paper.year else 0, len(paper.authors), len(paper.abstract))


class _UnionFind:
    """Fusionne deux articles s'ils partagent le même DOI OU le même titre normalisé —
    un article sans DOI doit quand même rejoindre le groupe d'un doublon qui, lui,
    en a un, dès lors que les titres correspondent (matching transitif)."""

    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def deduplicate(papers):
    n = len(papers)
    uf = _UnionFind(n)

    first_by_doi, first_by_title = {}, {}
    for i, p in enumerate(papers):
        doi = _extract_doi(p.url)
        if doi:
            if doi in first_by_doi:
                uf.union(i, first_by_doi[doi])
            else:
                first_by_doi[doi] = i

        title_key = _normalize_title(p.title)
        if title_key in first_by_title:
            uf.union(i, first_by_title[title_key])
        else:
            first_by_title[title_key] = i

    groups, order = {}, []
    for i in range(n):
        root = uf.find(i)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(papers[i])

    unique = []
    for root in order:
        group = groups[root]
        canonical = max(group, key=_completeness)
        all_sources = []
        for p in group:
            if p.source not in all_sources:
                all_sources.append(p.source)
        if len(all_sources) > 1:
            canonical = canonical.model_copy(update={"duplicate_sources": all_sources})
        unique.append(canonical)
    return unique


# ── Sources disponibles ───────────────────────────────────────────────────────

SOURCES = {
    # ── Général / pluridisciplinaire ──────────────────────────────────────────
    "arXiv":                search_arxiv,
    "Semantic Scholar":     search_semantic_scholar,
    "CrossRef":             search_crossref,
    "OpenAlex":             search_openalex,
    "CORE":                 search_core,
    "DOAJ":                 search_doaj,
    "Zenodo":               search_zenodo,
    "OSF Preprints":        search_osf,
    "BASE":                 search_base,
    # ── Médecine / biologie ───────────────────────────────────────────────────
    "PubMed":               search_pubmed,
    "NIH / PubMed Central": search_nih,
    "Europe PMC":           search_europepmc,
    "bioRxiv/medRxiv":      search_biorxiv,
    "PLOS":                 search_plos,
    "PeerJ":                search_peerj,
    # ── Informatique ─────────────────────────────────────────────────────────
    "DBLP":                 search_dblp,
    # ── Économie ─────────────────────────────────────────────────────────────
    "RePEC (Economics)":    search_repec,
    # ── France / SHS ─────────────────────────────────────────────────────────
    "HAL (CNRS)":           search_hal,
    "PERSÉE":               search_persee,
    "OpenEdition":          search_openedition,
    # ── Espagnol / Portugais ──────────────────────────────────────────────────
    "SciELO":               search_scielo,
    "Redalyc":              search_redalyc,
    "DIALNET":              search_dialnet,
    "e-Revistas (CSIC)":    search_erevistas,
    # ── Japonais ─────────────────────────────────────────────────────────────
    "J-STAGE":              search_jstage,
    "CiNii":                search_cinii,
    # ── Russe ────────────────────────────────────────────────────────────────
    "CyberLeninka (RU)":    search_cyberleninka,
    # ── Coréen ───────────────────────────────────────────────────────────────
    "KCI (Korea)":          search_kci,
    # ── Néerlandais ──────────────────────────────────────────────────────────
    "NARCIS (NL)":          search_narcis,
}

ALL_SOURCE_NAMES = list(SOURCES.keys())


def search_papers(
    query: str,
    max_results: int = 5,
    sources: list = ["arXiv"],
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
) -> list[Paper]:
    """Recherche multi-sources avec dédoublonnage. Retourne au plus max_results articles.

    Les sources sont interrogées en parallèle (threads) — ce sont des appels HTTP
    indépendants vers des APIs différentes, donc paralléliser est sans risque et
    ramène le temps total au temps de la source la plus lente, au lieu de la somme
    de toutes les sources. Le rate-limiting par source (arXiv, Semantic Scholar)
    reste appliqué via _rate_limit, thread-safe.
    """
    if not sources:
        sources = ["arXiv"]

    # On demande un buffer par source pour compenser le filtre de pertinence
    # Si l'utilisateur veut 7 articles avec score >= 7, on en cherche plus
    # car beaucoup seront filtrés. Buffer x3 = bonne couverture.
    per_source = min(max_results * 3, 20)

    valid_sources = [(name, SOURCES[name]) for name in sources if name in SOURCES]

    results_by_source = {}
    with ThreadPoolExecutor(max_workers=min(len(valid_sources), 8) or 1) as executor:
        future_to_name = {
            executor.submit(fn, query, per_source, year_min, year_max): name
            for name, fn in valid_sources
        }
        for future in as_completed(future_to_name):
            source_name = future_to_name[future]
            try:
                results_by_source[source_name] = future.result()
            except Exception as e:
                print(f"[{source_name}] Error: {e}")
                results_by_source[source_name] = []

    # Fusion round-robin (1 article de chaque source à tour de rôle), dans l'ordre
    # choisi par l'utilisateur — sinon la troncature à max_results favoriserait
    # systématiquement la source qui répond le plus vite (souvent arXiv), qui
    # monopoliserait le quota au détriment des autres sources sélectionnées.
    ordered_lists = [results_by_source.get(name, []) for name, _ in valid_sources]
    merged = [
        paper
        for group in zip_longest(*ordered_lists)
        for paper in group
        if paper is not None
    ]

    return deduplicate(merged)[:max_results]