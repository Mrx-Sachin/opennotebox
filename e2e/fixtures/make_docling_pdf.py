#!/usr/bin/env python3
"""Generate docling-narration fixture PDF with structured headings, tables, and captions."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

OUTPUT_PATH = Path(__file__).resolve().parent / "docling-narration.pdf"


def draw_header_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.gray)
    canvas.drawString(54, 750, "Journal of Automated Document Intelligence, Vol. 12, 2026")
    footer_text = f"Page {doc.page} of 3 - Meta DeepRead Technical Report"
    canvas.drawRightString(612 - 54, 35, footer_text)
    canvas.restoreState()


def generate_pdf() -> None:
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=60,
        bottomMargin=60,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitleStyle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        spaceAfter=12,
    )

    heading_style = ParagraphStyle(
        "DocHeadingStyle",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        spaceBefore=10,
        spaceAfter=8,
    )

    body_style = ParagraphStyle(
        "DocBodyStyle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=10,
    )

    caption_style = ParagraphStyle(
        "DocCaptionStyle",
        parent=styles["Italic"],
        fontSize=9,
        leading=12,
        textColor=colors.darkslategray,
        spaceBefore=6,
        spaceAfter=12,
    )

    story = [
        Paragraph("Neural Layout Analysis for Voice Narration", title_style),
        Paragraph(
            "Speech narration in digital reading systems requires extracting main body content "
            "while cleanly skipping tabular data, captions, and running page headers.",
            body_style
        ),
        Paragraph("Benchmark Evaluation", heading_style),
        Paragraph(
            "We evaluate deep parsing models across structured academic articles and reports.",
            body_style
        ),
        Table(
            [
                ["Architecture", "Precision", "Recall", "F1 Score"],
                ["Baseline Parser", "82.4%", "79.1%", "80.7%"],
                ["Docling Hybrid", "96.8%", "95.4%", "96.1%"],
            ],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]),
        ),
        Paragraph("Table 1: Quantitative evaluation comparing baseline parser with Docling hybrid layout analysis.", caption_style),
        Paragraph(
            "The experimental results demonstrate that neural layout analysis prevents unnatural TTS reading of table cells and captions.",
            body_style
        ),
        PageBreak(),

        Table(
            [
                ["Parameter", "Configuration Value", "Hardware Target"],
                ["Batch Size", "32 samples per device", "NVIDIA RTX 4090"],
                ["Learning Rate", "1e-4 with warmup", "Distributed DDP"],
                ["Dropout Rate", "0.15 regularized", "Float16 TensorCore"],
                ["Quantization", "8-bit integer weights", "ONNX Runtime CPU"],
            ],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.darkgray),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]),
        ),
        Paragraph("Table 2: Supplementary hyperparameters and target deployment hardware configurations.", caption_style),
        PageBreak(),

        Paragraph("Conclusion and Future Work", heading_style),
        Paragraph(
            "In summary, integrating deep layout models with speech synthesis ensures a seamless listening experience.",
            body_style
        ),
        Paragraph(
            "Future work will extend boundary detection to multi-column complex financial spreadsheets and scanned archives.",
            body_style
        ),
    ]

    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    print(f"Generated {OUTPUT_PATH}")


if __name__ == "__main__":
    generate_pdf()
