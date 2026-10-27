"""Knowledge graph retrieval: match query entities and traverse the KG.

Builds a NetworkX DiGraph from the database, matches query terms to entity
names, does a depth-1 DFS to collect a subgraph, and formats the result as
text context for an LLM prompt.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

import networkx as nx
from sqlalchemy.orm import Session

from app.models.kg import KgEntity, KgRelationship


def build_networkx_graph(db: Session, doc_ids: List[int]) -> nx.DiGraph:
    """Load KG entities and relationships from DB into a NetworkX DiGraph."""
    G = nx.DiGraph()

    entities = db.query(KgEntity).filter(KgEntity.document_id.in_(doc_ids)).all()
    for e in entities:
        G.add_node(e.id, name=e.name, entity_type=e.entity_type, description=e.description, document_id=e.document_id)

    rels = db.query(KgRelationship).filter(KgRelationship.document_id.in_(doc_ids)).all()
    for r in rels:
        if r.subject_id in G and r.object_id in G:
            G.add_edge(r.subject_id, r.object_id, relationship=r.relationship, confidence=r.confidence, page=r.page_number)

    return G


def match_query_entities(question: str, G: nx.DiGraph) -> List[int]:
    """Match words/phrases from the question to entity names in the graph.

    Uses substring matching (case-insensitive) and word-overlap scoring
    to find the most relevant seed nodes for graph traversal.
    """
    question_lower = question.lower()
    question_words = set(re.findall(r"[a-z]{3,}", question_lower))

    if not question_words:
        return []

    candidates: Dict[int, float] = {}
    for node, data in G.nodes(data=True):
        name = (data.get("name") or "").lower()
        name_words = set(re.findall(r"[a-z]{3,}", name))

        # Direct substring match gets highest score.
        if name in question_lower:
            candidates[node] = 10.0
            continue

        # Word overlap score.
        overlap = len(question_words & name_words)
        if overlap > 0:
            score = overlap * 2.0 / max(len(name_words), 1)
            if score >= 0.5:
                candidates[node] = score

    # Return top matches sorted by score.
    sorted_matches = sorted(candidates.items(), key=lambda x: -x[1])
    return [node for node, _ in sorted_matches[:5]]


def dfs_one_hop(G: nx.DiGraph, seed_nodes: List[int]) -> Dict[str, Any]:
    """Depth-first search with depth limit 1 from seed nodes.

    Returns a dict with 'nodes' (list of node dicts) and 'triplets'
    (list of formatted relationship strings).
    """
    visited_nodes: Set[int] = set()
    triplets: List[str] = []

    for seed in seed_nodes:
        if seed not in G:
            continue
        visited_nodes.add(seed)

        # Outgoing edges.
        for neighbor in G.successors(seed):
            if neighbor not in visited_nodes:
                visited_nodes.add(neighbor)
            edge_data = G.edges[seed, neighbor]
            subj_name = G.nodes[seed].get("name", "Unknown")
            obj_name = G.nodes[neighbor].get("name", "Unknown")
            rel = edge_data.get("relationship", "related_to")
            triplets.append(f"{subj_name} {rel} {obj_name}")

        # Incoming edges (reverse direction).
        for predecessor in G.predecessors(seed):
            if predecessor not in visited_nodes:
                visited_nodes.add(predecessor)
            edge_data = G.edges[predecessor, seed]
            subj_name = G.nodes[predecessor].get("name", "Unknown")
            obj_name = G.nodes[seed].get("name", "Unknown")
            rel = edge_data.get("relationship", "related_to")
            triplets.append(f"{subj_name} {rel} {obj_name}")

    nodes = []
    for node_id in visited_nodes:
        data = G.nodes[node_id]
        nodes.append({
            "id": node_id,
            "name": data.get("name", "Unknown"),
            "type": data.get("entity_type", "other"),
        })

    return {"nodes": nodes, "triplets": triplets}


def format_graph_context(subgraph: Dict[str, Any]) -> str:
    """Format a subgraph dict into readable text for an LLM prompt."""
    parts: List[str] = []

    triplets = subgraph.get("triplets", [])
    if triplets:
        parts.append("Knowledge Graph Facts:")
        for i, t in enumerate(triplets, 1):
            parts.append(f"  {i}. {t}")

    nodes = subgraph.get("nodes", [])
    if nodes:
        parts.append("Entities Found:")
        for n in nodes:
            parts.append(f"  - {n['name']} ({n['type']})")

    return "\n".join(parts) if parts else ""


def _hub_seed_nodes(G: nx.DiGraph, limit: int = 5) -> List[int]:
    """The most-connected nodes, as a fallback when nothing matches.

    Generic questions ("What is this document about?") share no words with
    entity names, so without a fallback a rich graph still yields nothing
    and the question dead-ends. The graph's hubs are its main topics,
    which is exactly what such a question is asking for.
    """
    degrees = dict(G.degree())
    return [n for n, _ in sorted(degrees.items(), key=lambda kv: -kv[1])[:limit]]


def retrieve_from_graph(
    db: Session, doc_ids: List[int], question: str
) -> Dict[str, Any]:
    """Full graph retrieval pipeline: build graph, match entities, traverse.

    Returns a dict with matched_entities, triplets, nodes, context string,
    and a fallback flag: when the question matches no entity, the hubs are
    traversed instead so a built graph can still answer generic questions.
    """
    G = build_networkx_graph(db, doc_ids)

    if G.number_of_nodes() == 0:
        return {
            "matched_entities": [],
            "triplets": [],
            "nodes": [],
            "context": "",
            "fallback": False,
        }

    seed_nodes = match_query_entities(question, G)
    fallback = False
    if not seed_nodes:
        seed_nodes = _hub_seed_nodes(G)
        fallback = bool(seed_nodes)

    subgraph = dfs_one_hop(G, seed_nodes)
    context = format_graph_context(subgraph)

    matched_names = [G.nodes[n].get("name", "") for n in seed_nodes if n in G]

    return {
        "matched_entities": matched_names,
        "triplets": subgraph["triplets"],
        "nodes": subgraph["nodes"],
        "context": context,
        "fallback": fallback,
    }
