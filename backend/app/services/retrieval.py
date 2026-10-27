"""Lexical retrieval index for document chat.

Paragraph-level chunks stored in a plain table with PostgreSQL full-text
search (tsvector/tsquery with GIN index + BM25-like ranking). Kept as raw
SQL on purpose: this is a derived search index, not domain data, so it
stays out of the SQLAlchemy models and is rebuilt lazily from ExtractedText
on first use.
"""
from __future__ import annotations

import re
import struct
import threading
from typing import Dict, Iterable, List, Optional, Sequence

import httpx
from loguru import logger
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.config import settings

# Chunk sizing in characters (~4 chars/token: target 300-500 tokens).
TARGET_CHARS = 1600
MAX_CHARS = 2400

_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how in is it its of on or "
    "that the this to was what when where which who why will with does do did "
    "about into their there these those than then also can could should would "
    # Book-meta words: they appear in running headers/TOC on nearly every page,
    # so they only pull in junk matches.
    "book chapter chapters section sections page pages author document text".split()
)

# PostgreSQL full-text search schema.
# Uses a tsvector column with a GIN index for fast BM25-like ranking.
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS chunk (
        id SERIAL PRIMARY KEY,
        document_id INTEGER NOT NULL,
        page_number INTEGER NOT NULL,
        chunk_index INTEGER NOT NULL,
        text TEXT NOT NULL,
        tsvectors TSVECTOR GENERATED ALWAYS AS (
            to_tsvector('english', COALESCE(text, ''))
        ) STORED,
        emb BYTEA
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk(document_id, page_number, chunk_index)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_fts ON chunk USING GIN(tsvectors)",
]


def ensure_schema(db: Session) -> None:
    for stmt in _SCHEMA:
        db.execute(sql(stmt))
    db.commit()


