#!/usr/bin/env python3
"""Production Docling RAM and Quality Verification Suite.

Executes isolated subprocess benchmarks comparing:
1. git_head_4t: Git HEAD production parser (ThreadedDoclingParse, 4 CPU threads)
2. dirty_2t: Dirty working tree parser (ThreadedDoclingParse, 2 CPU threads)
3. pypdfium_2t: PyPdfium candidate parser (PyPdfiumDocumentBackend, 2 CPU threads)
4. pypdfium_4t: PyPdfium candidate parser (PyPdfiumDocumentBackend, 4 CPU threads)
5. standard_docling_full: Standard full Docling pipeline (table structure enabled)

Features:
- Process isolation per run (ru_maxrss from OS kernel via getrusage)
- Alternating execution order (A, B, C, ...) to avoid cache/drift bias
- Multi-page extraction fidelity checks:
  * Page text SHA-256 hash
  * Semantic layout region count, labels, and normalized bounding box IoU
  * Narration exclusion ranges and narratable text hash
- Cross-check against standard full Docling
- Export of comprehensive JSON evidence artifact
"""

import argparse
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_PDF = (
    REPO_ROOT
    / "prototypes"
    / "docling_quantization_benchmark"
    / "basper.pdf"
)
OUTPUT_JSON = (
    REPO_ROOT
    / "prototypes"
    / "docling_quantization_benchmark"
    / "production_verification"
    / "production_verification_results.json"
)


def compute_bbox_iou(box1: List[float], box2: List[float]) -> float:
    """Calculate Intersection over Union (IoU) of two [l, t, r, b] boxes."""
    l1, t1, r1, b1 = box1
    l2, t2, r2, b2 = box2

    inter_l = max(l1, l2)
    inter_t = max(t1, t2)
    inter_r = min(r1, r2)
    inter_b = min(b1, b2)

    inter_w = max(0.0, inter_r - inter_l)
    inter_h = max(0.0, inter_b - inter_t)
    inter_area = inter_w * inter_h

    area1 = max(0.0, r1 - l1) * max(0.0, b1 - t1)
    area2 = max(0.0, r2 - l2) * max(0.0, b2 - t2)
    union_area = area1 + area2 - inter_area

    if union_area <= 0.0:
        return 1.0 if inter_area <= 0.0 else 0.0
    return inter_area / union_area


