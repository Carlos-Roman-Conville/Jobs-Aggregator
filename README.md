# Jobs Aggregator

Multi-source job posting aggregator with LLM-powered fit scoring and keyword overlap analysis.

## What it does

- **Ingests** job postings from Indeed (via Apify), USAJOBS, JobSpy (LinkedIn/Glassdoor/ZipRecruiter), Hacker News "Who's Hiring" threads, and RSS feeds
- **Deduplicates** by URL and normalized company+title
- **Summarizes** each posting with an LLM (OpenAI or Gemini) — extracts key requirements, seniority level, salary, verdict (strong_match / maybe / pass), and a 0-1 fit score calibrated against the user's career profile
- **Scores keyword overlap** between JD requirements and resume corpus (ATS-style heuristic)
- **Classifies** postings by category (IT helpdesk, IT general, operations, remote non-IT) and location policy (remote / hybrid / onsite within commute radius)
- **Serves** a Streamlit dashboard for browsing, filtering, and triaging the queue, plus a FastAPI backend for programmatic access

## Architecture

```
Sources (Indeed, USAJOBS, JobSpy, HN, RSS)
    │
    ▼
  Ingest → Dedup → Postgres (job_postings + job_pipeline_items)
    │
    ▼
  Summarize (OpenAI / Gemini) → fit_score, verdict, key_requirements
    │
    ▼
  Dashboard (Streamlit) + API (FastAPI)
```

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys
2. Start Postgres (Docker or local)
3. Install dependencies: `pip install -r requirements.txt`
4. Run the dashboard: `streamlit run job_dashboard.py`
5. Or run the API: `uvicorn api_server:app --host 127.0.0.1 --port 8000`

## Stack

- Python 3.10+
- PostgreSQL (psycopg2)
- OpenAI API / Google Gemini API
- FastAPI + Uvicorn
- Streamlit
- Apify (Indeed actor)
- python-jobspy (LinkedIn/Glassdoor/ZipRecruiter)
