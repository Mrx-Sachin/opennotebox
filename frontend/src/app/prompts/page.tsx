import { FileText } from "lucide-react"

// --- All system prompt templates embedded directly ---
// Mirrors backend/app/services/prompts.py defaults + prompts/*.jinja on-disk templates

const PROMPTS = [
  {
    name: "Page Summary",
    file: "prompts/summary/page.jinja",
    description: "Page-level document summarization",
    template: `<system>You are an expert document analyst.</system>
Create a page-level summary of the following text. Focus on the main arguments,
key points, and important details. Be factual and concise.

Text:
{{ content }}

Summary (under 300 words):`
  },
  {
    name: "Section Summary (On-Disk)",
    file: "prompts/summary/section.jinja",
    description: "Section-level document summarization (on-disk template)",
    template: `<system>You are an expert document analyst.</system>
Create a section-level summary for the section: "{{ section_title }}".
Include all main arguments, supporting evidence, and key takeaways.
Be comprehensive but concise.

Section content:
{{ content }}

Summary (under 500 words):`
  },
  {
    name: "Section Summary (Default)",
    file: "backend/app/services/prompts.py (default fallback)",
    description: "Section-level summarization -- hardcoded default when .jinja file is missing",
    template: `You are an experienced, encyclopedic teacher guiding a student through the section
titled "{{ section_title }}" of this book. Teach the material the way a great teacher
would: explain it in plain language, connect ideas to why they matter, define key
terms as they come up, and keep the worked examples and important details that make
the section useful - do not just produce a compressed abstract.

Write the explanation in clean Markdown:
- Short headings to break the section into its natural parts
- **Bold** key terms the first time you define them
- Bullet lists where they make a list of points easier to follow
- Otherwise flowing prose, like a teacher talking through the material

Do not comment on the section title or structure, and do not use meta-commentary
like "this section covers" - just teach the content directly. Keep it under
"{{ max_words }}" words.

Section content:
{{ content }}`
  },
  {
    name: "Page Summary (Default)",
    file: "backend/app/services/prompts.py (default fallback)",
    description: "Page-level summarization -- hardcoded default when .jinja file is missing",
    template: `You are an experienced, encyclopedic teacher guiding a student through this book,
page by page. Explain what is on this page the way a great teacher would: in plain
language, connecting ideas to why they matter, defining key terms as they come up,
and keeping worked examples and important details intact - do not just compress the
page into a shorter version of itself.

Write the explanation in clean Markdown:
- A short heading only if it genuinely helps orientation
- **Bold** key terms the first time you define them
- Bullet lists where they make a list of points easier to follow
- Otherwise flowing prose, like a teacher talking through the material

Do not use meta-commentary like "this page discusses" or "in this page" - just
teach the content directly. Keep it under {{ max_words }} words.

Page text:
{{ content }}`
  },
  {
    name: "Chat / Q&A (On-Disk)",
    file: "prompts/chat/qa.jinja",
    description: "Research assistant -- answers from document context only",
    template: `<system>You are a helpful, precise research assistant.</system>
You answer questions based ONLY on the provided document excerpts.
Cite sources using the format [Doc: page X].
Be factual. Never fabricate information not in the context.
If the answer is not in the context, say so explicitly.

Context:
{{ context }}

Question: {{ question }}

Answer:`
  },
  {
    name: "Chat / System (Default)",
    file: "backend/app/services/prompts.py (default fallback)",
    description: "Short system prompt for chat -- hardcoded default",
    template: `You are a precise research assistant answering questions from provided documents.`
  },
  {
    name: "Podcast Dialogue (On-Disk)",
    file: "prompts/podcast/dialogue.jinja",
    description: "Two-host podcast script from document material (on-disk template)",
    template: `<system>You are an expert podcast script writer.</system>
Write an engaging, conversational two-host podcast dialogue about the following
document material. The hosts are "A" and "B". They should sound natural, build on
each other's points, ask each other questions, react with brief commentary, and
cover the key ideas, facts, and takeaways from the material below. Avoid reading
the material verbatim - paraphrase and explain it the way two curious hosts would
discuss it on a podcast. Produce between 8 and 20 exchanges total (an "exchange"
is one line from either host).

{% if section_title %}
This podcast covers the section titled "{{ section_title }}" from the document
"{{ document_title }}".
{% else %}
This podcast covers the full document "{{ document_title }}".
{% endif %}

Material:
{{ content }}

Respond with ONLY strict JSON, no markdown code fences, no commentary before or
after, in exactly this shape:
{
  "title": "<a short catchy episode title>",
  "lines": [
    {"speaker": "A", "text": "..."},
    {"speaker": "B", "text": "..."}
  ]
}`
  },
  {
    name: "Podcast Dialogue (Default)",
    file: "backend/app/services/prompts.py (default fallback)",
    description: "Full podcast prompt with segmenting, continuation, scratchpad -- hardcoded default",
    template: `<system>You are an expert podcast script writer.</system>
Your task is to turn the document material below into an engaging, conversational
two-host podcast dialogue. The hosts are "A" and "B". The material may be messy
or fragmentary (extracted from a PDF) - don't worry about formatting artifacts;
your job is to extract the key ideas, facts, and takeaways worth discussing.

{% if section_title %}
This podcast covers the section titled "{{ section_title }}" from the document
"{{ document_title }}".
{% else %}
This podcast covers the full document "{{ document_title }}".
{% endif %}
{% if segment_count and segment_count > 1 %}
This is a long deep-dive episode being recorded across {{ segment_count }} back-to-
back segments so the whole document can be covered in depth. You are writing
segment {{ segment_index }} of {{ segment_count }}. The material below is only the
portion of the document assigned to THIS segment - do not worry about or reference
material outside it, the other segments cover the rest.
{% endif %}

{% if continuation_tail %}
Here is how the conversation ended at the close of the previous segment:
<previous_segment_ending>
{{ continuation_tail }}
</previous_segment_ending>
Continue the SAME conversation naturally from there. Do not greet the audience
again, do not reintroduce the hosts, and do not repeat points already made above -
move on to the new material below.

{% endif %}
Material for {% if continuation_tail %}this segment{% else %}the episode{% endif %}:
{{ content }}

First, in a scratchpad, brainstorm before writing any dialogue: identify the main
topics and the most interesting or surprising points, think of analogies or
examples that make abstract ideas concrete, note any jargon that needs a plain-
language explanation the first time it comes up, and rough out the order the
hosts should move through these points so the segment has a natural arc.
This is thinking space, not the transcript itself.

Then write the actual dialogue based on that brainstorm. The hosts should sound
natural: build on each other's points, ask each other real questions, react with
brief commentary, occasionally disagree or add nuance rather than just taking
turns lecturing. Avoid reading the material verbatim - paraphrase and explain it
the way two curious, well-prepared hosts would discuss it live. Define jargon in
plain language the first time a term comes up. Do not use bracketed placeholders
like [Host] or make up the hosts' real names beyond "A"/"B". Design the dialogue
to be read aloud - it will be converted directly to audio.
{% if segment_index and segment_count and segment_index < segment_count %}
Do NOT wrap up or conclude the episode in this segment - more material is coming
next. End naturally mid-flow, as hosts transitioning toward another point, not as
a sign-off or recap.
{% else %}
End with the hosts naturally recapping the key takeaways from the {% if segment_count and segment_count > 1 %}whole episode{% else %}conversation{% endif %}
as part of the conversation, not as an obvious "in summary" list.
{% endif %}

Produce {{ exchange_range }} exchanges total (an "exchange" is one line from
either host) - enough for a roughly {{ target_minutes }} minute {% if segment_count and segment_count > 1 %}segment{% else %}episode{% endif %}.

Respond with ONLY strict JSON, no markdown code fences, no commentary before or
after, in exactly this shape:
{
  "scratchpad": "<your brainstorming from above, a few sentences to a short
  paragraph>",
  "title": "<a short catchy episode title>",
  "lines": [
    {"speaker": "A", "text": "..."},
    {"speaker": "B", "text": "..."}
  ]
}`
  },
  {
    name: "Flashcards (On-Disk)",
    file: "prompts/flashcards/generate.jinja",
    description: "Study flashcards from document material (on-disk template)",
    template: `<system>You are an expert study-aid writer.</system>
Create a set of flashcards covering the key facts, concepts, definitions, and
arguments in the following document material. Each flashcard should have a clear,
specific question and a concise, correct answer. Where a flashcard is clearly
about content from a single page, include that page number; otherwise use null.
Produce between 15 and 30 flashcards covering the breadth of the material.

Document: "{{ document_title }}"

Material:
{{ content }}

Respond with ONLY strict JSON, no markdown code fences, no commentary before or
after, in exactly this shape:
[
  {"question": "...", "answer": "...", "page": 3},
  {"question": "...", "answer": "...", "page": null}
]`
  },
  {
    name: "Flashcards (Default)",
    file: "backend/app/services/prompts.py (default fallback)",
    description: "Full flashcards prompt with basic + cloze card types -- hardcoded default",
    template: `<system>You are an expert study-aid writer, skilled at writing Anki-style
spaced-repetition flashcards.</system>
Create a set of flashcards covering the key facts, concepts, definitions, and
arguments in the following document material. Where a flashcard is clearly about
content from a single page, include that page number; otherwise use null.
Produce between 15 and 30 cards covering the breadth of the material.

Use two card types, whichever suits each piece of information best:
- "basic": a clear, specific question and a concise, correct answer. Best for
  questions that need real explanation ("why", "how", comparisons).
- "cloze": a single sentence from the material with the key term(s) blanked out
  using Anki cloze markup: {{c1::the hidden term}}. Best for precise facts,
  names, dates, and definitions where recalling the exact term is the point. Use
  a new cN index for each independent blank within one sentence (c1, c2, ...) only
  when they are genuinely separate facts worth testing individually; otherwise blank
  a single span with c1.
Prefer cloze for terminology/facts and basic for conceptual understanding - aim for
a healthy mix of both, not all of one type.

Document: "{{ document_title }}"

Material:
{{ content }}

Respond with ONLY strict JSON, no markdown code fences, no commentary before or
after, in exactly this shape:
[
  {"type": "basic", "question": "...", "answer": "...", "page": 3},
  {"type": "cloze", "text": "The mitochondria is the {{c1::powerhouse}} of
  the {{c2::cell}}.", "page": 5}
]`
  },
  {
    name: "Structure Extraction (On-Disk)",
    file: "prompts/extraction/structure.jinja",
    description: "Extract headings, entities, and numerical data from page content",
    template: `<system>You are a document structuring assistant.</system>
From the following page content, extract:
1. All headings with their hierarchy levels
2. Key entities (people, places, organizations)
3. Important numerical data points

Output as structured JSON.

Page content:
{{ content }}`
  }
]

