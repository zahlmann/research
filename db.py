"""sqlite-vec helpers for chunk storage and vector search."""

import sqlite3
import struct
from pathlib import Path

import sqlite_vec


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with sqlite-vec loaded."""
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_db(db_path: str | Path) -> None:
    """Create the chunks table and vec0 virtual table."""
    conn = get_connection(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            page INTEGER NOT NULL,
            block_index INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            embedding float[1536]
        )
    """)
    conn.commit()
    conn.close()


def serialize_f32(vec: list[float]) -> bytes:
    """Serialize a list of floats to a compact binary format for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def insert_chunk(conn: sqlite3.Connection, text: str, page: int, block_index: int, embedding: list[float]) -> int:
    """Insert a chunk and its embedding. Returns the chunk id."""
    cur = conn.execute(
        "INSERT INTO chunks (text, page, block_index) VALUES (?, ?, ?)",
        (text, page, block_index),
    )
    chunk_id = cur.lastrowid
    conn.execute(
        "INSERT INTO chunk_embeddings (rowid, embedding) VALUES (?, ?)",
        (chunk_id, serialize_f32(embedding)),
    )
    return chunk_id


def search(conn: sqlite3.Connection, query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """Search for the most similar chunks. Returns list of {id, text, page, block_index, distance}."""
    rows = conn.execute(
        """
        SELECT c.id, c.text, c.page, c.block_index, v.distance
        FROM chunk_embeddings v
        JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (serialize_f32(query_embedding), top_k),
    ).fetchall()
    return [
        {"id": r[0], "text": r[1], "page": r[2], "block_index": r[3], "distance": r[4]}
        for r in rows
    ]
