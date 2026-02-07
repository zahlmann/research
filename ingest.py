"""PDF ingestion pipeline: text extraction, image description, chunking, embedding."""

import base64
import json
import logging
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF
from openai import OpenAI

import db

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path(__file__).parent


def slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:80]


def update_status(meta_path: Path, stage: str, **extra):
    """Update meta.json with current ingestion status."""
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta["status"] = stage
    meta.update(extra)
    meta_path.write_text(json.dumps(meta, indent=2))


def extract_fulltext(doc: fitz.Document) -> str:
    """Extract full text from PDF with page markers."""
    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        pages.append(f"--- PAGE {page_num + 1} ---\n{text}")
    return "\n\n".join(pages)


def extract_and_describe_images(doc: fitz.Document, img_dir: Path, client: OpenAI) -> list[dict]:
    """Extract images from PDF, describe with gpt-4o-mini, save with descriptive names."""
    img_dir.mkdir(exist_ok=True)
    images = []
    fig_num = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            image_bytes = base_image["image"]
            ext = base_image["ext"]

            # Skip tiny images (icons, bullets, etc.)
            if len(image_bytes) < 5000:
                continue

            # Get description from gpt-4o-mini
            b64 = base64.b64encode(image_bytes).decode()
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": (
                                "Describe this image in 5-10 words for a filename. "
                                "Be specific about what it shows (e.g. 'training loss curve over epochs'). "
                                "Only output the description, nothing else."
                            )},
                            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}}
                        ]
                    }],
                    max_tokens=50,
                )
                description = response.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"Failed to describe image on page {page_num + 1}: {e}")
                description = f"image-page-{page_num + 1}"

            slug_desc = slugify(description)
            fig_num += 1
            filename = f"fig{fig_num}-{slug_desc}.{ext}"
            (img_dir / filename).write_bytes(image_bytes)
            images.append({"filename": filename, "page": page_num + 1, "description": description})

    return images


def heuristic_chunk(doc: fitz.Document) -> list[dict]:
    """Split PDF into chunks using font-size changes and paragraph gaps.

    Returns list of {text, page, block_index}.
    Target: ~200-400 words per chunk.
    """
    chunks = []
    current_text = ""
    current_page = 1
    current_block_idx = 0
    block_counter = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if block.get("type") != 0:  # text blocks only
                continue

            block_text = ""
            is_heading = False
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    font_size = span.get("size", 12)
                    # Heuristic: font size > 14 likely a heading
                    if font_size > 14:
                        is_heading = True
                    block_text += text + " "
                block_text += "\n"

            block_text = block_text.strip()
            if not block_text:
                continue

            block_counter += 1

            # Start new chunk on headings or when current chunk is large enough
            word_count = len(current_text.split())
            if is_heading and current_text.strip() and word_count > 50:
                chunks.append({
                    "text": current_text.strip(),
                    "page": current_page,
                    "block_index": current_block_idx,
                })
                current_text = ""
                current_page = page_num + 1
                current_block_idx = block_counter

            current_text += block_text + "\n\n"

            # Split if we exceed ~400 words
            if len(current_text.split()) >= 400:
                chunks.append({
                    "text": current_text.strip(),
                    "page": current_page,
                    "block_index": current_block_idx,
                })
                current_text = ""
                current_page = page_num + 1
                current_block_idx = block_counter + 1

    # Don't forget the last chunk
    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "page": current_page,
            "block_index": current_block_idx,
        })

    return chunks


def embed_chunks(chunks: list[dict], client: OpenAI) -> list[list[float]]:
    """Embed all chunks in batches via OpenAI."""
    texts = [c["text"] for c in chunks]
    embeddings = []

    # OpenAI allows up to 2048 inputs per batch
    batch_size = 512
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model="text-embedding-3-small", input=batch)
        embeddings.extend([d.embedding for d in resp.data])

    return embeddings


