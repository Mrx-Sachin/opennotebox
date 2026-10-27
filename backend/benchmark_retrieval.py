#!/usr/bin/env python3
"""Retrieval benchmark: measures BM25 (Postgres FTS) search latency.

Seeds synthetic chunks under reserved document ids (900_000_000+), runs N
search queries through the real retrieval.search() code path against a real
Postgres, prints p50/p95/p99, and removes the synthetic rows afterwards.

Usage:
    # Against the local Docker Postgres (defaults match docker-compose.yml):
    python benchmark_retrieval.py

    # Custom:
    python benchmark_retrieval.py --documents 50 --chunks-per-doc 200 \
        --queries 200 --top-k 8

Output (one line per run):
    BM25 search: n=200 p50=X.Xms p95=X.Xms p99=X.Xms max=X.Xms min=X.Xms
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DOC_ID_BASE = 900_000_000  # reserved range, never collides with real documents

VOCAB = (
    "memory garbage collection jit compiler trace spidermonkey engine heap "
    "allocation bytecode interpreter register performance optimization "
    "thread lock barrier garbage object pointer reference counting nursery "
    "tenuring mutator collector marking sweeping compaction frame stack "
    "closure function invocation polymorphism inline cache branch prediction"
).split()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def seed_chunks(db, documents: int, chunks_per_doc: int) -> int:
    rng = random.Random(42)
    inserted = 0
    for d in range(documents):
        doc_id = DOC_ID_BASE + d
        for i in range(chunks_per_doc):
            words = [rng.choice(VOCAB) for _ in range(rng.randint(60, 120))]
            db.execute(
                text(
                    "INSERT INTO chunk (document_id, page_number, chunk_index, text)"
                    " VALUES (:d, :p, :i, :t)"
                ),
                {
                    "d": doc_id,
                    "p": i % 20 + 1,
                    "i": i,
                    "t": " ".join(words),
                },
            )
            inserted += 1
    db.commit()
    return inserted


def cleanup(db) -> None:
    db.execute(
        text("DELETE FROM chunk WHERE document_id >= :base"), {"base": DOC_ID_BASE}
    )
    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--database-url",
        default="postgresql+psycopg://opennotebox:opennotebox@127.0.0.1:5432/open_notebox",
    )
    ap.add_argument("--documents", type=int, default=50)
    ap.add_argument("--chunks-per-doc", type=int, default=200)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument(
        "--keep",
        action="store_true",
        help="keep seeded chunks after the run (for repeating the benchmark)",
    )
    args = ap.parse_args()

    from app.services import retrieval

    engine = create_engine(args.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        total = seed_chunks(db, args.documents, args.chunks_per_doc)
        doc_ids = [DOC_ID_BASE + d for d in range(args.documents)]
        print(f"seeded {total} synthetic chunks across {args.documents} documents")

        rng = random.Random(7)

        def make_phrases() -> list[str]:
            n = rng.randint(1, 3)
            return [
                " ".join(rng.sample(VOCAB, rng.randint(1, 3))) for _ in range(n)
            ]

        queries = [make_phrases() for _ in range(args.queries)]

        # Warm-up: connection setup + first plan cache runs must not skew p50.
        for q in queries[:5]:
            retrieval.search(db, doc_ids, q, top_k=args.top_k)

        latencies: list[float] = []
        hits = 0
        for q in queries:
            t0 = time.perf_counter()
            results = retrieval.search(db, doc_ids, q, top_k=args.top_k)
            latencies.append((time.perf_counter() - t0) * 1000)
            hits += len(results)

        print(
            f"BM25 search: n={len(latencies)} hits={hits} "
            f"p50={percentile(latencies, 50):.1f}ms "
            f"p95={percentile(latencies, 95):.1f}ms "
            f"p99={percentile(latencies, 99):.1f}ms "
            f"min={min(latencies):.1f}ms max={max(latencies):.1f}ms "
            f"mean={statistics.mean(latencies):.1f}ms"
        )
        return 0
    finally:
        if not args.keep:
            cleanup(db)
            print("cleaned up synthetic chunks")
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
