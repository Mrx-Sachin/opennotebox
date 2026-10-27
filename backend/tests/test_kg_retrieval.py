"""Tests for knowledge graph retrieval.

The key guard: a built graph must be able to answer a generic question
that shares no words with any entity name. Without a fallback, the
entity matcher returns no seeds, the traversal is empty, and Graph mode
dead-ends even on a richly built graph.
"""
from __future__ import annotations

from app.models.kg import KgEntity, KgRelationship
from app.services.kg_retrieval import match_query_entities, retrieve_from_graph


def _entity(db, doc_id, name, entity_type="concept"):
    e = KgEntity(
        document_id=doc_id,
        name=name,
        name_normalized=name.lower(),
        entity_type=entity_type,
    )
    db.add(e)
    db.flush()
    return e


def _rel(db, doc_id, subject, relationship, obj):
    r = KgRelationship(
        document_id=doc_id,
        subject_id=subject.id,
        object_id=obj.id,
        relationship=relationship,
    )
    db.add(r)
    db.flush()
    return r


def test_empty_graph_returns_empty(db, test_document):
    result = retrieve_from_graph(db, [test_document.id], "What is this about?")
    assert result["context"] == ""
    assert result["matched_entities"] == []
    assert result["fallback"] is False


def test_matching_question_uses_matched_nodes(db, test_document):
    t = _entity(db, test_document.id, "Transformer")
    a = _entity(db, test_document.id, "Attention")
    _rel(db, test_document.id, t, "uses", a)

    result = retrieve_from_graph(db, [test_document.id], "What is a transformer?")
    assert result["fallback"] is False
    assert "Transformer" in result["matched_entities"]
    assert "Transformer uses Attention" in result["triplets"]


def test_generic_question_falls_back_to_hubs(db, test_document):
    # "Transformer" is the hub: one outgoing, one incoming edge.
    t = _entity(db, test_document.id, "Transformer")
    a = _entity(db, test_document.id, "Attention")
    c = _entity(db, test_document.id, "Training", "process")
    _rel(db, test_document.id, t, "uses", a)
    _rel(db, test_document.id, c, "uses", t)

    # Shares no words with any entity name, so the matcher finds nothing.
    result = retrieve_from_graph(db, [test_document.id], "What is this document about?")
    assert result["fallback"] is True
    assert result["context"] != ""
    assert "Transformer uses Attention" in result["triplets"]
    assert "Training uses Transformer" in result["triplets"]


def test_hub_seeding_prefers_most_connected(db, test_document):
    from app.services.kg_retrieval import _hub_seed_nodes, build_networkx_graph

    t = _entity(db, test_document.id, "Transformer")
    a = _entity(db, test_document.id, "Attention")
    b = _entity(db, test_document.id, "Residual")
    _rel(db, test_document.id, t, "uses", a)
    _rel(db, test_document.id, t, "uses", b)

    G = build_networkx_graph(db, [test_document.id])
    seeds = _hub_seed_nodes(G)
    # The hub must come first so its neighbors define the context.
    assert seeds[0] == t.id


def test_match_query_entities_substring_and_overlap(db, test_document):
    from app.services.kg_retrieval import build_networkx_graph

    t = _entity(db, test_document.id, "Transformer")
    _entity(db, test_document.id, "Attention")
    G = build_networkx_graph(db, [test_document.id])
    matches = match_query_entities("Explain the transformer architecture", G)
    assert t.id in matches
    # A question with no overlap matches nothing.
    assert match_query_entities("What is this document about?", G) == []