def _split_page(content: str) -> List[str]:
    """Split page text into paragraph chunks of roughly TARGET_CHARS."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in paragraphs:
        if buf and len(buf) + len(p) + 2 > TARGET_CHARS:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
        while len(buf) > MAX_CHARS:
            # Hard split over-long paragraphs at a sentence boundary if possible.
            cut = buf.rfind(". ", 0, MAX_CHARS)
            cut = cut + 1 if cut > TARGET_CHARS // 2 else MAX_CHARS
            chunks.append(buf[:cut].strip())
            buf = buf[cut:].strip()
    if buf:
        chunks.append(buf)
    return chunks


def _is_junk(text: str) -> bool:
    """Skip TOC/index-style chunks: they match almost any keyword query and
    crowd out real prose."""
    if re.search(r"\.{5,}", text):  # TOC dot leaders
        return True
    first_line = text.lstrip().split("\n", 1)[0]
    if re.match(r"^(?:\d+\s+)?(?:Index|Contents)(?:\s+\d+)?$", first_line.strip()):
        return True  # running header of an index/contents page
    digits = sum(c.isdigit() for c in text)
    return digits / max(len(text), 1) > 0.15


def delete_document_chunks(db: Session, doc_id: int) -> int:
    """Remove every chunk row (text + embedding) belonging to a document.

    The chunk table is a raw-SQL derived index with no FK to documents, so
    cascade does not happen on its own: without this call, deleting or
    re-parsing a document leaves its old chunks (full text included) in the
    table forever, where BM25 and semantic search keep returning them.

    Transaction-neutral: the caller commits, so this can run as part of a
    larger delete/reparse transaction. Returns 0 when the index has not been
    created yet on this deployment (nothing was ever indexed).
    """
    if not sa_inspect(db.bind).has_table("chunk"):
        return 0
    result = db.execute(
        sql("DELETE FROM chunk WHERE document_id = :d"), {"d": doc_id}
    )
    return result.rowcount


def index_document(db: Session, doc_id: int, force: bool = False) -> None:
    """Build the chunk index for a document.

    Without force this is a no-op when an index already exists. With force
    the old chunk set is replaced in the same transaction, so a re-parse
    cannot leave stale text behind.

    A transaction-level advisory lock serialises concurrent indexing of the
    same document: two simultaneous chats otherwise both pass the existence
    check and each insert the full chunk set (no unique constraint to stop
    them), permanently duplicating every hit in retrieval.
    """
    ensure_schema(db)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            sql("SELECT pg_advisory_xact_lock(:k)"),
            {"k": int(doc_id) & 0x7FFFFFFF},
        )
    exists = db.execute(
        sql("SELECT 1 FROM chunk WHERE document_id = :d LIMIT 1"), {"d": doc_id}
    ).first()
    if exists and not force:
        return
    if exists:
        db.execute(sql("DELETE FROM chunk WHERE document_id = :d"), {"d": doc_id})

    pages = db.execute(
        sql(
            "SELECT page_number, content FROM extracted_text "
            "WHERE document_id = :d ORDER BY page_number"
        ),
        {"d": doc_id},
    ).all()

    count = 0
    for page_number, content in pages:
        if not content:
            continue
        i = 0
        for chunk_text in _split_page(content):
            if _is_junk(chunk_text):
                continue
            db.execute(
                sql(
                    "INSERT INTO chunk (document_id, page_number, chunk_index, text) "
                    "VALUES (:d, :p, :i, :t)"
                ),
                {"d": doc_id, "p": page_number, "i": i, "t": chunk_text},
            )
            i += 1
            count += 1
    db.commit()
    logger.info(f"Indexed doc {doc_id}: {count} chunks")


# ---------------------------------------------------------------------------
# Embeddings (hybrid retrieval: BM25 covers keyword lookups, embeddings cover
# paraphrase questions whose terms never appear verbatim in the text)

EMBED_MODEL = "nomic-embed-text"
EMBED_BATCH = 32


def _embed_api_url() -> str:
    # ollama_base_url points at the OpenAI-compatible /v1; embeddings use the
    # native endpoint. Deliberately NOT the active LLM backend: nomic-embed-text
    # is served by Ollama, and vLLM hosts chat models only. When Ollama is down
    # both callers below fall back to lexical-only retrieval.
    return settings.ollama_base_url.rsplit("/v1", 1)[0] + "/api/embed"


# Connect fast so a dead Ollama degrades to lexical-only in seconds rather
# than stalling every chat message for the full read timeout.
EMBED_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)


def embed_texts(texts: List[str], is_query: bool = False) -> List[List[float]]:
    """Embed texts via Ollama, L2-normalized so cosine similarity = dot."""
    prefix = "search_query: " if is_query else "search_document: "
    response = httpx.post(
        _embed_api_url(),
        json={"model": EMBED_MODEL, "input": [prefix + t[:2000] for t in texts]},
        timeout=EMBED_TIMEOUT,
    )
    response.raise_for_status()
    normalized = []
    for v in response.json()["embeddings"]:
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        normalized.append([x / norm for x in v])
    return normalized


def _pack(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> List[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def list_unembedded_chunk_ids(
    db: Session, doc_id: int, *, limit: int = 64
) -> List[int]:
    """Return up to `limit` chunk ids still missing embeddings (reading order)."""
    rows = db.execute(
        sql(
            "SELECT id FROM chunk WHERE document_id = :d AND emb IS NULL "
            "ORDER BY page_number, chunk_index LIMIT :n"
        ),
        {"d": doc_id, "n": limit},
    ).all()
    return [int(r[0]) for r in rows]


def ensure_embeddings(
    db: Session,
    doc_id: int,
    *,
    only_chunk_ids: Optional[Sequence[int]] = None,
) -> bool:
    """Embed chunks of the document that lack a vector.

    only_chunk_ids: when set, only those rows are considered. First-chat
    latency used to pay for embedding the entire book (hundreds of Ollama
    round-trips) before BM25 results could even be used for hybrid search.
    Callers pass the BM25 window first, then backfill the rest in the
    background via schedule_full_embeddings.
    """
    if only_chunk_ids is not None:
        ids = [int(i) for i in only_chunk_ids]
        if not ids:
            return True
        placeholders = ",".join(f":c{i}" for i in range(len(ids)))
        rows = db.execute(
            sql(
                f"SELECT id, text FROM chunk WHERE document_id = :d "
                f"AND emb IS NULL AND id IN ({placeholders})"
            ),
            {"d": doc_id, **{f"c{i}": cid for i, cid in enumerate(ids)}},
        ).all()
    else:
        rows = db.execute(
            sql("SELECT id, text FROM chunk WHERE document_id = :d AND emb IS NULL"),
            {"d": doc_id},
        ).all()
    if not rows:
        return True
    try:
        for start in range(0, len(rows), EMBED_BATCH):
            batch = rows[start:start + EMBED_BATCH]
            vectors = embed_texts([r[1] for r in batch])
            for (cid, _), vec in zip(batch, vectors):
                db.execute(
                    sql("UPDATE chunk SET emb = :e WHERE id = :i"),
                    {"e": _pack(vec), "i": cid},
                )
            db.commit()
        logger.info(
            f"Embedded {len(rows)} chunks for doc {doc_id}"
            + (" (scoped)" if only_chunk_ids is not None else "")
        )
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"Embedding unavailable ({e}); falling back to lexical-only")
        return False


def _background_embed_docs(doc_ids: Iterable[int]) -> None:
    """Fill remaining null embeddings after the request path has returned."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        for doc_id in doc_ids:
            try:
                ensure_embeddings(db, doc_id)
            except Exception as e:
                logger.warning(f"Background embed for doc {doc_id} failed: {e}")
    finally:
        db.close()


