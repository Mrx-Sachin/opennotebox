"""Compare eager and deferred layout with real PDFs in fresh processes.

This times extraction, not network upload, broker delay, database persistence,
or browser rendering. Each baseline run really executes Docling on the CPU.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/home/mrx/.local/venv/bin/python"


def _worker(arguments: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    spec = importlib.util.spec_from_file_location("benchmark_pdf_parser", arguments.source)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot import parser source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = module.PDFParserService()
    started = time.perf_counter()
    parsed = parser.parse_pdf(str(arguments.pdf))
    seconds = time.perf_counter() - started
    if not parsed or not parsed.get("pages"):
        raise RuntimeError("Extraction produced no pages")
    pages = parsed["pages"]
    words = sum(len(page.get("bounding_boxes") or []) for page in pages)
    regions = sum(len(page.get("layout_regions") or []) for page in pages)
    if not words or not any(page["content"].strip() for page in pages):
        raise RuntimeError("Extraction did not execute text and word geometry work")
    if arguments.variant == "eager" and not regions:
        raise RuntimeError("Baseline did not execute successful layout analysis")
    if arguments.variant == "deferred" and regions:
        raise RuntimeError("Initial extraction unexpectedly performed layout analysis")
    canonical = [{"page_number": page["page_number"], "content": page["content"]} for page in pages]
    result = {
        "variant": arguments.variant,
        "seconds": seconds,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "pages": len(pages),
        "word_boxes": words,
        "layout_regions": regions,
        "text_sha256": hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode()).hexdigest(),
    }
    arguments.output.write_text(json.dumps(result, indent=2) + "\n")


def _summary(samples: list[dict], field: str) -> dict:
    values = [sample[field] for sample in samples]
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--pdf", type=Path, required=True)
    argument_parser.add_argument("--baseline", default="596cd40")
    argument_parser.add_argument("--repetitions", type=int, default=5)
    argument_parser.add_argument("--output", type=Path, required=True)
    argument_parser.add_argument("--source", type=Path)
    argument_parser.add_argument("--variant", choices=("eager", "deferred"))
    arguments = argument_parser.parse_args()
    if arguments.source:
        _worker(arguments)
        return
    if arguments.repetitions < 5:
        argument_parser.error("At least five alternating samples are required")
    pdf_path = arguments.pdf.resolve(strict=True)
    baseline_source = subprocess.check_output(
        ["git", "show", f"{arguments.baseline}:backend/app/services/pdf_parser.py"],
        cwd=ROOT, text=True,
    )
    samples = []
    with tempfile.TemporaryDirectory(prefix="optional-layout-benchmark-") as temporary:
        directory = Path(temporary)
        baseline_path = directory / "baseline.py"
        baseline_path.write_text(baseline_source)
        candidate_path = directory / "candidate.py"
        candidate_path.write_text((ROOT / "backend/app/services/pdf_parser.py").read_text())
        for repetition in range(arguments.repetitions):
            for variant, source in (
                ("eager", baseline_path),
                ("deferred", candidate_path),
            ):
                output = directory / f"{variant}-{repetition}.json"
                subprocess.run(
                    [PYTHON, str(Path(__file__).resolve()), "--pdf", str(pdf_path),
                     "--output", str(output), "--source", str(source), "--variant", variant],
                    cwd=ROOT, check=True, capture_output=True, text=True, timeout=600,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"},
                )
                sample = json.loads(output.read_text())
                samples.append(sample)
                print(f"{repetition + 1}/{arguments.repetitions} {variant}: "
                      f"{sample['seconds']:.3f}s, peak RSS {sample['process_peak_rss_mib']:.1f} MiB", flush=True)
    eager = [sample for sample in samples if sample["variant"] == "eager"]
    deferred = [sample for sample in samples if sample["variant"] == "deferred"]
    report = {
        "baseline": arguments.baseline,
        "pdf": str(pdf_path),
        "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "load_average_after": os.getloadavg(),
        "cpu_count": os.cpu_count(),
        "versions": {name: importlib.metadata.version(name) for name in ("docling", "pdfplumber", "pypdfium2")},
        "scope": "Fresh-process parse_pdf with automatic OCR detection, full text and word boxes. "
                 "Eager includes actual Docling layout and exclusions; deferred never runs Docling. "
                 "Peak RSS is the entire parser process, not stack memory. No network, DB, broker, "
                 "browser, OCR-image benchmark, or simultaneous-user capacity claim.",
        "failures": 0,
        "exact_page_text_equivalent": len({sample["text_sha256"] for sample in samples}) == 1,
        "samples": samples,
        "summary": {
            variant: {field: _summary(values, field) for field in ("seconds", "process_peak_rss_mib")}
            for variant, values in (("eager", eager), ("deferred", deferred))
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"summary": report["summary"], "exact_page_text_equivalent": report["exact_page_text_equivalent"]}, indent=2))


if __name__ == "__main__":
    main()
