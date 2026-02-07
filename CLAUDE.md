# Research — Local PDF Research Tool

FastAPI backend + vanilla HTML/JS frontend. PDFs are ingested in the background
(text extraction, image description, embedding), then queried via claude -p.

## Running
```
uv run uvicorn server:app --reload --port 8000
```

## Key files
- server.py — FastAPI endpoints (upload, status, PDF serving, Q&A streaming)
- ingest.py — PDF ingestion pipeline (PyMuPDF + OpenAI embeddings + gpt-4o-mini vision)
- db.py — sqlite-vec helpers
- search_cli.py — CLI search tool called by claude -p during Q&A
- static/ — frontend (PDF.js + text selection + SSE streaming)

## Per-PDF data
Each PDF lives in pdfs/<slug>/ with chunks.db, fulltext.txt, img/, meta.json, CLAUDE.md.
