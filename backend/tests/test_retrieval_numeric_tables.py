"""Tests for retrieval indexing of numeric data, tables, and junk detection.

Validates that structured tables and high-density numeric prose are preserved
during indexing, while actual TOC dot leaders and book index pages are dropped.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from app.services import retrieval


class TestIsJunkNumericAndTables:
    """Unit tests for _is_junk with tables, numeric prose, and TOC/index noise."""

    def test_markdown_financial_table_header(self) -> None:
        """Markdown financial table header must not be classified as junk."""
        table_header = "| Year | Revenue | Profit |"
        assert not retrieval._is_junk(table_header)

    def test_markdown_financial_table_full(self) -> None:
        """Full markdown financial table with numeric values must be preserved."""
        table = (
            "| Year | Revenue | Profit |\n"
            "|---|---|---|\n"
            "| 2021 | $120M | $24M |\n"
            "| 2022 | $150M | $33M |\n"
            "| 2023 | $185M | $44M |\n"
            "| 2024 | $230M | $60M |"
        )
        assert not retrieval._is_junk(table)

    def test_ml_benchmark_table_header(self) -> None:
        """ML benchmark table header must not be classified as junk."""
        table_header = "| Model | Parameters | WER | FLOPs |"
        assert not retrieval._is_junk(table_header)

    def test_ml_benchmark_table_full(self) -> None:
        """Full ML benchmark table with dense metrics must be preserved."""
        table = (
            "| Model | Parameters | WER | FLOPs |\n"
            "|---|---|---|---|\n"
            "| Conformer-CTC | 120M | 4.2% | 1.8e11 |\n"
            "| Whisper-Small | 244M | 5.6% | 3.4e10 |\n"
            "| Emformer | 80M | 6.1% | 9.2e9 |"
        )
        assert not retrieval._is_junk(table)

    def test_financial_prose_high_numeric_density(self) -> None:
        """Financial prose with high numeric density must be preserved."""
        prose = (
            "$12.5M in Q3 2024, up 18.2% from $10.6M in Q3 2023, representing "
            "an operating profit of $3.2M (25.6% margin) compared to $2.1M "
            "(19.8% margin). Free cash flow reached $4.8M with CapEx at $1.1M."
        )
        assert not retrieval._is_junk(prose)

    def test_financial_prose_short_earnings(self) -> None:
        """Short financial snippet with ellipsis must be preserved."""
        prose = "$12.5M in Q3 2024, up 18.2% from $10.6M in Q3 2023..."
        assert not retrieval._is_junk(prose)

    def test_real_toc_dot_leaders(self) -> None:
        """TOC entries with dot leaders must be rejected as junk."""
        toc_line = "Chapter 1 ............... 12"
        assert retrieval._is_junk(toc_line)

    def test_multi_line_toc_dot_leaders(self) -> None:
        """Multi-line TOC entries with dot leaders must be rejected as junk."""
        toc_block = (
            "Introduction ..................... 1\n"
            "Background ....................... 15\n"
            "Methodology ...................... 42"
        )
        assert retrieval._is_junk(toc_block)

    def test_real_book_index_single_line(self) -> None:
        """Book index comma-number sequence must be rejected as junk."""
        index_line = "algorithms, 12, 14, 18, 22, 29, 31"
        assert retrieval._is_junk(index_line)

    def test_real_book_index_multi_line(self) -> None:
        """Multi-line book index page must be rejected as junk."""
        index_page = (
            "algorithms, 12, 14, 18, 22, 29, 31\n"
            "binary search trees, 45, 48, 52-55, 60\n"
            "hash tables, 88, 92, 105"
        )
        assert retrieval._is_junk(index_page)

    def test_running_headers(self) -> None:
        """Running headers for contents and index must be rejected as junk."""
        assert retrieval._is_junk("Table of Contents")
        assert retrieval._is_junk("Contents 5")
        assert retrieval._is_junk("Index 42")
        assert retrieval._is_junk("# Table of Contents")

    def test_metric_rows_preserved(self) -> None:
        """Metric rows with colons and comma-separated numbers must be preserved."""
        metric_row = "Revenue: 100, 200, 300"
        assert not retrieval._is_junk(metric_row)

    def test_non_ascii_numeric_prose_preserved(self) -> None:
        """Multilingual non-ASCII prose with numbers must be preserved."""
        chinese_prose = "第3季度营收 120 亿元，同比增长 15%"
        assert not retrieval._is_junk(chinese_prose)

    def test_real_book_index_single_page_numbers(self) -> None:
        """Multi-line book index with single page numbers must be rejected as junk."""
        index_page = (
            "Activation function, 45\n"
            "Backpropagation, 101\n"
            "Batch normalization, 120-125\n"
            "Bias-variance tradeoff, 33\n"
            "Convolution, 150"
        )
        assert retrieval._is_junk(index_page)

    def test_toc_pipe_with_dot_leaders_rejected(self) -> None:
        """TOC containing pipe symbols and dot leaders must be rejected as junk."""
        toc_pipe = (
            "# Table of Contents\n"
            "| Section | Page |\n"
            "Introduction .................... 1\n"
            "Chapter 1 ....................... 10"
        )
        assert retrieval._is_junk(toc_pipe)

    def test_single_pipe_not_table(self) -> None:
        """A single pipe character is not a structured table."""
        assert not retrieval._is_table("|")
        assert not retrieval._is_table("| |")

    def test_digit_noise_without_prose(self) -> None:
        """Pure number sequences without prose must be rejected as junk."""
        noise = "123 456 789 012 345 678 901 234"
        assert retrieval._is_junk(noise)


class TestIndexDocumentNumericAndTables:
    """Integration tests for index_document() with SQLite session."""

    @pytest.fixture
    def sqlite_db(self, tmp_path):
        """Create a temporary SQLite DB with chunk and extracted_text tables."""
        engine = create_engine(f"sqlite:///{tmp_path}/test_numeric.db")
        with engine.connect() as conn:
            conn.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS chunk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    emb BLOB,
                    UNIQUE(document_id, page_number, chunk_index)
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
        session_factory = sessionmaker(bind=engine)
        db = session_factory()
        yield db
        db.close()
        engine.dispose()

    def test_index_financial_table_preserved(self, sqlite_db: Session) -> None:
        """Financial tables in extracted_text must produce chunks in the chunk table."""
        doc_id = 101
        content = (
            "| Year | Revenue | Profit |\n"
            "|---|---|---|\n"
            "| 2021 | $120M | $24M |\n"
            "| 2022 | $150M | $33M |\n"
            "| 2023 | $185M | $44M |\n"
            "| 2024 | $230M | $60M |"
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text(
                "SELECT document_id, page_number, chunk_index, text "
                "FROM chunk WHERE document_id = :d"
            ),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == doc_id
        assert rows[0][1] == 1
        assert rows[0][2] == 0
        assert "Revenue" in rows[0][3]
        assert "$230M" in rows[0][3]

    def test_index_ml_benchmark_table_preserved(self, sqlite_db: Session) -> None:
        """ML benchmark tables in extracted_text must produce chunks."""
        doc_id = 102
        content = (
            "| Model | Parameters | WER | FLOPs |\n"
            "|---|---|---|---|\n"
            "| Conformer-CTC | 120M | 4.2% | 1.8e11 |\n"
            "| Whisper-Small | 244M | 5.6% | 3.4e10 |"
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text(
                "SELECT document_id, page_number, chunk_index, text "
                "FROM chunk WHERE document_id = :d"
            ),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == doc_id
        assert rows[0][1] == 1
        assert rows[0][2] == 0
        assert "Conformer-CTC" in rows[0][3]
        assert "FLOPs" in rows[0][3]

    def test_index_financial_prose_preserved(self, sqlite_db: Session) -> None:
        """High-density numeric prose must be indexed into the chunk table."""
        doc_id = 103
        content = (
            "$12.5M in Q3 2024, up 18.2% from $10.6M in Q3 2023, representing "
            "an operating profit of $3.2M (25.6% margin) compared to $2.1M "
            "(19.8% margin). Free cash flow reached $4.8M with CapEx at $1.1M."
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text(
                "SELECT document_id, page_number, chunk_index, text "
                "FROM chunk WHERE document_id = :d"
            ),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == doc_id
        assert rows[0][1] == 1
        assert rows[0][2] == 0
        assert "$12.5M in Q3 2024" in rows[0][3]
        assert "18.2%" in rows[0][3]

    def test_index_drops_toc_dot_leaders(self, sqlite_db: Session) -> None:
        """TOC pages with dot leaders must be dropped from indexing."""
        doc_id = 104
        content = (
            "Chapter 1 ............... 12\n\n"
            "Chapter 2 ............... 34"
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text("SELECT id FROM chunk WHERE document_id = :d"),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 0

    def test_index_drops_book_index(self, sqlite_db: Session) -> None:
        """Book index pages with comma-number sequences must be dropped."""
        doc_id = 105
        content = "algorithms, 12, 14, 18, 22, 29, 31"
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text("SELECT id FROM chunk WHERE document_id = :d"),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 0

    def test_index_populates_full_chunk_metadata(self, sqlite_db: Session) -> None:
        """index_document() must properly set document_id, page_number, chunk_index, and text."""
        doc_id = 106
        page_1_content = (
            "First page introductory discussion about system architecture.\n\n"
            "| Model | Parameters | WER | FLOPs |\n"
            "| Conformer-CTC | 120M | 4.2% | 1.8e11 |"
        )
        page_2_content = (
            "$12.5M in Q3 2024, up 18.2% from $10.6M in Q3 2023, representing "
            "an operating profit of $3.2M."
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, 1, :c1), (:d, 2, :c2)"
            ),
            {"d": doc_id, "c1": page_1_content, "c2": page_2_content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text(
                "SELECT document_id, page_number, chunk_index, text "
                "FROM chunk WHERE document_id = :d ORDER BY page_number, chunk_index"
            ),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) == 2
        # Page 1 check
        assert rows[0][0] == doc_id
        assert rows[0][1] == 1
        assert rows[0][2] == 0
        assert "FLOPs" in rows[0][3]

        # Page 2 check
        assert rows[1][0] == doc_id
        assert rows[1][1] == 2
        assert rows[1][2] == 0
        assert "$12.5M in Q3 2024" in rows[1][3]

    def test_index_non_ascii_and_metric_rows_preserved(self, sqlite_db: Session) -> None:
        """Multilingual numeric prose and metric rows must produce search chunks."""
        doc_id = 107
        content = (
            "第3季度营收 120 亿元，同比增长 15%\n\n"
            "Revenue: 100, 200, 300\n"
            "Operating Margin: 25%, 28%, 31%"
        )
        sqlite_db.execute(
            sql_text(
                "INSERT INTO extracted_text (document_id, page_number, content) "
                "VALUES (:d, :p, :c)"
            ),
            {"d": doc_id, "p": 1, "c": content},
        )
        sqlite_db.commit()

        retrieval.index_document(sqlite_db, doc_id)

        rows = sqlite_db.execute(
            sql_text(
                "SELECT document_id, page_number, chunk_index, text "
                "FROM chunk WHERE document_id = :d"
            ),
            {"d": doc_id},
        ).fetchall()

        assert len(rows) >= 1
        assert "第3季度营收" in rows[0][3]
