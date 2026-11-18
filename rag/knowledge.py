"""Small, local lexical retrieval pilot for approved IBP guidance.

Only Markdown files deliberately placed in the knowledge directory are indexed.
This is a shared corpus, not a per-user document store.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path

DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "knowledge" / "ibp"
TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = frozenset("a an and are as at be by for from how i in is it of on or the to what which with".split())
MAX_FILE_BYTES = 256_000
MAX_CHUNK_CHARS = 1600


def _terms(text: str) -> list[str]:
    return [word for word in TOKEN.findall(text.lower()) if word not in STOPWORDS and len(word) > 1]


def _chunks(text: str):
    # Split by paragraphs, then bound long paragraphs without losing their text.
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        for start in range(0, len(paragraph), MAX_CHUNK_CHARS):
            chunk = paragraph[start:start + MAX_CHUNK_CHARS].strip()
            if chunk:
                yield chunk


def retrieve(query: str, directory: Path | None = None, limit: int = 3) -> list[dict]:
    """Return relevant excerpts and stable local source labels, or an empty list."""
    root = Path(directory or os.environ.get("IBP_KNOWLEDGE_DIR", DEFAULT_DIRECTORY))
    query_terms = set(_terms(query))
    if not query_terms or limit <= 0:
        return []
    documents = []
    for path in sorted(root.glob("*.md")) if root.is_dir() else []:
        try:
            if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        for number, chunk in enumerate(_chunks(content), 1):
            terms = Counter(_terms(chunk))
            if terms:
                documents.append(({"source": path.name, "section": number}, chunk, terms))
    # The shared snapshot is refreshed separately; no network call occurs here.
    if directory is None:
        from rag.sharepoint_knowledge import snapshot
        for item in snapshot():
            for number, chunk in enumerate(_chunks(item["text"]), 1):
                terms = Counter(_terms(chunk))
                if terms:
                    documents.append(({"source": item["source"], "section": number,
                                       "url": item["url"], "modified": item["modified"]}, chunk, terms))
    if not documents:
        return []
    frequencies = Counter(term for _, _, terms in documents for term in terms if term in query_terms)
    scored = []
    for metadata, chunk, terms in documents:
        matches = query_terms.intersection(terms)
        if not matches:
            continue
        score = sum((1 + math.log(terms[term])) * (1 + math.log((len(documents) + 1) / (frequencies[term] + 1))) for term in matches)
        scored.append((score, metadata, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]["source"], item[1]["section"]))
    return [{**metadata, "text": chunk} for _, metadata, chunk in scored[:min(limit, 5)]]