const VARIABLE_COLORS: Record<string, string> = {
  content: "bg-blue-500/20 text-blue-300",
  section_title: "bg-purple-500/20 text-purple-300",
  document_title: "bg-purple-500/20 text-purple-300",
  context: "bg-emerald-500/20 text-emerald-300",
  question: "bg-emerald-500/20 text-emerald-300",
  max_words: "bg-amber-500/20 text-amber-300",
  segment_count: "bg-orange-500/20 text-orange-300",
  segment_index: "bg-orange-500/20 text-orange-300",
  continuation_tail: "bg-cyan-500/20 text-cyan-300",
  exchange_range: "bg-orange-500/20 text-orange-300",
  target_minutes: "bg-orange-500/20 text-orange-300",
}

function highlightVariables(text: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  let remaining = text
  let key = 0

  // Collect all variable names found
  const matches: { name: string; index: number }[] = []
  const regex = /\{\{\s*(\w+)\s*\}\}/g
  let m: RegExpExecArray | null
  let pos = 0
  while ((m = regex.exec(remaining)) !== null) {
    matches.push({ name: m[1], index: pos + m.index })
    pos += m[0].length
  }

  if (matches.length === 0) {
    return text
  }

  let lastEnd = 0
  for (const { name, index } of matches) {
    if (index > lastEnd) {
      parts.push(remaining.slice(lastEnd, index))
    }
    const close = remaining.indexOf("}}", index)
    const fullMatch = remaining.slice(index, close + 2)
    const color = VARIABLE_COLORS[name] || "bg-gray-500/20 text-gray-300"
    parts.push(
      <span key={key++} className={color}>
        {fullMatch}
      </span>
    )
    lastEnd = close + 2
  }
  if (lastEnd < remaining.length) {
    parts.push(remaining.slice(lastEnd))
  }

  return parts
}

