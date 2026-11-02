"""Strip Markdown syntax down to plain speakable text for TTS.

Summaries are now written in Markdown (headings, **bold**, bullet lists,
etc. - see prompts.py), but TTS engines read the literal symbols aloud ("hash
hash", "star star") if they're sent as-is. This is only meant for
summary-derived text; raw PDF page text is never Markdown and must not be
run through this.
"""
import re


def strip_markdown(text: str) -> str:
    """Convert Markdown text to plain, speakable text using only stdlib re."""
    if not text:
        return text

    out = text

    # Fenced code blocks: drop the fence markers, keep the code text.
    out = re.sub(r"```[a-zA-Z0-9]*\n?", "", out)

    # Images: ![alt](url) -> alt
    out = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", out)
    # Links: [text](url) -> text
    out = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", out)

    # Headings: leading #'s
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.MULTILINE)

    # Bold/italic/strikethrough markers
    out = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"\*(.+?)\*", r"\1", out)
    out = re.sub(r"___(.+?)___", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", out)
    out = re.sub(r"~~(.+?)~~", r"\1", out)

    # Inline code / backticks
    out = out.replace("`", "")

    # Blockquote markers
    out = re.sub(r"^\s{0,3}>\s?", "", out, flags=re.MULTILINE)

    # List markers: -, *, +, and numbered "1."
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"^\s*\d+\.\s+", "", out, flags=re.MULTILINE)

    # Horizontal rules
    out = re.sub(r"^\s*([-*_])\1{2,}\s*$", "", out, flags=re.MULTILINE)

    # Collapse repeated blank lines and trim
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = "\n".join(line.strip() for line in out.splitlines())
    out = out.strip()

    return out
