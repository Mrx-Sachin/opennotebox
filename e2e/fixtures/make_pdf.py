#!/usr/bin/env python3
"""Write the committed e2e sample PDF (2 pages, one URI annotation)."""
from __future__ import annotations

from pathlib import Path

PAGE1 = [
    "E2E Sample Paper",
    "This line is for click to read.",
    "Human-centered computing is a link target.",
    "There are genuinely good agentic frameworks available today.",
]
PAGE2 = [
    "Second page of the e2e sample.",
    "Local scripts handle the mechanical work.",
]

LINK_URI = "https://example.com/e2e-link"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_stream(lines: list[str], start_y: int = 720, leading: int = 18) -> str:
    cmds = ["BT", "/F1 14 Tf", f"72 {start_y} Td"]
    for i, line in enumerate(lines):
        if i:
            cmds.append(f"0 -{leading} Td")
        cmds.append(f"({_escape(line)}) Tj")
    cmds.append("ET")
    return "\n".join(cmds)


def build_pdf() -> bytes:
    # Line 3 on page 1 sits at y=720-36=684. PDF y is the baseline.
    # Cover that line with a URI annotation.
    s1 = _text_stream(PAGE1).encode("latin-1")
    s2 = _text_stream(PAGE2).encode("latin-1")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> "
            b"/Annots [8 0 R] >>"
        ),
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>"
        ),
        f"<< /Length {len(s1)} >>\nstream\n".encode("ascii") + s1 + b"\nendstream",
        f"<< /Length {len(s2)} >>\nstream\n".encode("ascii") + s2 + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Type /Annot /Subtype /Link /Rect [72 670 400 698] "
            b"/Border [0 0 0] /A << /S /URI /URI ("
            + LINK_URI.encode("ascii")
            + b") >> >>"
        ),
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii")
        out += body
        out += b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def main() -> None:
    dest = Path(__file__).with_name("e2e-sample.pdf")
    dest.write_bytes(build_pdf())
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