def generate_claude_md(slug_dir: Path) -> None:
    """Generate the per-PDF CLAUDE.md that instructs claude -p."""
    content = f"""# PDF Research Context

You are answering questions about this document.

## 1. Semantic search — find relevant chunks

Run:
```
uv run python {RESEARCH_DIR}/search_cli.py "query" --db chunks.db --top 5
```

Replace "query" with a search query derived from the user's question.

Returns matching chunks with their text and page numbers.

## 2. Get context — search fulltext for chunk matches

The file fulltext.txt has the full document text with page markers (--- PAGE N ---).
After finding chunks via search, grep for a distinctive phrase from each chunk
in fulltext.txt to locate it, then read the surrounding lines for context.
Do NOT read the entire file — just the relevant sections.

## 3. Images — check for relevant figures

The img/ folder contains images extracted from the PDF.
Filenames ARE descriptions (e.g. "fig1-neural-network-architecture-diagram.png").
List the files, identify which ones are relevant to the question by their names,
and analyze those images if they would help answer the question.

## Instructions
- Always start with semantic search (step 1)
- Get context for the best matches (step 2)
- Check images if the question involves figures, diagrams, or visual content (step 3)
- Use WebSearch for external knowledge when helpful
- Cite page numbers

## Answer style
- Be conversational but maintain mathematical, fact-based, and engineering rigor
- Talk like a knowledgeable colleague, not a textbook
- Use LaTeX for math — the UI renders it via KaTeX
- Use simple LaTeX: inline `$x^2$` and display `$$\\sum_{{i=1}}^n x_i$$`
- Avoid complex LaTeX environments (no align, gather, cases) — stick to basic
  expressions with `$...$` for inline and `$$...$$` for display math
- Keep answers focused and substantive — no filler
"""
    (slug_dir / "CLAUDE.md").write_text(content)


def ingest_pdf(slug: str) -> None:
    """Full ingestion pipeline for a PDF."""
    slug_dir = RESEARCH_DIR / "pdfs" / slug
    pdf_path = slug_dir / "document.pdf"
    meta_path = slug_dir / "meta.json"
    db_path = slug_dir / "chunks.db"

    client = OpenAI()
    doc = fitz.open(str(pdf_path))

    # Update meta with page count and title
    title = doc.metadata.get("title", "") or slug.replace("-", " ").title()
    update_status(meta_path, "extracting", title=title, pages=len(doc))

    # 1. Extract full text
    logger.info(f"[{slug}] Extracting text...")
    fulltext = extract_fulltext(doc)
    (slug_dir / "fulltext.txt").write_text(fulltext)

    # 2. Extract and describe images
    update_status(meta_path, "describing_images", title=title, pages=len(doc))
    logger.info(f"[{slug}] Extracting and describing images...")
    img_dir = slug_dir / "img"
    images = extract_and_describe_images(doc, img_dir, client)
    logger.info(f"[{slug}] Described {len(images)} images")

    # 3. Heuristic chunking
    update_status(meta_path, "chunking", title=title, pages=len(doc))
    logger.info(f"[{slug}] Chunking text...")
    chunks = heuristic_chunk(doc)
    logger.info(f"[{slug}] Created {len(chunks)} chunks")

    if not chunks:
        update_status(meta_path, "ready", title=title, pages=len(doc), chunks=0, images=len(images))
        doc.close()
        return

    # 4. Embed chunks
    update_status(meta_path, "embedding", title=title, pages=len(doc))
    logger.info(f"[{slug}] Embedding {len(chunks)} chunks...")
    embeddings = embed_chunks(chunks, client)

    # 5. Store in sqlite-vec
    logger.info(f"[{slug}] Storing in database...")
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    for chunk, emb in zip(chunks, embeddings):
        db.insert_chunk(conn, chunk["text"], chunk["page"], chunk["block_index"], emb)
    conn.commit()
    conn.close()

    # 6. Generate CLAUDE.md
    generate_claude_md(slug_dir)

    # 7. Done
    update_status(meta_path, "ready", title=title, pages=len(doc), chunks=len(chunks), images=len(images))
    doc.close()
    logger.info(f"[{slug}] Ingestion complete: {len(chunks)} chunks, {len(images)} images")