def run_worker(
    config_id: str,
    pdf_path: str,
    page_range: str,
    output_file: str,
) -> None:
    """Subprocess worker that executes one run and dumps result to json."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    t_start = time.perf_counter()
    status = "success"
    err_msg = ""
    result_data: Dict[str, Any] = {}

    target_pdf = pdf_path
    is_temp = False

    try:
        import types
        from unittest.mock import patch

        import pypdfium2 as pdfium
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.base_models import ConversionStatus, InputFormat
        from docling.document_converter import DocumentConverter

        from app.services.narration import get_narratable_text
        from app.services.pdf_parser import PDFParserService

        if page_range and page_range != "all":
            start_p, end_p = [int(p.strip()) for p in page_range.split("-")]
            with pdfium.PdfDocument(pdf_path) as doc:
                end_p = min(end_p, len(doc))
                if not 1 <= start_p <= end_p:
                    raise ValueError("Invalid page range")
                with pdfium.PdfDocument.new() as dst:
                    dst.import_pages(doc, list(range(start_p - 1, end_p)))
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        target_pdf = tmp.name
                    dst.save(target_pdf)
            is_temp = True

        baseline_ref = "596cd40c848a7c9bb761d0bcc051c9763a21c202"
        baseline_source = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:backend/app/services/pdf_parser.py"],
            cwd=REPO_ROOT, text=True,
        )
        baseline_module = types.ModuleType("docling_baseline_parser")
        exec(compile(baseline_source, "git_baseline_pdf_parser.py", "exec"),
             baseline_module.__dict__)
        parser_class = (
            PDFParserService if config_id == "production_current"
            else baseline_module.PDFParserService
        )
        captured = []

        def make_converter(*args, **kwargs):
            option = kwargs["format_options"][InputFormat.PDF]
            if config_id != "production_current":
                option.pipeline_options.accelerator_options.num_threads = (
                    4 if config_id in {"git_head_4t", "pypdfium_4t", "standard_docling_full"}
                    else 2
                )
                if "pypdfium" in config_id:
                    option.backend = PyPdfiumDocumentBackend
                if config_id == "standard_docling_full":
                    option.pipeline_options.do_table_structure = True
            converter = DocumentConverter(*args, **kwargs)
            original_convert = converter.convert

            def convert(*convert_args, **convert_kwargs):
                converted = original_convert(*convert_args, **convert_kwargs)
                captured.append(converted)
                return converted

            converter.convert = convert
            return converter

        with patch("docling.document_converter.DocumentConverter", make_converter):
            parsed = parser_class().parse_pdf(target_pdf)
        with pdfium.PdfDocument(target_pdf) as doc:
            expected_pages = len(doc)
        if not parsed or len(parsed.get("pages", [])) != expected_pages:
            raise RuntimeError("Production parser did not return all expected pages")
        if len(captured) != 1 or captured[0].status != ConversionStatus.SUCCESS:
            raise RuntimeError("Docling conversion failed or was skipped")
        pages_info = []
        for page in parsed["pages"]:
            content = page["content"]
            exclusions = page["narration_exclusions"]
            regions = page["layout_regions"]
            if not regions:
                raise RuntimeError(f"Missing Docling regions on page {page['page_number']}")
            spoken = get_narratable_text(content, exclusions, regions)
            info = {
                "page_number": page["page_number"],
                "text_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "text_len": len(content),
                "num_bounding_boxes": len(page.get("bounding_boxes") or []),
                "num_layout_regions": len(regions),
                "layout_regions": regions,
                "num_narration_exclusions": len(exclusions),
                "narration_exclusions": exclusions,
                "narratable_text_sha256": hashlib.sha256(spoken.encode()).hexdigest(),
                "narratable_text_len": len(spoken),
            }
            if config_id == "standard_docling_full":
                from rapidfuzz.distance import Levenshtein
                import re

                markdown = captured[0].document.export_to_markdown(page_no=page["page_number"])
                def normalize(text):
                    return re.sub(r"\s+", " ", text).strip()
                native_text, docling_text = normalize(content), normalize(markdown)
                native_tokens = set(re.findall(r"\w+", native_text.lower()))
                docling_tokens = set(re.findall(r"\w+", docling_text.lower()))
                union = native_tokens | docling_tokens
                info["standard_markdown"] = markdown
                info["markdown_vs_production"] = {
                    "whitespace_normalized_edit_similarity": Levenshtein.normalized_similarity(
                        native_text, docling_text
                    ),
                    "token_jaccard": len(native_tokens & docling_tokens) / len(union) if union else 1,
                }
            pages_info.append(info)
        result_data = {
            "config_id": config_id,
            "num_pages": parsed["num_pages"],
            "baseline_ref": baseline_ref,
            "parser_source_sha256": hashlib.sha256(
                (Path(BACKEND_DIR / "app/services/pdf_parser.py").read_text()
                 if config_id == "production_current" else baseline_source).encode()
            ).hexdigest(),
            "pages": pages_info,
        }

    except Exception as exc:
        status = "error"
        err_msg = f"{type(exc).__name__}: {exc}"
    finally:
        if is_temp and os.path.exists(target_pdf):
            try:
                os.unlink(target_pdf)
            except OSError:
                pass

    t_end = time.perf_counter()
    latency_s = round(t_end - t_start, 4)
    # ru_maxrss on Linux is in KiB; convert to MiB
    peak_rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)

    payload = {
        "config_id": config_id,
        "status": status,
        "error_message": err_msg,
        "cpu_s": round(resource.getrusage(resource.RUSAGE_SELF).ru_utime + resource.getrusage(resource.RUSAGE_SELF).ru_stime, 4),
        "latency_s": latency_s,
        "peak_rss_mb": peak_rss_mb,
        "result": result_data,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def execute_subprocess_run(
    config_id: str,
    pdf_path: str,
    page_range: str,
    python_bin: str = "/home/mrx/.local/venv/bin/python",
) -> Dict[str, Any]:
    """Execute a single run in an isolated subprocess."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_out:
        tmp_out_path = tmp_out.name

    try:
        cmd = [
            python_bin,
            str(Path(__file__).resolve()),
            "--worker",
            "--config-id",
            config_id,
            "--pdf",
            str(pdf_path),
            "--pages",
            page_range,
            "--output-file",
            tmp_out_path,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            return {
                "config_id": config_id,
                "status": "subprocess_failed",
                "error_message": f"Exit code {proc.returncode}: {proc.stderr}",
                "latency_s": 0.0,
                "peak_rss_mb": 0.0,
                "result": {},
            }
        with open(tmp_out_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {
            "config_id": config_id,
            "status": "exception",
            "error_message": str(exc),
            "latency_s": 0.0,
            "peak_rss_mb": 0.0,
            "result": {},
        }
    finally:
        if os.path.exists(tmp_out_path):
            try:
                os.unlink(tmp_out_path)
            except OSError:
                pass


def compare_pages(
    base_pages: List[Dict[str, Any]],
    cand_pages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate agreement metrics between baseline and candidate pages."""
    if not base_pages or len(base_pages) != len(cand_pages):
        return {"match": False, "reason": "Page count mismatch"}

    total_text_matches = 0
    total_narratable_matches = 0
    total_region_count_matches = 0
    total_exclusion_matches = 0

    ious: List[float] = []
    label_matches = 0
    total_matched_regions = 0

    per_page_details = []

    for b_page, c_page in zip(base_pages, cand_pages):
        p_no = b_page["page_number"]
        text_match = b_page["text_sha256"] == c_page["text_sha256"]
        narr_match = (
            b_page["narratable_text_sha256"] == c_page["narratable_text_sha256"]
        )
        reg_count_match = (
            b_page["num_layout_regions"] == c_page["num_layout_regions"]
        )
        excl_match = (
            b_page["narration_exclusions"] == c_page["narration_exclusions"]
        )

        if text_match:
            total_text_matches += 1
        if narr_match:
            total_narratable_matches += 1
        if reg_count_match:
            total_region_count_matches += 1
        if excl_match:
            total_exclusion_matches += 1

        b_regs = b_page["layout_regions"]
        c_regs = c_page["layout_regions"]

        from scipy.optimize import linear_sum_assignment

        page_ious = []
        page_lbl_matches = 0
        min_len = min(len(b_regs), len(c_regs))
        if min_len:
            weights = [[compute_bbox_iou(b["bbox"], c["bbox"]) for c in c_regs] for b in b_regs]
            rows, columns = linear_sum_assignment(weights, maximize=True)
            for row, column in zip(rows, columns):
                iou = weights[row][column]
                page_ious.append(iou)
                ious.append(iou)
                if b_regs[row]["label"] == c_regs[column]["label"]:
                    label_matches += 1
                    page_lbl_matches += 1
                total_matched_regions += 1

        per_page_details.append({
            "page_number": p_no,
            "text_match": text_match,
            "narratable_text_match": narr_match,
            "num_regions_baseline": len(b_regs),
            "num_regions_candidate": len(c_regs),
            "region_count_match": reg_count_match,
            "mean_iou": round(statistics.mean(page_ious), 4) if page_ious else 1.0,
            "label_agreement_pct": (
                round(100.0 * page_lbl_matches / max(len(b_regs), len(c_regs)), 2)
                if max(len(b_regs), len(c_regs)) else 100.0
            ),
            "exclusions_baseline": b_page["narration_exclusions"],
            "exclusions_candidate": c_page["narration_exclusions"],
            "exclusions_match": excl_match,
        })

    num_pages = len(base_pages)
    return {
        "num_pages": num_pages,
        "text_match_pct": round(100.0 * total_text_matches / num_pages, 2),
        "narratable_text_match_pct": round(
            100.0 * total_narratable_matches / num_pages, 2
        ),
        "region_count_match_pct": round(
            100.0 * total_region_count_matches / num_pages, 2
        ),
        "exclusion_exact_match_pct": round(
            100.0 * total_exclusion_matches / num_pages, 2
        ),
        "mean_bbox_iou": round(statistics.mean(ious), 4) if ious else 1.0,
        "label_agreement_pct": (
            round(100.0 * label_matches / total_matched_regions, 2)
            if total_matched_regions > 0
            else 100.0
        ),
        "pages": per_page_details,
    }


def run_benchmark_matrix(
    configs: List[str],
    pdf_path: str,
    page_range: str,
    num_runs: int = 5,
) -> Dict[str, Any]:
    """Execute alternating runs across configurations and aggregate results."""
    print(f"Starting alternating benchmark with {num_runs} runs per config...")
    print(f"Configurations: {configs}")
    print(f"PDF: {pdf_path} (pages: {page_range})")

    all_runs: List[Dict[str, Any]] = []

    # Interleaved run plan: A, B, C, ..., A, B, C, ...
    for run_idx in range(1, num_runs + 1):
        for config_id in configs:
            print(
                f"[{time.strftime('%H:%M:%S')}] Round {run_idx}/{num_runs}: Running {config_id}...",
                flush=True,
            )
            data = execute_subprocess_run(config_id, pdf_path, page_range)
            data["round"] = run_idx
            all_runs.append(data)
            print(
                f"  -> status: {data['status']}, RSS: {data['peak_rss_mb']} MiB, latency: {data['latency_s']} s",
                flush=True,
            )

    # Group runs by config_id
    grouped: Dict[str, List[Dict[str, Any]]] = {c: [] for c in configs}
    for r in all_runs:
        grouped[r["config_id"]].append(r)

    summary: Dict[str, Any] = {}
    for c, runs in grouped.items():
        success_runs = [r for r in runs if r["status"] == "success"]
        fail_count = len(runs) - len(success_runs)
        rss_vals = [r["peak_rss_mb"] for r in success_runs]
        lat_vals = [r["latency_s"] for r in success_runs]

        summary[c] = {
            "total_runs": len(runs),
            "successful_runs": len(success_runs),
            "failures": fail_count,
            "output_stable_across_runs": bool(success_runs) and all(
                run["result"].get("pages") == success_runs[0]["result"].get("pages")
                for run in success_runs
            ),
            "peak_rss_mb": {
                "median": round(statistics.median(rss_vals), 2) if rss_vals else 0.0,
                "min": round(min(rss_vals), 2) if rss_vals else 0.0,
                "max": round(max(rss_vals), 2) if rss_vals else 0.0,
                "range": (
                    round(max(rss_vals) - min(rss_vals), 2) if rss_vals else 0.0
                ),
                "values": rss_vals,
            },
            "latency_s": {
                "median": round(statistics.median(lat_vals), 2) if lat_vals else 0.0,
                "min": round(min(lat_vals), 2) if lat_vals else 0.0,
                "max": round(max(lat_vals), 2) if lat_vals else 0.0,
                "range": (
                    round(max(lat_vals) - min(lat_vals), 2) if lat_vals else 0.0
                ),
                "values": lat_vals,
            },
        }

    # Compare fidelity against git_head_4t or dirty_2t baseline
    baseline_id = "git_head_4t" if "git_head_4t" in grouped else configs[0]
    baseline_sample = next(
        (r for r in grouped[baseline_id] if r["status"] == "success"), None
    )

    fidelity_comparisons: Dict[str, Any] = {}
    if baseline_sample and baseline_sample.get("result", {}).get("pages"):
        base_pages = baseline_sample["result"]["pages"]
        for c in configs:
            if c == baseline_id:
                continue
            cand_sample = next(
                (r for r in grouped[c] if r["status"] == "success"), None
            )
            if cand_sample and cand_sample.get("result", {}).get("pages"):
                cand_pages = cand_sample["result"]["pages"]
                fidelity_comparisons[f"{c}_vs_{baseline_id}"] = compare_pages(
                    base_pages, cand_pages
                )

    return {
        "metadata": {
            "baseline_commit": "596cd40c848a7c9bb761d0bcc051c9763a21c202",
            "pdf_sha256": hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest(),
            "python": sys.version,
            "cpu_count": os.cpu_count(),
            "load_average": os.getloadavg(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pdf_path": str(pdf_path),
            "page_range": page_range,
            "num_rounds": num_runs,
            "configs": configs,
            "baseline": baseline_id,
        },
        "summary": summary,
        "fidelity_comparisons": fidelity_comparisons,
        "raw_runs": all_runs,
    }


def print_report_table(benchmark_data: Dict[str, Any]) -> None:
    """Print readable summary tables for terminal and logs."""
    summary = benchmark_data["summary"]
    meta = benchmark_data["metadata"]

    print("\n" + "=" * 90)
    print(f"DOCLING BENCHMARK VERIFICATION REPORT (Pages: {meta['page_range']}, Rounds: {meta['num_rounds']})")
    print("=" * 90)
    print(
        f"{'Configuration':<25} | {'Peak RSS (MiB) median [min, max]':<32} | {'Latency (s) median [min, max]':<26} | {'Failures'}"
    )
    print("-" * 90)

    base_rss = 0.0
    if meta["baseline"] in summary:
        base_rss = summary[meta["baseline"]]["peak_rss_mb"]["median"]

    for c, s in summary.items():
        rss = s["peak_rss_mb"]
        lat = s["latency_s"]
        delta_str = ""
        if base_rss > 0 and c != meta["baseline"] and rss["median"] > 0:
            diff = rss["median"] - base_rss
            pct = (diff / base_rss) * 100.0
            delta_str = f" ({diff:+.1f} MiB, {pct:+.1f}%)"

        rss_str = f"{rss['median']:.1f} [{rss['min']:.1f}, {rss['max']:.1f}]{delta_str}"
        lat_str = f"{lat['median']:.2f} [{lat['min']:.2f}, {lat['max']:.2f}]"
        print(f"{c:<25} | {rss_str:<32} | {lat_str:<26} | {s['failures']}")

    print("=" * 90)

    fidelities = benchmark_data.get("fidelity_comparisons", {})
    if fidelities:
        print("\nEXTRACTION & NARRATION FIDELITY (vs Baseline):")
        print("-" * 90)
        for comp_name, comp in fidelities.items():
            print(f"Comparison: {comp_name}")
            print(f"  Pages evaluated:            {comp['num_pages']}")
            print(f"  Text SHA-256 Match:        {comp['text_match_pct']}%")
            print(f"  Region Count Match:         {comp['region_count_match_pct']}%")
            print(f"  Label Agreement:            {comp['label_agreement_pct']}%")
            print(f"  Bounding Box Mean IoU:      {comp['mean_bbox_iou']}")
            print(f"  Narration Exclusion Match:  {comp['exclusion_exact_match_pct']}%")
            print(f"  Spoken Text Match:          {comp['narratable_text_match_pct']}%")
            print("-" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(description="Production Docling Verification")
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Run in isolated subprocess worker mode",
    )
    parser.add_argument("--config-id", type=str, default="git_head_4t")
    parser.add_argument("--pdf", type=str, default=str(DEFAULT_PDF))
    parser.add_argument("--pages", type=str, default="1-4")
    parser.add_argument("--output-file", type=str, default="")
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of alternating runs per configuration (default: 5)",
    )
    parser.add_argument(
        "--configs",
        type=str,
        default="git_head_4t,dirty_2t,production_current,standard_docling_full",
        help="Comma-separated configs to benchmark",
    )
    parser.add_argument(
        "--save-json",
        type=str,
        default=str(OUTPUT_JSON),
        help="Path to save full JSON evidence",
    )

    args = parser.parse_args()

    if args.worker:
        run_worker(args.config_id, args.pdf, args.pages, args.output_file)
        return

    config_list = [c.strip() for c in args.configs.split(",") if c.strip()]
    allowed = {"git_head_4t", "dirty_2t", "pypdfium_2t", "pypdfium_4t",
               "production_current", "standard_docling_full"}
    if not config_list or not set(config_list) <= allowed or args.runs < 1:
        parser.error("Select known configurations and a positive run count")
    results = run_benchmark_matrix(
        config_list,
        args.pdf,
        args.pages,
        num_runs=args.runs,
    )

    print_report_table(results)

    save_path = Path(args.save_json)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved structured JSON evidence to: {save_path}")
    if any(row["failures"] for row in results["summary"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
