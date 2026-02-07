# research

Local PDF research tool. Drop in PDFs, they get chunked, embedded, and images extracted+described in the background. Then select text in the viewer and ask questions — answered by Claude Code searching the vector DB and surrounding context.

## Setup

```
cp .env.example .env
# add your OPENAI_API_KEY to .env
uv sync
uv run uvicorn server:app --reload --port 8000
```

Open http://localhost:8000, drop a PDF, wait for ingestion, select text, ask questions.