export default function PromptsPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="border-b border-gray-800 bg-gray-900/80 px-6 py-4">
        <div className="flex items-center gap-3">
          <FileText className="h-5 w-5 text-indigo-400" />
          <h1 className="text-lg font-semibold">System Prompts</h1>
        </div>
        <p className="mt-1 text-xs text-gray-400">
          All AI model prompts used by this instance. On-disk templates (.jinja) take precedence over hardcoded defaults.
        </p>
      </div>

      <div className="mx-auto max-w-4xl px-6 py-8 space-y-8">
        {PROMPTS.map((p) => (
          <section key={p.name} className="rounded-xl border border-gray-800 bg-gray-900/50 overflow-hidden">
            <div className="border-b border-gray-800 px-5 py-3 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-gray-100">{p.name}</h2>
                <p className="text-xs text-gray-400">{p.description}</p>
              </div>
              <span className="shrink-0 rounded bg-gray-800 px-2 py-0.5 font-mono text-[10px] text-gray-400">
                {p.file}
              </span>
            </div>
            <pre className="overflow-x-auto p-5 text-sm leading-relaxed text-gray-300 whitespace-pre-wrap break-words">
              {highlightVariables(p.template)}
            </pre>
          </section>
        ))}

        <div className="rounded-xl border border-gray-800 bg-gray-900/50 px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-2">Template Resolution</h3>
          <ol className="list-decimal list-inside space-y-1 text-xs text-gray-400">
            <li><strong className="text-gray-300">load_template(name)</strong> looks for <code className="bg-gray-800 px-1 py-0.5 rounded">prompts/&lt;name&gt;.jinja</code> on disk</li>
            <li>If the file exists, it is rendered with Jinja2 via <code className="bg-gray-800 px-1 py-0.5 rounded">render_jinja()</code></li>
            <li>If the file is missing, the hardcoded default from <code className="bg-gray-800 px-1 py-0.5 rounded">_get_default()</code> is used as fallback</li>
            <li>Summary endpoints call <code className="bg-gray-800 px-1 py-0.5 rounded">render_template()</code> which delegates to <code className="bg-gray-800 px-1 py-0.5 rounded">render_jinja()</code></li>
            <li>Podcast and flashcard endpoints call <code className="bg-gray-800 px-1 py-0.5 rounded">render_jinja()</code> directly with rich context (segmenting, continuation, etc.)</li>
          </ol>
        </div>
      </div>
    </div>
  )
}
