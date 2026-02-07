"""FastAPI server: upload, status, PDF serving, Q&A streaming."""

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ingest import ingest_pdf

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESEARCH_DIR = Path(__file__).parent
PDFS_DIR = RESEARCH_DIR / "pdfs"
PDFS_DIR.mkdir(exist_ok=True)

app = FastAPI()


def make_slug(filename: str) -> str:
    """Create a filesystem-safe slug from a filename."""
    name = Path(filename).stem
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:80] or "document"


# --- API Endpoints ---


@app.get("/api/pdfs")
async def list_pdfs():
    """List all PDFs with their status."""
    pdfs = []
    if not PDFS_DIR.exists():
        return pdfs
    for slug_dir in sorted(PDFS_DIR.iterdir()):
        if not slug_dir.is_dir():
            continue
        meta_path = slug_dir / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            meta = {"status": "unknown"}
        meta["slug"] = slug_dir.name
        pdfs.append(meta)
    return pdfs


@app.post("/api/upload")
async def upload_pdf(file: UploadFile, background_tasks: BackgroundTasks):
    """Upload a PDF and start background ingestion."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return {"error": "Only PDF files are accepted"}, 400

    slug = make_slug(file.filename)

    # Handle duplicate slugs
    base_slug = slug
    counter = 1
    while (PDFS_DIR / slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    slug_dir = PDFS_DIR / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "img").mkdir(exist_ok=True)

    # Save PDF
    pdf_path = slug_dir / "document.pdf"
    content = await file.read()
    pdf_path.write_bytes(content)

    # Initial meta
    meta = {"status": "queued", "title": slug.replace("-", " ").title(), "slug": slug}
    (slug_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # Start background ingestion
    background_tasks.add_task(ingest_pdf, slug)

    return {"slug": slug, "status": "queued"}


@app.get("/api/pdfs/{slug}/status")
async def pdf_status(slug: str):
    """Get ingestion status for a PDF."""
    meta_path = PDFS_DIR / slug / "meta.json"
    if not meta_path.exists():
        return {"error": "Not found"}, 404
    return json.loads(meta_path.read_text())


@app.get("/api/pdfs/{slug}/pdf")
async def serve_pdf(slug: str):
    """Serve the raw PDF file."""
    pdf_path = PDFS_DIR / slug / "document.pdf"
    if not pdf_path.exists():
        return {"error": "Not found"}, 404
    return FileResponse(pdf_path, media_type="application/pdf")


class AskRequest(BaseModel):
    question: str
    selected_text: str = ""
    page: int | None = None


@app.post("/api/pdfs/{slug}/ask")
async def ask_question(slug: str, req: AskRequest):
    """Q&A endpoint with SSE streaming via claude -p."""
    slug_dir = PDFS_DIR / slug

    if not slug_dir.exists():
        return {"error": "PDF not found"}, 404

    # Build the prompt for claude -p
    prompt_parts = []
    if req.selected_text:
        prompt_parts.append(f"Selected text from page {req.page or '?'}:\n\"\"\"\n{req.selected_text}\n\"\"\"")
    prompt_parts.append(f"Question: {req.question}")
    prompt = "\n\n".join(prompt_parts)

    async def stream_response():
        sent_text = set()  # track text we've already sent to avoid duplicates

        try:
            process = await asyncio.create_subprocess_exec(
                "claude", "-p", prompt,
                "--output-format", "stream-json",
                "--verbose",
                "--allowedTools", "Bash(*)", "Read(*)", "Grep(*)", "Glob(*)", "WebSearch", "WebFetch",
                cwd=str(slug_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async for line in process.stdout:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                # Assistant messages contain text blocks
                if event_type == "assistant":
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if block.get("type") == "text":
                            text = block["text"]
                            if text not in sent_text:
                                sent_text.add(text)
                                yield f"data: {json.dumps({'text': text})}\n\n"

                # Final result — use result text if nothing streamed yet
                elif event_type == "result":
                    result_text = event.get("result", "")
                    if result_text and not sent_text:
                        yield f"data: {json.dumps({'text': result_text, 'final': True})}\n\n"

            await process.wait()

        except Exception as e:
            logger.error(f"Error in Q&A stream: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Serve static files and index.html
app.mount("/static", StaticFiles(directory=str(RESEARCH_DIR / "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(str(RESEARCH_DIR / "static" / "index.html"))
