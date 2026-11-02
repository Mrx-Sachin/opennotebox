#!/usr/bin/env python3
"""CLI utility for maintenance repair of legacy Docling PDF extractions.

By default, this tool runs in dry-run mode and performs safety checks without
altering the database or writing backup files.

When --apply is specified, it strictly requires --expected-sha256 and --backup.
An exclusive backup file with mode 0600 is written BEFORE any database changes.
Existing ExtractedText row IDs are preserved via in-place transactional update.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure backend directory is in Python module search path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.database import SessionLocal
from app.services.extraction_repair import repair_legacy_pdf_extraction
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair legacy Docling PDF extractions in-place with modern "
            "word-level bounding boxes and untouched line extraction."
        )
    )
    parser.add_argument(
        "--document-id",
        type=int,
        required=True,
        help="ID of the Document to inspect or repair.",
    )
    parser.add_argument(
        "--expected-sha256",
        type=str,
        default=None,
        help="Expected SHA-256 hex digest of the original source PDF bytes (required for --apply).",
    )
    parser.add_argument(
        "--backup",
        type=str,
        default=None,
        help="Path where exclusive 0600 JSON backup will be written before apply (required for --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply repair transactionally. If omitted, tool runs in read-only dry-run mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Explicitly run in read-only dry-run mode (default).",
    )
    parser.add_argument(
        "--pdf-path",
        type=str,
        default=None,
        help="Optional local path to source PDF if different from doc.file_path (e.g. host vs container path).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Optional SQLAlchemy database connection URL (defaults to app database setting).",
    )
    return parser.parse_args(argv)


def get_db_session(db_url: str | None) -> Session:
    """Create and return a database session."""
    if db_url:
        engine = create_engine(db_url)
        custom_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return custom_session()
    return SessionLocal()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Determine execution mode: apply requires explicit flag and both sha256 and backup
    is_apply = args.apply and not args.dry_run

    if is_apply:
        if not args.expected_sha256:
            print(
                "ERROR: --expected-sha256 is required when --apply is specified.",
                file=sys.stderr,
            )
            return 1
        if not args.backup:
            print(
                "ERROR: --backup <path> is required when --apply is specified.",
                file=sys.stderr,
            )
            return 1
    else:
        # Dry-run mode
        if not args.expected_sha256:
            # For dry-run, if expected-sha256 is not supplied, require it to be safe
            print(
                "ERROR: --expected-sha256 is required for validation.",
                file=sys.stderr,
            )
            return 1

    mode_label = "APPLY" if is_apply else "DRY-RUN"
    print("==================================================")
    print(f"Legacy PDF Extraction Repair Tool - Mode: {mode_label}")
    print(f"Document ID: {args.document_id}")
    print(f"Expected SHA-256: {args.expected_sha256}")
    if is_apply:
        print(f"Backup target: {args.backup}")
    if args.pdf_path:
        print(f"PDF override path: {args.pdf_path}")
    print("==================================================")

    db = get_db_session(args.db_url)
    try:
        result = repair_legacy_pdf_extraction(
            db=db,
            document_id=args.document_id,
            expected_sha256=args.expected_sha256,
            backup_path=args.backup if is_apply else None,
            apply=is_apply,
            file_path=args.pdf_path,
        )

        if result.get("noop"):
            print("\n[RESULT: NO-OP]")
            print(f"Document {args.document_id} is already in current format.")
            print(result.get("reason", "No changes needed."))
            return 0

        if result.get("dry_run"):
            print("\n[RESULT: DRY-RUN SUCCESS]")
            print(f"Title: {result.get('title')}")
            print(f"Total pages: {result.get('num_pages')}")
            print(f"Layout status: {result.get('layout_status')}")
            print(f"Detected format: {result.get('classification')}")
            print(f"Pages to repair: {result.get('pages_to_repair')}")
            print("\nNo database changes were made. No backup file was created.")
            print("\nTo apply this repair, re-run with:")
            print(
                f"  {sys.argv[0]} --apply --document-id {args.document_id} "
                f"--expected-sha256 {args.expected_sha256} --backup <backup_path.json>"
            )
            return 0

        if result.get("applied"):
            print("\n[RESULT: REPAIR APPLIED SUCCESSFULLY]")
            print(f"Document ID: {result.get('document_id')}")
            print(f"Backup written to: {result.get('backup_path')} (mode 0600)")
            print(f"Updated pages in place: {result.get('updated_pages')} (IDs preserved)")
            print(f"Invalidated retrieval chunks: {result.get('deleted_chunks')}")
            print(f"Invalidated embedding backfill records: {result.get('deleted_backfills')}")
            print(f"Recomputed narration exclusions: {result.get('recomputed_exclusions_count')}")
            print(f"Preserved layout status: {result.get('layout_status')}")
            return 0

        print(f"\n[RESULT: UNEXPECTED STATE] {result}", file=sys.stderr)
        return 1

    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\nUNEXPECTED FAILURE: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
