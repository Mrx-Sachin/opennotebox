"""Chat engine with RAG-powered document Q&A.

Pipeline: rewrite the question into standalone search phrases (one small LLM
call, also resolves follow-up references) -> BM25 retrieval over paragraph
chunks (services/retrieval.py) -> answer against a numbered source list with
mandatory inline [n] citations -> keep only the citations actually used.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import openai
from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.database import SessionLocal
from app.models import Document, LibraryEntry, Summary
from app.services import retrieval
from app.services.citations import (
    build_passage_sources,
    format_source_line,
    used_citations,
)
from app.services.json_extract import extract_json
from app.services.metrics import get_metrics
from app.services.llm_semaphore import (
    LLMAdmissionTimeout,
    LLMAdmissionUnavailable,
    llm_semaphore,
)

# Total character budget for the numbered source list (~4k tokens), keeping
# the full prompt well under 8k tokens for single-GPU latency.
SOURCE_CHAR_BUDGET = 14_000
TOP_K = 8
CANDIDATES = 24  # BM25 candidates handed to the LLM reranker
SEMANTIC_K = 12  # embedding candidates merged into the reranker pool
HISTORY_TURNS = 3  # user+assistant pairs included in the answer prompt

# Retry budgets when a backend rejects enable_thinking. Rerank returns a
# short index list; rewrite returns a small JSON object; neither needs 2048.
STRUCTURED_RETRY_MAX_TOKENS = 512

_REFERENCE_WORDS = frozenset(
    "it its this that these those they them their he him she her we us our".split()
)

# Connect fast so an unreachable Ollama fails in seconds, not minutes; read
# stays generous since single-GPU generation is slow.
OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=240.0, write=10.0, pool=5.0)


def _text_from_chunk(chunk: Any) -> str:
    """Extract displayable text from a LangChain chat message chunk."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def library_document_titles(db, doc_ids: List[int], user_id: Optional[int]) -> Dict[int, str]:
    """Resolve only titles visible in the requester's private library."""
    if not doc_ids or user_id is None:
        return {doc_id: f"Document {doc_id}" for doc_id in doc_ids}

    rows = (
        db.query(
            Document.id,
            LibraryEntry.title,
            Document.shared_title,
            Document.is_shared,
        )
        .join(LibraryEntry, LibraryEntry.document_id == Document.id)
        .filter(Document.id.in_(doc_ids), LibraryEntry.user_id == user_id)
        .all()
    )
    titles = {}
    for doc_id, private_title, shared_title, is_shared in rows:
        titles[doc_id] = (
            private_title
            or (shared_title if is_shared else None)
            or f"Document {doc_id}"
        )
    return titles


def _unreachable_message(user_id: Optional[int] = None) -> str:
    """Error text for a connect/timeout failure, naming the backend.

    A user with several configured backends cannot act on a bare "AI
    backend is unreachable" - they need to know which one. Best-effort:
    any lookup failure degrades to the generic message rather than
    turning a 503 into a 500.
    """
    try:
        from app.services.llm_settings import get_active_llm

        name = get_active_llm(user_id).get("name") or "config-default"
    except Exception:
        return "AI backend is unreachable"
    if name == "config-default":
        return "AI backend is unreachable"
    return f"AI backend '{name}' is unreachable"


class OllamaUnreachableError(RuntimeError):
    """Raised when the active LLM backend cannot be reached at all
    (connect/timeout failure). The message names the failing backend so
    the user knows which one to fix, not just "an AI backend"."""


