# Legacy PDF Extraction Maintenance Repair

## Overview

A dedicated administrative maintenance repair utility for restoring modern word-level bounding boxes and untouched line extraction for documents parsed under the legacy Docling pipeline, while strictly preserving document identity, ownership, library memberships, notes, bookmarks, conversations, completed summaries, and cached layout regions.

## Ownership and Code Location

- Service: `backend/app/services/extraction_repair.py`
- Maintenance CLI: `scripts/repair_legacy_pdf_extraction.py`
- Test suite: `backend/tests/test_extraction_repair.py`

## Core Safety Guarantees

1. **Dry-Run by Default**:
   Invoking `scripts/repair_legacy_pdf_extraction.py` without `--apply` runs in read-only dry-run mode, inspecting document state, verifying hashes, and reporting detected box formats without database modifications or backup writes.

2. **Explicit Verification Required for Apply**:
   Applying changes requires `--apply`, `--document-id <ID>`, `--expected-sha256 <HASH>`, and `--backup <PATH>`. The tool strictly refuses if any parameter is missing.

3. **Locking and Active-Job Refusal**:
   Before modifying anything, the repair tool acquires a transactional row lock (`SELECT ... FOR UPDATE`) and advisory transaction lock (PostgreSQL namespace `2030401`). Under lock, it checks and refuses if:
   - Document `processing_status` is not in `{"completed", "extracted", "failed"}` (e.g. queued, parsing, extracting, pending).
   - Document `layout_status` is in `{"queued", "running", "pending"}`.
   - Any `EmbeddingBackfill` record has status in `{"pending", "queued", "running"}`.
   - Any `Summary` record has status in `{"generating", "queued", "running"}`.
   - Any `KgExtractionState` record on `kg_extraction_state` has status in `{"pending", "extracting", "queued", "running"}`.

4. **Double SHA-256 Validation**:
   The source PDF bytes are hashed and verified against `--expected-sha256` (and `doc.content_hash`) before parsing, and re-verified immediately after parsing before any database write.

5. **Exclusive Pre-Repair Backup (Mode 0600)**:
   A JSON backup of the pre-repair document metadata, all existing `ExtractedText` rows, and `EmbeddingBackfill` state is written using `os.O_CREAT | os.O_EXCL` and mode `0600`. If the backup file already exists, repair refuses.

6. **In-Place Update Preserving IDs**:
   Existing `ExtractedText.id` values are preserved by updating rows in place. Stale Docling tables and images are cleared or replaced by fresh parser structures.

7. **Layout Status and Exclusions**:
   - Ready layout regions (`layout_status == "ready"`) are preserved on each page.
   - Narration exclusions are recomputed from the new word bounding boxes and cached layout regions using pdfplumber page dimensions without re-running Docling inference.
   - Documents with `layout_status == "not_requested"` preserve that status without queuing layout tasks.

8. **Invalidation of Derivative Indices**:
   - Stale chunk rows are cleared via `retrieval.delete_document_chunks(db, doc_id)`.
   - Stale embedding backfill records are cleared so fresh embeddings can be generated on demand.

9. **Idempotence**:
   Applying repair to an already-current document (word-level bounding boxes) immediately no-ops without database modifications or backup file creation.

## Usage

```bash
# Dry-run inspection
/home/mrx/.local/venv/bin/python scripts/repair_legacy_pdf_extraction.py \
  --document-id 3 \
  --expected-sha256 9185ccc01c249b68444af1808f17868b71c11afecb29c3fbc59111f6bf5b16ec

# Apply maintenance repair with backup
/home/mrx/.local/venv/bin/python scripts/repair_legacy_pdf_extraction.py \
  --apply \
  --document-id 3 \
  --expected-sha256 9185ccc01c249b68444af1808f17868b71c11afecb29c3fbc59111f6bf5b16ec \
  --backup /path/to/backup_doc3.json
```
