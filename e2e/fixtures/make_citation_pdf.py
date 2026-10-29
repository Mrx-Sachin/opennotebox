#!/usr/bin/env python3
"""Generate the citation-evidence fixture PDF for frontend and backend E2E tests."""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUTPUT_PATH = Path(__file__).resolve().parent / "citation-evidence.pdf"

def generate_pdf():
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=letter,
        leftMargin=72,
        rightMargin=72,
        topMargin=72,
        bottomMargin=72
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        spaceAfter=12,
    )
    caption_style = ParagraphStyle(
        "CaptionStyle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontSize=11,
        leading=15,
        spaceAfter=12,
    )

    story = [
        Paragraph("Citation Provenance Verification Paper", title_style),
        Paragraph(
            "This is the introductory page for citation provenance verification testing. "
            "It introduces speech recognition models and scaling benchmarks.",
            body_style
        ),
        Spacer(1, 100),
        PageBreak(),
        # Page 2: Figure 8 Caption at the top
        Paragraph(
            "Figure 8. Zero-shot Whisper performance scales reliably across tasks and languages with increasing model size.",
            caption_style
        ),
        Spacer(1, 20),
        Paragraph(
            "Table 6. Performance improves with increasing dataset size.",
            caption_style
        ),
        Spacer(1, 10),
        # Page 2: Supporting paragraph below Table 6
        Paragraph(
            "All increases in the dataset size result in improved performance on all tasks, although we see significant variability in improvement rates across tasks and sizes. Performance improves rapidly on English speech recognition from 3,000 to 13,000 hours and then slows down noticeably between 13,000 and 54,000 hours.",
            body_style
        ),
        Spacer(1, 10),
        # Page 2: Second citation paragraph for multiple citations test
        Paragraph(
            "Multitask learning provides robust transfer across speech recognition and translation benchmarks with minimal fine-tuning required.",
            body_style
        ),
        Spacer(1, 10),
        # Page 2: Numeric signs / hyphens test paragraph
        Paragraph(
            "We evaluate models with -1 and +1 score adjustments across 1-3 training phases.",
            body_style
        ),
        Spacer(1, 10),
        # Page 2: Duplicate phrase for ambiguity testing
        Paragraph(
            "Identical repeated passage on page two for ambiguity testing. Identical repeated passage on page two for ambiguity testing.",
            body_style
        )
    ]

    doc.build(story)
    print(f"Generated {OUTPUT_PATH}")

if __name__ == "__main__":
    generate_pdf()
