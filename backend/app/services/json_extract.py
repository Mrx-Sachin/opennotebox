"""Robust extraction of a JSON object/array from an LLM text response.

LLMs frequently wrap JSON in markdown code fences or add stray commentary
before/after the JSON payload. This helper strips fences and falls back to
locating the outermost balanced {...} or [...] span.
"""
import json
import re
from typing import Any


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> Any:
    """Parse the first well-formed JSON value found in `text`.

    Raises ValueError if no valid JSON could be extracted.
    """
    if text is None:
        raise ValueError("Empty response")

    candidate = text.strip()
    candidate = _FENCE_RE.sub("", candidate).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fall back to scanning for the first balanced {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = candidate.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    span = candidate[start:i + 1]
                    try:
                        return json.loads(span)
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract valid JSON from response: {text[:500]!r}")
