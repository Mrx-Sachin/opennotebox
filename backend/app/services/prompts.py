"""Jinja2 prompt templates for page and section summarization.

Reuses the prompt system pattern from open-notebook's ai-prompter library.
"""
import os
from typing import Dict

from loguru import logger


def _prompts_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", "prompts")),
        os.path.normpath(os.path.join(here, "..", "..", "..", "prompts")),
        "/app/prompts",
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


PROMPTS_DIR = _prompts_dir()


def load_template(template_name: str) -> str:
    """Load a Jinja2 prompt template from the prompts directory."""
    path = os.path.join(PROMPTS_DIR, f"{template_name}.jinja")
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Prompt template not found: {template_name}, using default")
        return _get_default(template_name)


def _get_default(template_name: str) -> str:
    defaults: Dict[str, str] = {
        "summary/page": (
            "You are an experienced, encyclopedic teacher guiding a student through this book,\n"
            "page by page. Explain what is on this page the way a great teacher would: in plain\n"
            "language, connecting ideas to why they matter, defining key terms as they come up,\n"
            "and keeping worked examples and important details intact - do not just compress the\n"
            "page into a shorter version of itself.\n\n"
            "Write the explanation in clean Markdown:\n"
            "- A short heading only if it genuinely helps orientation\n"
            "- **Bold** key terms the first time you define them\n"
            "- Bullet lists where they make a list of points easier to follow\n"
            "- Otherwise flowing prose, like a teacher talking through the material\n\n"
            "Do not use meta-commentary like \"this page discusses\" or \"in this page\" - just\n"
            "teach the content directly. Keep it under {{ max_words }} words.\n\n"
            "Page text:\n{{ content }}"
        ),
        "summary/section": (
            "You are an experienced, encyclopedic teacher guiding a student through the section\n"
            "titled \"{{ section_title }}\" of this book. Teach the material the way a great teacher\n"
            "would: explain it in plain language, connect ideas to why they matter, define key\n"
            "terms as they come up, and keep the worked examples and important details that make\n"
            "the section useful - do not just produce a compressed abstract.\n\n"
            "Write the explanation in clean Markdown:\n"
            "- Short headings to break the section into its natural parts\n"
            "- **Bold** key terms the first time you define them\n"
            "- Bullet lists where they make a list of points easier to follow\n"
            "- Otherwise flowing prose, like a teacher talking through the material\n\n"
            "Do not comment on the section title or structure, and do not use meta-commentary\n"
            "like \"this section covers\" - just teach the content directly. Keep it under\n"
            "{{ max_words }} words.\n\n"
            "Section content:\n{{ content }}"
        ),
        "chat/system": (
            "You are a precise research assistant answering questions from provided documents."
        ),
        # Fallbacks matching prompts/podcast/dialogue.jinja and
        # prompts/flashcards/generate.jinja, used when the prompts directory is
        # not available inside the container (only ./backend/app is mounted).
        "podcast/dialogue": (
            "<system>You are an expert podcast script writer.</system>\n"
            "Your task is to turn the document material below into an engaging, conversational\n"
            "two-host podcast dialogue. The hosts are \"A\" and \"B\". The material may be messy\n"
            "or fragmentary (extracted from a PDF) - don't worry about formatting artifacts;\n"
            "your job is to extract the key ideas, facts, and takeaways worth discussing.\n\n"
            "{% if section_title %}\n"
            "This podcast covers the section titled \"{{ section_title }}\" from the document\n"
            "\"{{ document_title }}\".\n"
            "{% else %}\n"
            "This podcast covers the full document \"{{ document_title }}\".\n"
            "{% endif %}\n"
            "{% if segment_count and segment_count > 1 %}\n"
            "This is a long deep-dive episode being recorded across {{ segment_count }} back-to-\n"
            "back segments so the whole document can be covered in depth. You are writing\n"
            "segment {{ segment_index }} of {{ segment_count }}. The material below is only the\n"
            "portion of the document assigned to THIS segment - do not worry about or reference\n"
            "material outside it, the other segments cover the rest.\n"
            "{% endif %}\n\n"
            "{% if continuation_tail %}\n"
            "Here is how the conversation ended at the close of the previous segment:\n"
            "<previous_segment_ending>\n{{ continuation_tail }}\n</previous_segment_ending>\n"
            "Continue the SAME conversation naturally from there. Do not greet the audience\n"
            "again, do not reintroduce the hosts, and do not repeat points already made above -\n"
            "move on to the new material below.\n\n"
            "{% endif %}"
            "Material for {% if continuation_tail %}this segment{% else %}the episode{% endif %}:\n"
            "{{ content }}\n\n"
            "First, in a scratchpad, brainstorm before writing any dialogue: identify the main\n"
            "topics and the most interesting or surprising points, think of analogies or\n"
            "examples that make abstract ideas concrete, note any jargon that needs a plain-\n"
            "language explanation the first time it comes up, and rough out the order the\n"
            "hosts should move through these points so the segment has a natural arc.\n"
            "This is thinking space, not the transcript itself.\n\n"
            "Then write the actual dialogue based on that brainstorm. The hosts should sound\n"
            "natural: build on each other's points, ask each other real questions, react with\n"
            "brief commentary, occasionally disagree or add nuance rather than just taking\n"
            "turns lecturing. Avoid reading the material verbatim - paraphrase and explain it\n"
            "the way two curious, well-prepared hosts would discuss it live. Define jargon in\n"
            "plain language the first time a term comes up. Do not use bracketed placeholders\n"
            "like [Host] or make up the hosts' real names beyond \"A\"/\"B\". Design the dialogue\n"
            "to be read aloud - it will be converted directly to audio.\n"
            "{% if segment_index and segment_count and segment_index < segment_count %}\n"
            "Do NOT wrap up or conclude the episode in this segment - more material is coming\n"
            "next. End naturally mid-flow, as hosts transitioning toward another point, not as\n"
            "a sign-off or recap.\n"
            "{% else %}\n"
            "End with the hosts naturally recapping the key takeaways from the {% if segment_count and segment_count > 1 %}whole episode{% else %}conversation{% endif %} "
            "as part of the conversation, not as an obvious \"in summary\" list.\n"
            "{% endif %}\n\n"
            "Produce {{ exchange_range }} exchanges total (an \"exchange\" is one line from\n"
            "either host) - enough for a roughly {{ target_minutes }} minute {% if segment_count and segment_count > 1 %}segment{% else %}episode{% endif %}.\n\n"
            "Respond with ONLY strict JSON, no markdown code fences, no commentary before or\n"
            "after, in exactly this shape:\n"
            "{\n"
            "  \"scratchpad\": \"<your brainstorming from above, a few sentences to a short\n"
            "  paragraph>\",\n"
            "  \"title\": \"<a short catchy episode title>\",\n"
            "  \"lines\": [\n"
            "    {\"speaker\": \"A\", \"text\": \"...\"},\n"
            "    {\"speaker\": \"B\", \"text\": \"...\"}\n"
            "  ]\n"
            "}\n"
        ),
        "flashcards/generate": (
            "<system>You are an expert study-aid writer, skilled at writing Anki-style\n"
            "spaced-repetition flashcards.</system>\n"
            "Create a set of flashcards covering the key facts, concepts, definitions, and\n"
            "arguments in the following document material. Where a flashcard is clearly about\n"
            "content from a single page, include that page number; otherwise use null.\n"
            "Produce between 15 and 30 cards covering the breadth of the material.\n\n"
            "{% raw %}"
            "Use two card types, whichever suits each piece of information best:\n"
            "- \"basic\": a clear, specific question and a concise, correct answer. Best for\n"
            "  questions that need real explanation (\"why\", \"how\", comparisons).\n"
            "- \"cloze\": a single sentence from the material with the key term(s) blanked out\n"
            "  using Anki cloze markup: {{c1::the hidden term}}. Best for precise facts,\n"
            "  names, dates, and definitions where recalling the exact term is the point. Use\n"
            "  a new cN index for each independent blank within one sentence (c1, c2, ...) only\n"
            "  when they are genuinely separate facts worth testing individually; otherwise blank\n"
            "  a single span with c1.\n"
            "{% endraw %}"
            "Prefer cloze for terminology/facts and basic for conceptual understanding - aim for\n"
            "a healthy mix of both, not all of one type.\n\n"
            "Document: \"{{ document_title }}\"\n\n"
            "Material:\n{{ content }}\n\n"
            "Respond with ONLY strict JSON, no markdown code fences, no commentary before or\n"
            "after, in exactly this shape:\n"
            "{% raw %}"
            "[\n"
            "  {\"type\": \"basic\", \"question\": \"...\", \"answer\": \"...\", \"page\": 3},\n"
            "  {\"type\": \"cloze\", \"text\": \"The mitochondria is the {{c1::powerhouse}} of\n"
            "  the {{c2::cell}}.\", \"page\": 5}\n"
            "]\n"
            "{% endraw %}"
        ),
        "flashcards/from_note": (
            "You turn a highlighted passage and the reader's own note into one study flashcard.\n"
            "Write a single clear question and a concise correct answer.\n\n"
            "Quote:\n{{ quote }}\n\n"
            "Reader's note:\n{{ note }}\n\n"
            "Respond with ONLY strict JSON: {\"question\": \"...\", \"answer\": \"...\"}"
        ),
    }
    return defaults.get(template_name, "Generate a summary of: {{ content }}")


def render_jinja(template_name: str, **context: object) -> str:
    """Render a prompt template using real Jinja2 syntax (e.g. {{ var }}, {% if %}).

    Used by newer templates (podcast/, flashcards/) that rely on actual Jinja
    control flow rather than the simplified str.format placeholders used by
    render_template/_get_default above.
    """
    from jinja2 import Template

    raw = load_template(template_name)
    return Template(raw).render(**context)


def render_template(
    template_name: str,
    *,
    content: str = "",
    section_title: str = "",
    max_words: int = 300,
    **extra: object,  # Allow any extra kwargs without type error
) -> str:
    """Render a prompt template with the given data.

    All on-disk templates under prompts/ use Jinja2 ({{ var }}). This used to
    call str.format(), which treats ``{{`` as an escaped brace and leaves the
    literal ``{ content }`` in the prompt - so after the prompts directory was
    mounted into containers, every summary silently asked the model to
    summarize a placeholder instead of the page text. Delegate to Jinja.
    """
    return render_jinja(
        template_name,
        content=content,
        section_title=section_title,
        max_words=max_words,
        **extra,
    )