def schedule_full_embeddings(doc_ids: Sequence[int]) -> None:
    """Fire-and-forget full-document embed after a scoped first pass."""
    ids = [int(d) for d in doc_ids]
    if not ids:
        return
    t = threading.Thread(
        target=_background_embed_docs,
        args=(ids,),
        name="chunk-embed-backfill",
        daemon=True,
    )
    t.start()


def semantic_search(
    db: Session, doc_ids: List[int], queries: List[str], top_k: int = 12
) -> List[Dict]:
    """Cosine similarity against all embedded chunks.

    Scores each chunk by its best match across the queries (the raw question
    plus rewritten phrasings) - reformulations often land far closer to the
    relevant passage than the user's own wording.
    """
    try:
        query_vecs = embed_texts(queries, is_query=True)
    except Exception as e:
        logger.warning(f"Query embedding failed ({e}); lexical-only")
        return []

    placeholders = ",".join(f":doc{i}" for i in range(len(doc_ids)))
    rows = db.execute(
        sql(
            f"SELECT id, document_id, page_number, chunk_index, text, emb "
            f"FROM chunk WHERE document_id IN ({placeholders}) AND emb IS NOT NULL"
        ),
        {f"doc{i}": d for i, d in enumerate(doc_ids)},
    ).all()

    scored = []
    for cid, did, page, idx, chunk_text, blob in rows:
        vec = _unpack(blob)
        sims = [sum(a * b for a, b in zip(qv, vec)) for qv in query_vecs]
        scored.append(
            {
                "chunk_id": cid,
                "doc_id": did,
                "page": page,
                "chunk_index": idx,
                "text": chunk_text,
                "score": -max(sims),  # negative like bm25: lower = better
                "snippet": chunk_text[:300],
                "sims": sims,
            }
        )

    # Per-query quotas: guarantee each query contributes its own best hits,
    # so one query's dense neighborhood can't crowd the others out of top_k.
    quota = 4
    selected: Dict[int, Dict] = {}
    for qi in range(len(query_vecs)):
        for rank, h in enumerate(sorted(scored, key=lambda h: -h["sims"][qi])[:quota]):
            if rank == 0:
                # Each query's best hit is must-keep downstream: the answer
                # engine feeds these to the LLM even if its reranker balks.
                h["sem_top"] = True
            selected[h["chunk_id"]] = h
    for h in sorted(scored, key=lambda h: h["score"]):
        if len(selected) >= top_k:
            break
        selected.setdefault(h["chunk_id"], h)
    top = sorted(selected.values(), key=lambda h: h["score"])
    for h in top:
        del h["sims"]

    # Use FTS to surface relevant snippets instead of chunk heads.
    terms = set()
    for q in queries:
        terms.update(_content_terms(q))
    if terms:
        # Build a tsquery from terms for highlighting.
        match = " & ".join(f"'{t}'" for t in terms)
        for h in top:
            try:
                row = db.execute(
                    sql(
                        "SELECT ts_headline('english', text, :q, "
                        "'MaxWords=32, MinWords=16, MaxFragments=1, "
                        "StartSel=<mark>, StopSel=</mark>, "
                        "FragmentDelimiter= ... ') AS hl "
                        "FROM chunk WHERE id = :cid AND "
                        "tsvectors @@ to_tsquery('english', :q)"
                    ),
                    {"q": match, "cid": h["chunk_id"]},
                ).first()
                if row and row[0]:
                    # Strip <mark> tags for plain text snippet.
                    h["snippet"] = row[0].replace("<mark>", "").replace("</mark>", "")
            except Exception:
                db.rollback()  # clear poisoned transaction
                break
    return top