class ChatEngine:
    """RAG chat engine scoped to documents with verifiable source citations."""

    @staticmethod
    def should_skip_rewrite(
        question: str, history: Optional[List[Dict[str, str]]]
    ) -> bool:
        """Skip the latency-only rewrite when no prior context needs resolving."""
        if history:
            return False
        words = re.findall(r"[A-Za-z0-9']+", question.lower())
        return len(words) >= 3 and not _REFERENCE_WORDS.intersection(words)

    def _make_model(self, user_id: Optional[int] = None, **kwargs) -> ChatOpenAI:
        """Build a client for user_id's currently active LLM backend (see
        services/llm_settings.py). Built fresh per call, not cached on self,
        so switching the active backend in Settings takes effect within the
        ~15s llm_settings cache window without restarting the process.

        This module is a singleton (`chat_engine` below), so caching the
        client here would pin chat to whatever backend was active at import
        time - which is how chat kept dialing a dead host while summaries,
        flashcards, and podcasts had already moved to the active one.

        user_id must be threaded in from the caller (ultimately the chat
        router's authenticated user). Omitting it - as every call site here
        did until this was traced down - does not fall back to "the
        deployment default backend", it falls back to _seed_user_prefs()'s
        hardcoded default, silently ignoring whatever the user actually
        picked in Settings. That was live in production: a user configured
        a vLLM backend, selected it in Settings, and every chat request kept
        going to the Ollama endpoint anyway with no error, no log entry
        pointing at the cause and no GPU utilization on the box they'd
        expected to see doing the work.
        """
        from app.services.llm_settings import get_active_llm

        active = get_active_llm(user_id)
        return ChatOpenAI(
            base_url=active["base_url"],
            model=active["model"],
            api_key=active["api_key"] or "ollama",
            timeout=OLLAMA_TIMEOUT,
            max_retries=1,
            **kwargs,
        )

    def model(self, user_id: Optional[int] = None) -> ChatOpenAI:
        """The answer model: user's active backend at temperature 0.3."""
        return self._make_model(user_id, temperature=0.3)

    def _rewrite_model(self, user_id: Optional[int] = None) -> ChatOpenAI:
        """Model for the two structured-output helpers (query rewrite and
        rerank), both of which must return parseable JSON.

        Thinking is disabled. Reasoning models (Qwen3.6) otherwise spend the
        entire token budget on chain-of-thought and return EMPTY content -
        measured 1536 tokens / 30s producing nothing, versus 97 tokens / 2.2s
        with thinking off. Raising max_tokens does not fix it; the reasoning
        expands to fill whatever budget it is given. Empty content here is
        silent: both callers fall back to raw-BM25 retrieval, so the symptom
        is quietly worse answers rather than an error.
        """
        return self._make_model(
            user_id,
            temperature=0.0,
            max_tokens=768,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    async def _structured_invoke(self, prompt: str, user_id: Optional[int] = None) -> str:
        """Invoke the rewrite/rerank model, returning raw content.

        ``chat_template_kwargs`` is understood by vLLM but not by every
        OpenAI-compatible server, so a backend that rejects it gets one
        retry without it rather than silently degrading retrieval.
        """
        try:
            async with llm_semaphore():
                response = await self._rewrite_model(user_id).ainvoke(
                    [HumanMessage(content=prompt)]
                )
        except openai.BadRequestError:
            logger.warning(
                "Backend rejected enable_thinking; retrying without it. "
                "Expect slower structured calls on a reasoning model."
            )
            plain = self._make_model(
                user_id, temperature=0.0, max_tokens=STRUCTURED_RETRY_MAX_TOKENS
            )
            async with llm_semaphore():
                response = await plain.ainvoke([HumanMessage(content=prompt)])
        return response.content

    async def answer(
        self,
        question: str,
        doc_ids: List[int],
        page_scope: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[int] = None,
        rag_mode: Optional[str] = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Answer a question scoped to the given documents.

        history: prior turns as [{role, content}], oldest first.
        user_id: whose active LLM backend to use (see _make_model) - pass
        the authenticated caller's id, not the default.
        rag_mode: "standard" (BM25+embeddings), "hybrid" (vector+KG), "graph" (KG only).
        Returns {content, citations: [{n, doc_id, doc_title, page, excerpt}], token_count}.

        DB sessions are opened only for short reads (titles, source assembly).
        They are never held open across LLM awaits or embedding HTTP calls.
        """
        m = get_metrics()
        m.rag_requests.increment()

        # Dispatch to mode-specific handlers. A hybrid/graph failure is
        # surfaced to the caller as an error rather than silently degrading
        # to standard RAG - the user asked for a specific mode and deserves
        # to know it failed (and the KG may simply not be built yet).
        if rag_mode in ("hybrid", "graph"):
            try:
                if on_token is not None:
                    return await self._answer_hybrid_stream(
                        question=question,
                        doc_ids=doc_ids,
                        history=history,
                        user_id=user_id,
                        mode=rag_mode,
                        on_token=on_token,
                    )
                from app.services.hybrid_rag import answer_hybrid

                return await answer_hybrid(
                    question=question,
                    doc_ids=doc_ids,
                    history=history,
                    user_id=user_id,
                    mode=rag_mode,
                )
            except OllamaUnreachableError:
                raise
            except (LLMAdmissionTimeout, LLMAdmissionUnavailable):
                raise
            except Exception as e:
                logger.error(f"HybridRAG ({rag_mode}) failed: {e}")
                raise RuntimeError(
                    f"Knowledge-graph answer failed: {e}. "
                    "The knowledge graph may not be built yet - build it in "
                    "the KG tab, or retry with standard mode."
                ) from e

        # Standard RAG mode (BM25 + embeddings)
        t0 = time.perf_counter()
        stage_ms: Dict[str, int] = {}

        def _mark(name: str, started: float) -> None:
            stage_ms[name] = int((time.perf_counter() - started) * 1000)

        try:
            t = time.perf_counter()
            db = SessionLocal()
            try:
                title_map = library_document_titles(db, doc_ids, user_id)
                doc_titles = [title_map[doc_id] for doc_id in doc_ids if doc_id in title_map]
            finally:
                db.close()
            _mark("titles", t)

            t = time.perf_counter()
            if self.should_skip_rewrite(question, history):
                # Raw-query retrieval already covers BM25 and semantic search;
                # retain rewriting for short or context-dependent questions.
                phrases, rephrasings = [], []
            else:
                phrases, rephrasings = await self.rewrite_query(
                    question, history, doc_titles, user_id
                )
            _mark("rewrite", t)

            # First chat on a document builds its chunk + embedding index
            # (tens of seconds); keep it off the event loop.
            t = time.perf_counter()
            candidates = await asyncio.to_thread(
                self._retrieve, doc_ids, phrases, rephrasings, question
            )
            _mark("retrieve", t)

            t = time.perf_counter()
            hits = await self._rerank(question, candidates, user_id)
            # Semantic per-query best hits always reach the answer model; the
            # reranker is literal-minded and can veto exactly the passage a
            # paraphrase question needs.
            picked = {h["chunk_id"] for h in hits}
            hits = [
                h for h in candidates
                if h.get("sem_top") and h["chunk_id"] not in picked
            ] + hits
            _mark("rerank", t)

            logger.info(
                f"chat retrieval: keywords={phrases} rephrasings={rephrasings} "
                f"source_pages={[h['page'] for h in hits]}"
            )

            # Prioritize the page the user is currently reading if scoped.
            if page_scope and page_scope.get("page"):
                hits = self._boost_page(hits, page_scope["page"])

            t = time.perf_counter()
            db = SessionLocal()
            try:
                sources = self._build_sources(db, doc_ids, hits, user_id)
            finally:
                db.close()
            _mark("sources", t)

            if not sources:
                logger.info(
                    "chat.stage total={}ms stages={} empty_sources=1",
                    int((time.perf_counter() - t0) * 1000),
                    stage_ms,
                )
                return {
                    "content": (
                        "I could not find anything in the selected document(s) "
                        "related to that question."
                    ),
                    "citations": [],
                    "token_count": 0,
                }

            prompt = self._build_prompt(question, sources, history)
            t = time.perf_counter()
            async with llm_semaphore():
                answer_messages = [
                    SystemMessage(content=self._system_prompt()),
                    HumanMessage(content=prompt),
                ]
                if on_token is None:
                    response = await self.model(user_id).ainvoke(answer_messages)
                    content = response.content
                else:
                    content = await self._stream_model_content(
                        self.model(user_id), answer_messages, on_token
                    )
            _mark("generate", t)

            citations = self._used_citations(content, sources)
            logger.info(
                "chat.stage total={}ms stages={} sources={} citations={}",
                int((time.perf_counter() - t0) * 1000),
                stage_ms,
                len(sources),
                len(citations),
            )
            # Record RAG metrics
            m.rag_latency.observe((time.perf_counter() - t0) * 1000)
            m.rag_chunk_count.observe(len(sources))
            return {
                "content": content,
                "citations": citations,
                "token_count": len(content.split()),
            }
        except OllamaUnreachableError:
            logger.error("Chat engine failed: Ollama unreachable")
            raise
        except openai.AuthenticationError as e:
            # A 401 from an OpenAI-compatible endpoint. For the CLI bridges
            # this almost always means the stored api_key is not the bridge's
            # current shared secret, which Settings -> AI Model repairs by
            # reconnecting - say so instead of "Chat failed: <raw sdk error>".
            logger.error(f"Chat engine failed: backend rejected the api key: {e}")
            raise RuntimeError(
                "The AI backend rejected its API key. Open Settings -> AI Model "
                "and click Connect on this backend to refresh it."
            ) from e
        except openai.RateLimitError as e:
            logger.warning(f"Chat engine rate limited: {e}")
            raise RuntimeError(
                "The AI backend is rate-limited. Try again in a few seconds."
            ) from e
        except openai.InternalServerError as e:
            logger.error(f"Chat engine failed: backend internal error: {e}")
            raise RuntimeError(
                "The AI backend encountered an internal error. "
                "The GPU may be overloaded - try again in a moment."
            ) from e
        except (LLMAdmissionTimeout, LLMAdmissionUnavailable):
            raise
        except (openai.APIConnectionError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"Chat engine failed: AI backend unreachable: {e}")
            raise OllamaUnreachableError(_unreachable_message(user_id)) from e
        except Exception as e:
            logger.error(f"Chat engine failed: {e}")
            raise RuntimeError(f"Chat failed: {e}")

    async def _answer_hybrid_stream(
        self,
        question: str,
        doc_ids: List[int],
        history: Optional[List[Dict[str, str]]],
        user_id: Optional[int],
        mode: str,
        on_token: Callable[[str], Awaitable[None]],
    ) -> Dict[str, Any]:
        """Run hybrid/graph retrieval and stream the answer model's tokens.

        Retrieval, prompt construction, and citation filtering use the same
        helpers as the non-streaming HybridRAG route. Keeping this small
        streaming adapter here avoids changing retrieval behavior for callers
        that still use ``answer_hybrid`` directly.
        """
        from app.services.hybrid_rag import (
            _kg_built_for_docs,
            _no_sources_message,
            _system_prompt,
            _used_citations,
            build_hybrid_prompt,
            retrieve_hybrid,
        )

        started = time.perf_counter()
        use_vector = mode == "hybrid"
        use_graph = mode in ("hybrid", "graph")
        db = SessionLocal()
        try:
            title_map = library_document_titles(db, doc_ids, user_id)
            doc_titles = [title_map[doc_id] for doc_id in doc_ids if doc_id in title_map]
            phrases: List[str] = []
            rephrasings: List[str] = []
            if use_vector and not self.should_skip_rewrite(question, history):
                phrases, rephrasings = await self.rewrite_query(
                    question, history, doc_titles, user_id
                )
            result = await asyncio.to_thread(
                retrieve_hybrid,
                db,
                doc_ids,
                question,
                phrases,
                rephrasings,
                use_vector=use_vector,
                use_graph=use_graph,
                user_id=user_id,
            )
            sources = result["combined_sources"]
            kg_built = _kg_built_for_docs(db, doc_ids)
        finally:
            db.close()

        if not sources:
            return {
                "content": _no_sources_message(mode, kg_built),
                "citations": [],
                "token_count": 0,
            }

        prompt = build_hybrid_prompt(question, sources, history)
        messages = [
            SystemMessage(content=_system_prompt()),
            HumanMessage(content=prompt),
        ]
        async with llm_semaphore():
            content = await self._stream_model_content(
                self.model(user_id), messages, on_token
            )
        citations = _used_citations(content, sources)
        metrics = get_metrics()
        metrics.rag_latency.observe((time.perf_counter() - started) * 1000)
        metrics.rag_chunk_count.observe(len(sources))
        logger.info(
            "hybrid_rag mode={} total={}ms sources={} citations={}",
            mode,
            int((time.perf_counter() - started) * 1000),
            len(sources),
            len(citations),
        )
        return {
            "content": content,
            "citations": citations,
            "token_count": len(content.split()),
        }

    @staticmethod
    async def _stream_model_content(
        model: ChatOpenAI,
        messages: List[Any],
        on_token: Callable[[str], Awaitable[None]],
    ) -> str:
        """Forward generated text chunks and return the assembled content."""
        pieces: List[str] = []
        async for chunk in model.astream(messages):
            text = _text_from_chunk(chunk)
            if text:
                pieces.append(text)
                await on_token(text)
        return "".join(pieces)

    # ------------------------------------------------------------------
    # Query rewriting

    async def rewrite_query(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]],
        doc_titles: Optional[List[str]] = None,
        user_id: Optional[int] = None,
    ) -> tuple[List[str], List[str]]:
        """Resolve follow-up references and produce two kinds of queries:

        keywords - short phrases in the text's own vocabulary, for BM25;
        rephrasings - standalone rewordings of the question, for embeddings.
        Falls back to the raw question alone on any failure.
        """
        context = ""
        if doc_titles:
            context += f"The user is asking about: {'; '.join(doc_titles[:3])}\n\n"
        if history:
            recent = history[-4:]
            turns = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in recent)
            context += f"Conversation so far:\n{turns}\n\n"

        prompt = (
            f"{context}The user now asks: \"{question}\"\n\n"
            "Produce search queries to find the passages that answer this "
            "question, resolving any pronouns or references using the "
            "conversation. Respond with ONLY a JSON object of this shape:\n"
            '{"keywords": [3-4 short phrases (2-5 words) using concrete words '
            "the author would have written in the text when discussing this "
            'topic], "rephrasings": [2-3 alternative standalone phrasings of '
            "the question itself, each using different vocabulary for the "
            'same intent], "passage": "a 2-3 sentence hypothetical excerpt, '
            "written in the source text's own voice, that would answer the "
            'question if it existed in the text"}\n'
            "Example for \"Which claims in this programming book aged badly?\": "
            '{"keywords": ["code generation", "programmers will not be needed", '
            '"future of programming"], "rephrasings": ["predictions about '
            'programming that turned out wrong", "outdated advice about how '
            'software would be built"], "passage": "Some have suggested that '
            "we are close to the end of code, that soon all code will be "
            "generated instead of written and programmers simply won't be "
            'needed."}'
        )
        try:
            content = await self._structured_invoke(prompt, user_id)
            parsed = extract_json(content)
            if isinstance(parsed, dict):
                keywords = [
                    str(p) for p in parsed.get("keywords") or [] if str(p).strip()
                ][:4]
                rephrasings = [
                    str(p) for p in parsed.get("rephrasings") or [] if str(p).strip()
                ][:3]
                # HyDE: a hypothetical excerpt embeds far closer to the real
                # passage than any phrasing of the question does.
                passage = str(parsed.get("passage") or "").strip()
                if passage:
                    rephrasings.append(passage[:600])
                if keywords or rephrasings:
                    return keywords, rephrasings
        except openai.AuthenticationError as e:
            # A 401 from an OpenAI-compatible endpoint. For the CLI bridges
            # this almost always means the stored api_key is not the bridge's
            # current shared secret, which Settings -> AI Model repairs by
            # reconnecting - say so instead of "Chat failed: <raw sdk error>".
            logger.error(f"Chat engine failed: backend rejected the api key: {e}")
            raise RuntimeError(
                "The AI backend rejected its API key. Open Settings -> AI Model "
                "and click Connect on this backend to refresh it."
            ) from e
        except (openai.APIConnectionError, httpx.ConnectError, httpx.TimeoutException) as e:
            # Ollama itself is unreachable - don't limp through retrieval and
            # rerank only to time out again on the final answer call.
            raise OllamaUnreachableError(_unreachable_message(user_id)) from e
        except (LLMAdmissionTimeout, LLMAdmissionUnavailable):
            raise
        except Exception as e:
            logger.warning(f"Query rewrite failed, using raw question: {e}")
        return [], []

    # ------------------------------------------------------------------
    # Retrieval (hybrid: BM25 for keyword questions, embeddings for
    # paraphrase questions whose wording never appears in the text)

    @staticmethod
    def _retrieve(
        doc_ids: List[int],
        phrases: List[str],
        rephrasings: List[str],
        question: str,
    ) -> List[Dict]:
        db = SessionLocal()
        try:
            for doc_id in doc_ids:
                retrieval.index_document(db, doc_id)
            # BM25 first - no embedding wait. Then embed only the lexical
            # window so hybrid search can score those chunks immediately.
            # Full-document embedding is queued as a background Celery job.
            lexical = retrieval.search(
                db, doc_ids, phrases + [question], top_k=CANDIDATES
            )
            hit_ids = [h["chunk_id"] for h in lexical if h.get("chunk_id") is not None]
            if hit_ids:
                for doc_id in doc_ids:
                    retrieval.ensure_embeddings(db, doc_id, only_chunk_ids=hit_ids)
            else:
                # No lexical hits (paraphrase-only questions): embed a small
                # prefix so semantic search has something to score without
                # blocking on the whole book.
                for doc_id in doc_ids:
                    retrieval.ensure_embeddings(
                        db,
                        doc_id,
                        only_chunk_ids=retrieval.list_unembedded_chunk_ids(
                            db, doc_id, limit=64
                        ),
                    )
            retrieval.schedule_full_embeddings(doc_ids)
            # Embeddings get question rephrasings, NOT the keyword phrases:
            # topic-guess keywords drag semantic search toward plausible
            # answer topics and drown out the actual question.
            semantic = retrieval.semantic_search(
                db, doc_ids, [question] + rephrasings, top_k=SEMANTIC_K
            )
            by_id = {h["chunk_id"]: h for h in lexical}
            merged = list(lexical)
            for h in semantic:
                if h["chunk_id"] in by_id:
                    if h.get("sem_top"):
                        by_id[h["chunk_id"]]["sem_top"] = True
                else:
                    merged.append(h)
            return merged
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Reranking

    async def _rerank(
        self, question: str, hits: List[Dict], user_id: Optional[int] = None
    ) -> List[Dict]:
        """Have the LLM pick the most relevant candidates.

        BM25 alone fails on paraphrase questions when the query terms are
        ubiquitous in the book (e.g. "code" in a programming book), so a small
        LLM call selects from match-context snippets. Falls back to BM25
        order on any failure.
        """
        if len(hits) <= TOP_K:
            return hits
        listing = "\n".join(
            f"[{i + 1}] (p.{h['page']}) {h.get('snippet') or h['text'][:240]}"
            for i, h in enumerate(hits)
        )
        prompt = (
            f"Question: {question}\n\nSnippets:\n{listing}\n\n"
            f"Which snippets contain information that helps answer the "
            f"question? Include snippets about the question's underlying "
            f"topic even when they use different vocabulary than the "
            f"question. Respond with ONLY a JSON array of up to {TOP_K} "
            f"snippet numbers, most relevant first."
        )
        try:
            content = await self._structured_invoke(prompt, user_id)
            picked = extract_json(content)
            if isinstance(picked, list):
                idxs = [
                    int(n) - 1
                    for n in picked
                    if isinstance(n, int) or (isinstance(n, str) and n.strip().isdigit())
                ]
                chosen = [hits[i] for i in idxs if 0 <= i < len(hits)][:TOP_K]
                if chosen:
                    # Backfill a thin pick with next-best candidates: the
                    # answer model handles extra context well, and only the
                    # sources it actually cites surface in the UI.
                    if len(chosen) < 5:
                        picked = {h["chunk_id"] for h in chosen}
                        chosen += [
                            h for h in hits if h["chunk_id"] not in picked
                        ][: TOP_K - len(chosen)]
                    return chosen
        except (LLMAdmissionTimeout, LLMAdmissionUnavailable):
            raise
        except Exception as e:
            logger.warning(f"Rerank failed, using BM25 order: {e}")
        return hits[:TOP_K]

    # ------------------------------------------------------------------
    # Context assembly

    @staticmethod
    def _boost_page(hits: List[Dict], page: int) -> List[Dict]:
        return sorted(hits, key=lambda h: (h["page"] != page, h["score"]))

    def _build_sources(
        self, db, doc_ids: List[int], hits: List[Dict], user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Number the retrieved chunks into a passage-level source list within budget.

        When retrieval comes back nearly empty (broad questions like "what is
        this book about"), section summaries are used as sources instead.
        """
        titles = library_document_titles(db, doc_ids, user_id)
        section_summaries = []
        if len(hits) < 2 and user_id is not None:
            section_summaries = (
                db.query(Summary)
                .filter(
                    Summary.document_id.in_(doc_ids),
                    Summary.user_id == user_id,
                    Summary.mode == "section",
                    Summary.content.isnot(None),
                    Summary.content != "",
                )
                .limit(4)
                .all()
            )
        return build_passage_sources(
            hits=hits,
            titles=titles,
            max_budget=SOURCE_CHAR_BUDGET,
            fallback_summaries=section_summaries,
        )

    # ------------------------------------------------------------------
    # Prompting

    async def answer_general(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[int] = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Answer a question with no document grounding and no retrieval.

        The document path deliberately refuses to say anything its sources
        don't support, which is correct when the user asked about their
        book and wrong when they just asked a general question. This
        returns no citations because there are no sources to cite.
        """
        t0 = time.perf_counter()
        try:
            messages: List[Any] = [SystemMessage(content=self._general_system_prompt())]
            for turn in (history or [])[-(HISTORY_TURNS * 2):]:
                content = turn.get("content") or ""
                if not content:
                    continue
                messages.append(
                    AIMessage(content=content)
                    if turn.get("role") == "assistant"
                    else HumanMessage(content=content)
                )
            messages.append(HumanMessage(content=question))

            async with llm_semaphore():
                if on_token is None:
                    response = await self.model(user_id).ainvoke(messages)
                    content = response.content
                else:
                    content = await self._stream_model_content(
                        self.model(user_id), messages, on_token
                    )
            total_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "chat.stage mode=general total={}ms stages={{'generate': {}}}",
                total_ms,
                total_ms,
            )
            return {
                "content": content,
                "citations": [],
                "token_count": len(content.split()),
            }
        except OllamaUnreachableError:
            raise
        except openai.AuthenticationError as e:
            # A 401 from an OpenAI-compatible endpoint. For the CLI bridges
            # this almost always means the stored api_key is not the bridge's
            # current shared secret, which Settings -> AI Model repairs by
            # reconnecting - say so instead of "Chat failed: <raw sdk error>".
            logger.error(f"Chat engine failed: backend rejected the api key: {e}")
            raise RuntimeError(
                "The AI backend rejected its API key. Open Settings -> AI Model "
                "and click Connect on this backend to refresh it."
            ) from e
        except openai.RateLimitError as e:
            logger.warning(f"General chat rate limited: {e}")
            raise RuntimeError(
                "The AI backend is rate-limited. Try again in a few seconds."
            ) from e
        except openai.InternalServerError as e:
            logger.error(f"General chat failed: backend internal error: {e}")
            raise RuntimeError(
                "The AI backend encountered an internal error. "
                "The GPU may be overloaded - try again in a moment."
            ) from e
        except (LLMAdmissionTimeout, LLMAdmissionUnavailable):
            raise
        except (openai.APIConnectionError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"General chat failed: AI backend unreachable: {e}")
            raise OllamaUnreachableError(_unreachable_message(user_id)) from e
        except Exception as e:
            logger.error(f"General chat failed: {e}")
            raise RuntimeError(f"Chat failed: {e}")

    @staticmethod
    def _general_system_prompt() -> str:
        return (
            "You are a helpful, knowledgeable assistant. Answer the user's "
            "question directly and concisely from your own knowledge. No "
            "documents have been provided for this question, so do not cite "
            "sources, do not invent citation markers like [1], and do not "
            "claim to be quoting the user's documents. If the answer depends "
            "on something only their documents would say, tell them to attach "
            "the document and ask again."
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a precise research assistant answering questions about the "
            "user's documents using ONLY the numbered sources provided. Every "
            "factual claim must carry an inline citation like [1] or [2, 3] "
            "referencing the particular numbered passage that directly supports that claim, never another passage from the same page or document. Never cite a number "
            "that is not in the source list, and never make claims the sources "
            "do not support. The sources may use different vocabulary than the "
            "question; connect them when the meaning matches (e.g. a passage "
            "about automatic code generation is relevant to a question about "
            "AI) and make the connection explicit. If the sources truly do not "
            "bear on the question, say so plainly. Be factual and concise."
        )

    def _build_prompt(
        self,
        question: str,
        sources: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]],
    ) -> str:
        source_block = "\n\n".join(
            format_source_line(s) for s in sources
        )
        parts = [f"Sources:\n{source_block}"]
        if history:
            recent = history[-(HISTORY_TURNS * 2):]
            turns = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in recent)
            parts.append(f"Conversation so far:\n{turns}")
        parts.append(
            f"Question: {question}\n\nAnswer with inline [n] citations after every claim."
        )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Citation filtering

    @staticmethod
    def _used_citations(
        content: str, sources: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Keep only sources whose number actually appears as [n] in the answer."""
        return used_citations(content, sources)


chat_engine = ChatEngine()
