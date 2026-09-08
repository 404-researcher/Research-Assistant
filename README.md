# 404-researcher — Research Assistant

An AI-powered research assistant that searches **29 academic databases at once**, scores and summarizes every article with an AI model, and writes a synthesis report you can export as PDF, Excel, Markdown or citations (APA / BibTeX / IEEE).

It runs as a simple web app on your own computer — no account, no cloud service required if you use the free local AI option (Ollama).

> Built as a learning/research project, now open-sourced for anyone to use or improve.

---

## What it does

- 🔍 **Multi-source search** — queries up to 29 databases in parallel (arXiv, Semantic Scholar, CrossRef, OpenAlex, PubMed, HAL, SciELO, DBLP, and more — general science, medicine, economics, and regional databases in French, Spanish, Japanese, Korean, Russian, Dutch...).
- 🧹 **De-duplication** — the same article found in several databases is merged into one entry.
- 🌐 **Query translation** — type your search in any language; it's auto-translated to English before searching (you can review/edit the translation first).
- 🤖 **AI scoring & summaries** — each article is scored for relevance (0–10) and summarized by an AI model, with key points and a one-line justification.
- 📊 **Synthesis report** — a generated report covering main themes, key findings, consensus, and gaps across all retrieved articles.
- 🌍 **Language filter** — restrict results to articles written in a specific language, and choose the language your summaries/report are written in.
- 📄 **PDF upload & analysis** — drop a paper's PDF and get an AI summary, methodology breakdown, and relevance score for your topic.
- ⚖️ **Compare mode** — run two searches side by side to compare two topics or angles.
- 🕘 **Search history** — past searches and their generated files are kept locally so you can revisit or re-download them.
- 📦 **Export everything** — citations (APA/BibTeX/IEEE), and reports as Markdown, PDF, Excel, or a single ZIP.

## How it works

1. You type a research topic and pick which databases to search.
2. The app queries those databases in parallel and merges/de-duplicates the results.
3. Each article's abstract is sent to an AI model (your choice: local Ollama, OpenAI, or Anthropic) which scores its relevance and writes a summary.
4. The app asks the AI for one more pass: a synthesis report across all the articles it kept.
5. You review everything in the browser and export what you need.

Nothing about your topic or results is stored anywhere except on your own machine (in `history.json`, ignored by git).

## Requirements

- **Python 3.10+**
- One AI provider, your choice:
  - **[Ollama](https://ollama.com)** — free, runs entirely on your machine, no API key. Recommended to get started.
  - **OpenAI API key** — paid, requires an account at [platform.openai.com](https://platform.openai.com).
  - **Anthropic API key** — paid, requires an account at [console.anthropic.com](https://console.anthropic.com).
  - **Mistral API key** — offers a free tier (paid plans also available), requires an account at [console.mistral.ai](https://console.mistral.ai).

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/404-researcher/research-assistant.git
cd research-assistant
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
```

On Windows:
```bash
.venv\Scripts\pip install -r requirements.txt
```

On macOS/Linux:
```bash
.venv/bin/pip install -r requirements.txt
```

### 3. (Optional) Set up an API key

Only needed if you want to use OpenAI, Anthropic, or Mistral instead of the free local option.

```bash
cp .env.example .env
```

Then open `.env` and paste the key for the provider you'll use (you don't need all of them):
```
OPENAI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
MISTRAL_API_KEY=your-key-here
```

### 4. (Optional) Set up Ollama for free local AI

1. Install [Ollama](https://ollama.com).
2. Pull the model used by default:
   ```bash
   ollama pull mistral
   ```
3. Before searching, start the Ollama server in a terminal:
   ```bash
   ollama serve
   ```

## Running the app

**Windows:** double-click `lancer.bat`, or run:
```bash
.venv\Scripts\python.exe -m streamlit run app.py
```

**macOS/Linux:**
```bash
.venv/bin/python -m streamlit run app.py
```

Your browser will open automatically at `http://localhost:8501`.

## Project structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit interface — the whole UI lives here |
| `search.py` | Connectors for all 29 academic database APIs, de-duplication |
| `summarizer.py` | AI scoring, per-article summaries, and report generation |
| `query_translation.py` | Query translation and article language filtering |
| `citation.py` | Citation formatting (APA / BibTeX / IEEE) |
| `pdf_export.py` / `excel_export.py` | Report export to PDF / Excel |
| `pdf_upload.py` | Text extraction from uploaded PDFs |
| `history.py` | Local search history (saved to `history.json`, not versioned) |

## Optional dependencies

The app runs without these, but the related feature is disabled if they're missing:

| Package | Enables |
|---|---|
| `reportlab` | PDF report export |
| `openpyxl` | Excel report export |
| `pymupdf` | PDF upload & text extraction |
| `langdetect` | Article language detection/filter |

They're already listed in `requirements.txt`, so a normal `pip install -r requirements.txt` covers everything.

## Contributing

Issues and pull requests are welcome — this started as a personal project and is shared in the hope it's useful to other students and researchers. Feel free to suggest a new database source, a bug fix, or a UI improvement. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## License

[MIT](LICENSE) — free to use, modify, and distribute.
