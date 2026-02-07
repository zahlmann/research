# research

Local PDF research tool. Drop in PDFs, they get chunked, embedded, and images extracted+described in the background. Then select text in the viewer and ask questions — answered by Claude Code searching the vector DB and surrounding context.

## Requirements

- [OpenAI API key](https://platform.openai.com/api-keys) — for embeddings and image descriptions
- [Claude Pro/Max subscription](https://claude.ai) — for Q&A via Claude Code
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — must be installed and authenticated (`npm install -g @anthropic-ai/claude-code`)

## Setup

```
cp .env.example .env
# add your OPENAI_API_KEY to .env
uv sync
uv run uvicorn server:app --reload --port 8000
```

Open http://localhost:8000, drop a PDF, wait for ingestion, select text, ask questions.
