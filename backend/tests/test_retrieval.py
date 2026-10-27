"""Tests for retrieval module: chunking, BM25 search, junk detection.

Uses a raw SQLAlchemy session against SQLite so no Postgres is required.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

import pytest

from app.services import retrieval


class TestSplitPage:
    """Unit tests for _split_page chunking logic."""

    def test_single_paragraph(self) -> None:
        text = "This is a single short paragraph."
        chunks = retrieval._split_page(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_two_paragraphs_merged(self) -> None:
        text = "First paragraph here.\n\nSecond paragraph here."
        chunks = retrieval._split_page(text)
        assert len(chunks) == 1
        assert "First" in chunks[0]
        assert "Second" in chunks[0]

    def test_long_text_splits(self) -> None:
        """Long content exceeding MAX_CHARS must be split."""
        text = " ".join(["word"] * 600) + ".\n\n" + " ".join(["other"] * 600)
        chunks = retrieval._split_page(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= retrieval.MAX_CHARS

    def test_hard_split_over_max(self) -> None:
        """A single paragraph longer than MAX_CHARS must be split."""
        text = "A very long sentence without breaks. " * 200
        chunks = retrieval._split_page(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= retrieval.MAX_CHARS

    def test_empty_content(self) -> None:
        chunks = retrieval._split_page("")
        assert chunks == []

    def test_whitespace_only(self) -> None:
        chunks = retrieval._split_page("   \n\n   ")
        assert chunks == []


class TestIsJunk:
    """Unit tests for _is_junk filter."""

    def test_toc_dot_leaders(self) -> None:
        assert retrieval._is_junk("Introduction ........... 1")

    def test_index_page(self) -> None:
        assert retrieval._is_junk("Index 42")

    def test_contents_page(self) -> None:
        assert retrieval._is_junk("Contents")

    def test_digit_heavy(self) -> None:
        text = "123 456 789 012 345 678 901 234"
        assert retrieval._is_junk(text)

    def test_normal_prose(self) -> None:
        text = "This is a perfectly normal paragraph of prose."
        assert not retrieval._is_junk(text)

    def test_mixed_content(self) -> None:
        text = "The year was 1999 and everything was fine."
        assert not retrieval._is_junk(text)


class TestContentTerms:
    """Unit tests for _content_terms extraction."""

    def test_basic_extraction(self) -> None:
        terms = retrieval._content_terms("the quick brown fox")
        assert "the" not in terms
        assert "quick" in terms
        assert "brown" in terms
        assert "fox" in terms

    def test_punctuation_stripped(self) -> None:
        terms = retrieval._content_terms("hello, world! yes.")
        assert "hello" in terms
        assert "world" in terms
        assert "yes" in terms

    def test_empty_after_stopwords(self) -> None:
        terms = retrieval._content_terms("the a an is at")
        assert len(terms) == 0

    def test_single_chars_removed(self) -> None:
        terms = retrieval._content_terms("a b c hello")
        assert "a" not in terms
        assert "b" not in terms
        assert "c" not in terms
        assert "hello" in terms

    def test_numeric_terms(self) -> None:
        terms = retrieval._content_terms("version 2.0 release 123")
        assert "version" in terms
        assert "123" in terms


class TestQueryTiers:
    """Unit tests for _query_tiers generation."""

    def test_single_word(self) -> None:
        tiers = retrieval._query_tiers("testing")
        assert len(tiers) >= 1

    def test_multiple_words(self) -> None:
        tiers = retrieval._query_tiers("hello world")
        assert len(tiers) == 3

    def test_empty_after_stopwords(self) -> None:
        tiers = retrieval._query_tiers("the a an")
        assert tiers == []


class TestSearchSQLite:
    """Integration tests for chunking/indexing against SQLite."""

    @pytest.fixture()
    def sqlite_db(self, tmp_path):
        """Create a temporary SQLite DB with chunk and extracted_text tables."""
        engine = create_engine(f"sqlite:///{tmp_path}/chunks.db")
        with engine.connect() as conn:
            conn.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS chunk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    emb BLOB
                )
            """))
            conn.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS extracted_text (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    headings TEXT DEFAULT '[]'
                )
            """))
            conn.commit()
        Session = sessionmaker(bind=engine)
        db = Session()
        yield db
        db.close()
        engine.dispose()

    def test_split_and_index_chunks(self, sqlite_db: Session) -> None:
        """Verify that _split_page produces proper chunks."""
        content = "\n\n".join([
            "First paragraph of the document. It talks about important things.",
            "Second paragraph continues the discussion about those things.",
            "Third paragraph wraps everything up nicely with a conclusion.",
        ])
        chunks = retrieval._split_page(content)
        assert len(chunks) >= 1
        all_text = " ".join(chunks)
        assert "First" in all_text
        assert "Third" in all_text

    def test_junk_chunks_filtered(self, sqlite_db: Session) -> None:
        toc_text = "Chapter 1 ............ 1\n\nChapter 2 ............ 15"
        chunks = retrieval._split_page(toc_text)
        non_junk = [c for c in chunks if not retrieval._is_junk(c)]
        assert len(non_junk) == 0

    def test_insert_chunks_directly(self, sqlite_db: Session) -> None:
        sqlite_db.execute(sql_text(
            "INSERT INTO chunk (document_id, page_number, chunk_index, text) "
            "VALUES (1, 1, 0, 'Hello world test chunk')"
        ))
        sqlite_db.execute(sql_text(
            "INSERT INTO chunk (document_id, page_number, chunk_index, text) "
            "VALUES (1, 1, 1, 'Another test chunk for retrieval')"
        ))
        sqlite_db.commit()

        result = sqlite_db.execute(sql_text(
            "SELECT id, document_id, text FROM chunk WHERE document_id = 1"
        )).fetchall()
        assert len(result) == 2

    def test_pack_unpack_vectors(self) -> None:
        original = [0.1, -0.2, 0.3, 0.0, 1.0, -0.5]
        packed = retrieval._pack(original)
        unpacked = retrieval._unpack(packed)
        assert len(unpacked) == len(original)
        for a, b in zip(original, unpacked):
            assert abs(a - b) < 1e-6

    def test_pack_empty_vector(self) -> None:
        packed = retrieval._pack([])
        assert packed == b""

    def test_semantic_search_returns_list(self, sqlite_db: Session) -> None:
        """semantic_search should return empty list when embeddings fail."""
        result = retrieval.semantic_search(sqlite_db, [1], ["test"])
        assert isinstance(result, list)
