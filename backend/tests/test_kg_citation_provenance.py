"""Tests for knowledge graph citation provenance and original evidence grounding.

Verifies:
1. Cross-document same entity: entities with the same name across documents resolve
   only to their respective authorized document chunks and pages.
2. Missing page metadata: entities with missing/null page hints resolve to the true
   page from the document's actual chunk index, never fabricating page 1.
3. Graph-only synthetic text absence: generated graph descriptions/triplets are not
   standalone numbered citeable sources. Citations always ground to real passages.
4. Hybrid duplicate sources: when vector and graph retrieval find the same passage,
   sources are deduplicated by exact chunk_id/text, preserving distinct passages that
   share the same prefix.
5. Scoped document isolation: retrieval scoped to Document A never leaks or resolves
   chunks from Document B, even if both documents share identically named entities.
6. Graph capacity regression: entities and relationships beyond legacy arbitrary limits
   (such as 500 nodes) are fully loaded into the graph and remain searchable.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models import Document, ExtractedText
from app.models.kg import KgEntity, KgRelationship, KgExtractionState
from app.services import hybrid_rag
from app.services.kg_retrieval import retrieve_from_graph


def _create_doc(db, user_id, title, pages_text):
    """Helper to create a document with extracted text pages."""
    doc = Document(
        user_id=user_id,
        title=title,
        format="pdf",
        file_path=f"/tmp/{title}.pdf",
        file_size=1024,
        num_pages=len(pages_text),
        processing_status="extracted",
    )
    db.add(doc)
    db.flush()

    for p_num, content in pages_text.items():
        db.add(
            ExtractedText(
                document_id=doc.id,
                page_number=p_num,
                content=content,
            )
        )
    db.flush()
    return doc


def _create_entity(db, doc_id, name, pages=None):
    e = KgEntity(
        document_id=doc_id,
        name=name,
        name_normalized=name.lower().replace(" ", "_"),
        entity_type="concept",
        pages=json.dumps(pages) if isinstance(pages, list) else pages,
    )
    db.add(e)
    db.flush()
    return e


def _create_rel(db, doc_id, subj, rel, obj, page_number=None):
    r = KgRelationship(
        document_id=doc_id,
        subject_id=subj.id,
        object_id=obj.id,
        relationship=rel,
        page_number=page_number,
    )
    db.add(r)
    db.flush()
    return r


class TestKgCitationProvenance:
    def test_crossdoc_same_entity_resolves_to_correct_doc_and_page(self, db, test_user):
        """Cross-document isolation: same entity name in Doc A and Doc B must resolve

        strictly to the respective document's chunk and page number.
        """
        doc_a = _create_doc(
            db,
            test_user.id,
            "Document_A.pdf",
            {
                1: "Introductory remarks.",
                3: "The Transformer model in Document A uses self-attention mechanisms for vision.",
            },
        )
        doc_b = _create_doc(
            db,
            test_user.id,
            "Document_B.pdf",
            {
                1: "Table of contents.",
                7: "The Transformer architecture in Document B scales across sixty languages.",
            },
        )

        _create_entity(db, doc_a.id, "Transformer", pages=[3])
        _create_entity(db, doc_b.id, "Transformer", pages=[7])
        db.flush()

        # Query scoped only to Doc A
        result_a = retrieve_from_graph(db, [doc_a.id], "How does Transformer work?")
        chunks_a = result_a.get("candidate_chunks", [])

        assert len(chunks_a) >= 1
        for c in chunks_a:
            assert c["doc_id"] == doc_a.id
            assert c["page"] == 3
            assert "Document A" in c["text"]
            assert "Document B" not in c["text"]

        # Query scoped only to Doc B
        result_b = retrieve_from_graph(db, [doc_b.id], "How does Transformer work?")
        chunks_b = result_b.get("candidate_chunks", [])

        assert len(chunks_b) >= 1
        for c in chunks_b:
            assert c["doc_id"] == doc_b.id
            assert c["page"] == 7
            assert "Document B" in c["text"]
            assert "Document A" not in c["text"]

    def test_page_metadata_missing_resolves_to_true_chunk_page(self, db, test_user):
        """When entity/relationship page metadata is null/missing, retrieval must discover

        the true page from the document's chunk index without fabricating page 1.
        """
        doc = _create_doc(
            db,
            test_user.id,
            "Whisper_Paper.pdf",
            {
                1: "Title page and abstract.",
                5: "Whisper is a robust speech recognition model trained on thousands of hours of audio.",
            },
        )

        # Entity with pages=None, relationship with page_number=None
        w = _create_entity(db, doc.id, "Whisper", pages=None)
        sr = _create_entity(db, doc.id, "Speech Recognition", pages=None)
        _create_rel(db, doc.id, w, "enables", sr, page_number=None)
        db.flush()

        result = retrieve_from_graph(db, [doc.id], "Tell me about Whisper")
        candidates = result.get("candidate_chunks", [])

        assert len(candidates) >= 1
        matched = next(c for c in candidates if "Whisper is a robust" in c["text"])
        assert matched["doc_id"] == doc.id
        assert matched["page"] == 5  # True page, not 1 or None

    @pytest.mark.asyncio
    async def test_graph_only_synthetic_text_absence(self, db, test_user):
        """In graph-only mode, synthetic graph facts/triplets must never be formatted

        as numbered citeable sources. If candidate chunks exist, citations point to
        original passages; if no candidates exist, return no sources honestly.
        """
        doc = _create_doc(
            db,
            test_user.id,
            "Synthetic_Test.pdf",
            {
                1: "Document overview without relevant content.",
                4: "Quantum computing utilizes qubits for exponential speedup in simulation.",
            },
        )
        q = _create_entity(db, doc.id, "Quantum Computing", pages=[4])
        qu = _create_entity(db, doc.id, "Qubits", pages=[4])
        _create_rel(db, doc.id, q, "utilizes", qu, page_number=4)

        db.add(
            KgExtractionState(
                document_id=doc.id,
                status="completed",
                chunks_processed=2,
                total_chunks=2,
            )
        )
        db.flush()

        retrieval_res = hybrid_rag.retrieve_hybrid(
            db,
            [doc.id],
            question="What does quantum computing utilize?",
            phrases=[],
            rephrasings=[],
            use_vector=False,
            use_graph=True,
            user_id=test_user.id,
        )

        sources = retrieval_res["combined_sources"]
        assert len(sources) >= 1

        for s in sources:
            assert s["page"] == 4
            assert s["source_type"] == "document"
            assert "Knowledge Graph" not in s["doc_title"]
            assert not s["text"].startswith("Knowledge Graph Facts:")

    def test_hybrid_duplicate_sources_deduplicated_and_distinct_preserved(self, db, test_user):
        """When vector and graph retrieval find the same chunk, it is deduplicated.

        When distinct passages share the same prefix (first 100 chars), both are preserved.
        """
        shared_prefix = (
            "Deep learning scaling laws show consistent improvement across multiple "
            "speech recognition tasks and multilingual translation benchmarks "
        )
        passage_1 = f"{shared_prefix} when expanding compute budget by a factor of 10."
        passage_2 = f"{shared_prefix} when expanding training dataset volume by a factor of 10."

        doc = _create_doc(
            db,
            test_user.id,
            "Scaling_Study.pdf",
            {
                1: "Abstract.",
                3: f"{passage_1}\n\n{passage_2}",
            },
        )

        _create_entity(db, doc.id, "Scaling Laws", pages=[3])
        db.flush()

        with patch(
            "app.services.retrieval.embed_texts",
            side_effect=lambda texts, **kw: [[0.1, 0.2] for _ in texts],
        ):
            retrieval_res = hybrid_rag.retrieve_hybrid(
                db,
                [doc.id],
                question="What do scaling laws show?",
                phrases=["scaling laws", "consistent improvement"],
                rephrasings=[],
                use_vector=True,
                use_graph=True,
                user_id=test_user.id,
            )

        sources = retrieval_res["combined_sources"]
        p1_found = any("compute budget" in s["text"] for s in sources)
        p2_found = any("dataset volume" in s["text"] for s in sources)
        assert p1_found, "Passage 1 should be preserved"
        assert p2_found, "Passage 2 should be preserved"

        texts = [s["text"] for s in sources]
        assert len(texts) == len(set(texts)), "No identical passages should be duplicated"

    def test_scoped_doc_isolation_with_same_named_entity(self, db, test_user, second_user):
        """Retrieval scoped to Document A never leaks or returns chunks from Document B,

        even when both documents contain the exact same entity name.
        """
        doc_a = _create_doc(
            db,
            test_user.id,
            "Doc_User_A.pdf",
            {
                1: "Public overview.",
                2: "Attention mechanism in User A document enables contextual word embeddings.",
            },
        )
        doc_b = _create_doc(
            db,
            second_user.id,
            "Doc_User_B_Secret.pdf",
            {
                1: "Confidential.",
                4: "Attention mechanism in User B document contains secret proprietary algorithms.",
            },
        )

        _create_entity(db, doc_a.id, "Attention", pages=[2])
        _create_entity(db, doc_b.id, "Attention", pages=[4])
        db.flush()

        with patch(
            "app.services.retrieval.embed_texts",
            side_effect=lambda texts, **kw: [[0.1, 0.2] for _ in texts],
        ):
            retrieval_res = hybrid_rag.retrieve_hybrid(
                db,
                [doc_a.id],
                question="How does attention mechanism work?",
                phrases=["attention mechanism"],
                rephrasings=[],
                use_vector=True,
                use_graph=True,
                user_id=test_user.id,
            )

        sources = retrieval_res["combined_sources"]
        assert len(sources) >= 1

        for s in sources:
            assert s["doc_id"] == doc_a.id
            assert "User A" in s["text"]
            assert "User B" not in s["text"]
            assert "secret" not in s["text"].lower()

    def test_entity_beyond_500_cap_is_searchable(self, db, test_user):
        """Regression: graphs with more than 500 entities must not truncate nodes

        before question matching. An entity created beyond index 500 remains fully
        searchable.
        """
        doc = _create_doc(
            db,
            test_user.id,
            "Large_Graph_Doc.pdf",
            {
                1: "Large knowledge base document.",
                10: "The Zeta Unique Protocol provides decentralized distributed consensus.",
            },
        )

        # Populate 520 entities into the document
        for i in range(515):
            _create_entity(db, doc.id, f"Bulk_Concept_{i}", pages=[1])

        # Add the 516th entity which is the query target
        _create_entity(db, doc.id, "Zeta Unique Protocol", pages=[10])
        db.flush()

        result = retrieve_from_graph(db, [doc.id], "What does Zeta Unique Protocol provide?")
        matched = result.get("matched_entities", [])
        candidates = result.get("candidate_chunks", [])

        assert "Zeta Unique Protocol" in matched, (
            "Target entity beyond 500 count should be matched in the graph"
        )
        assert len(candidates) >= 1
        assert any(c["page"] == 10 and "Zeta Unique Protocol" in c["text"] for c in candidates)