def _content_terms(phrase: str) -> List[str]:
    return [
        t for t in re.findall(r"[A-Za-z0-9']+", phrase.lower())
        if len(t) > 1 and t not in _STOPWORDS
    ]


def _query_tiers(phrase: str) -> List[str]:
    """PostgreSQL FTS query variants for a phrase, most selective first.

    Tier 0: exact phrase in single quotes (works with the english
    configuration's thesaurus/stemming).
    Tier 1: all content terms combined with AND.
    Tier 2: any content term (recall fallback; near-useless alone for common
    words, so it only fills leftover slots).
    """
    terms = _content_terms(phrase)
    if not terms:
        return []
    exact = '"' + " ".join(re.findall(r"[A-Za-z0-9']+", phrase.lower())) + '"'
    tiers = [exact]
    if len(terms) > 1:
        tiers.append(" & ".join(f"'{t}'" for t in terms))
    tiers.append(" | ".join(f"'{t}'" for t in terms))
    return tiers


def search(
    db: Session,
    doc_ids: List[int],
    phrases: List[str],
    top_k: int = 8,
) -> List[Dict]:
    """BM25-like search across documents; merged over phrases, best score wins.

    Returns [{chunk_id, doc_id, page, chunk_index, text, score}] sorted by
    relevance (lowest score first), with up to one neighboring chunk
    merged into each of the two strongest hits for continuity.
    """
    placeholders = ",".join(f":doc{i}" for i in range(len(doc_ids)))
    params_docs = {f"doc{i}": d for i, d in enumerate(doc_ids)}

    best: Dict[int, Dict] = {}
    for phrase in phrases:
        for tier, match in enumerate(_query_tiers(phrase)):
            try:
                rows = db.execute(
                    sql(
                        f"""
                        SELECT c.id, c.document_id, c.page_number, c.chunk_index,
                               c.text,
                               ts_rank_cd(tsvectors, to_tsquery('english', :q)) AS score,
                               ts_headline('english', c.text,
                                   to_tsquery('english', :q),
                                   'MaxWords=32, MinWords=16, MaxFragments=1,
                                    StartSel=<mark>, StopSel=</mark>,
                                    FragmentDelimiter= ... ') AS snip
                        FROM chunk c
                        WHERE c.tsvectors @@ to_tsquery('english', :q)
                          AND c.document_id IN ({placeholders})
                        ORDER BY score DESC LIMIT :k
                        """
                    ),
                    {"q": match, "k": top_k * 2, **params_docs},
                ).all()
            except Exception:
                db.rollback()  # clear poisoned transaction so subsequent queries work
                continue  # malformed query from odd input; other tiers still run
            for cid, did, page, idx, chunk_text, score, snip in rows:
                key = (tier, -score)  # negate: ts_rank is float, higher = better
                if cid not in best or key < best[cid]["rank"]:
                    s = snip.replace("<mark>", "").replace("</mark>", "") if snip else None
                    best[cid] = {
                        "chunk_id": cid,
                        "doc_id": did,
                        "page": page,
                        "chunk_index": idx,
                        "text": chunk_text,
                        "score": -score,  # negate so lower = better (like BM25)
                        "snippet": s,
                        "rank": key,
                    }

    hits = sorted(best.values(), key=lambda h: h["rank"])[:top_k]

    # Merge the following neighbor chunk into the two strongest hits so
    # mid-thought excerpts read complete (substitute for index-time overlap).
    taken = {h["chunk_id"] for h in hits}
    for h in hits[:2]:
        row = db.execute(
            sql(
                "SELECT id, text FROM chunk WHERE document_id = :d AND page_number = :p "
                "AND chunk_index = :i"
            ),
            {"d": h["doc_id"], "p": h["page"], "i": h["chunk_index"] + 1},
        ).first()
        if row and row[0] not in taken and len(h["text"]) + len(row[1]) <= MAX_CHARS * 2:
            h["text"] = f"{h['text']}\n\n{row[1]}"
            taken.add(row[0])

    return hits
