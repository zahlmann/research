"""CLI search tool — called by claude -p during Q&A.

Usage:
    uv run python /Users/johann/Dropbox/apps/research/search_cli.py "query" --db chunks.db --top 5
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

import db


def main():
    parser = argparse.ArgumentParser(description="Search PDF chunks by semantic similarity")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--db", required=True, help="Path to chunks.db")
    parser.add_argument("--top", type=int, default=5, help="Number of results")
    args = parser.parse_args()

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

    client = OpenAI()
    resp = client.embeddings.create(model="text-embedding-3-small", input=args.query)
    query_embedding = resp.data[0].embedding

    conn = db.get_connection(args.db)
    results = db.search(conn, query_embedding, top_k=args.top)
    conn.close()

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"--- Result {i} (page {r['page']}, distance {r['distance']:.4f}) ---")
        print(r["text"])
        print()


if __name__ == "__main__":
    main()
