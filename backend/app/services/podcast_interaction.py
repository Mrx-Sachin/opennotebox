"""Service for handling podcast interruptions, ASR transcription, and Q&A audio synthesis."""
import asyncio
import json
import os
import re
from typing import List, Optional, Tuple
import uuid

from fastapi import HTTPException
import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Podcast
from app.schemas import PodcastInterruptResponse
from app.services import retrieval
from app.services.json_extract import extract_json
from app.services.llm_semaphore import llm_semaphore
from app.services.llm_settings import get_active_llm
from app.services.prompts import render_jinja
from app.tasks.podcast_tasks import SPEAKER_VOICE, _stitch_wavs


def resolve_active_exchange(
    transcript: Optional[dict],
    current_time: float,
    line_index: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[dict], int]:
    """Resolve the active and upcoming dialogue lines based on playback time or index.

    Args:
        transcript: The podcast transcript containing lines and optional timing.
        current_time: Current playback timestamp in seconds.
        line_index: Optional explicit index override.

    Returns:
        A tuple of (current_line, next_line, line_index).
    """
    if not transcript or not transcript.get("lines"):
        return None, None, 0

    lines = transcript.get("lines", [])
    if not lines:
        return None, None, 0

    # Explicit line_index override takes precedence if valid.
    if line_index is not None and 0 <= line_index < len(lines):
        next_line = lines[line_index + 1] if line_index + 1 < len(lines) else None
        return lines[line_index], next_line, line_index

    # Check if lines have start_time and end_time timestamps.
    has_timestamps = all(
        isinstance(line_item.get("start_time"), (int, float))
        and isinstance(line_item.get("end_time"), (int, float))
        for line_item in lines
    )

    if has_timestamps:
        if current_time < lines[0]["start_time"]:
            return None, lines[0], 0

        for i, line in enumerate(lines):
            start = line["start_time"]
            end = line["end_time"]
            if start <= current_time <= end:
                next_line = lines[i + 1] if i + 1 < len(lines) else None
                return line, next_line, i

            if i < len(lines) - 1:
                next_start = lines[i + 1]["start_time"]
                if end < current_time < next_start:
                    return line, lines[i + 1], i

        if current_time > lines[-1]["end_time"]:
            return lines[-1], None, len(lines) - 1

        return lines[-1], None, len(lines) - 1

    # Legacy fallback without timestamps: character-weighted proportional estimation.
    lengths = [max(len(str(line_item.get("text", ""))), 1) for line_item in lines]
    total_chars = sum(lengths) or 1
    total_duration = None
    if isinstance(transcript.get("duration"), (int, float)) and transcript["duration"] > 0:
        total_duration = float(transcript["duration"])
    else:
        total_duration = max(total_chars / 15.0, 1.0)

    if current_time < 0.0:
        return None, lines[0], 0

    elapsed = 0.0
    for i, (line, length) in enumerate(zip(lines, lengths)):
        line_dur = (length / total_chars) * total_duration
        start = elapsed
        end = elapsed + line_dur
        elapsed = end
        if start <= current_time <= end:
            next_line = lines[i + 1] if i + 1 < len(lines) else None
            return line, next_line, i

    return lines[-1], None, len(lines) - 1


async def retrieve_podcast_context(
    db: Session,
    doc_id: int,
    question: str,
    document_title: str = "",
    user_id: Optional[int] = None,
    max_chars: int = 4000,
    current_line: Optional[dict] = None,
) -> Tuple[str, List[dict]]:
    """Retrieve relevant chunks from the source document fast without blocking LLM rewrites.

    Args:
        db: Database session.
        doc_id: Document ID.
        question: User query.
        max_chars: Maximum total characters for context block.
        current_line: Optional dialogue line being spoken when paused.

    Returns:
        Tuple of (context_string, citations_list).
    """
    clean_q = re.sub(r"[^\w\s]", " ", question).strip()
    words = [
        w for w in clean_q.split()
        if len(w) > 3 and w.lower() not in (
            "this", "that", "with", "from", "have", "what", "were", "about", "your", "they", "there", "their", "idea"
        )
    ]
    search_queries = [question]
    if len(words) >= 2:
        search_queries.append(" ".join(words[:4]))
    if current_line and current_line.get("text"):
        # Contextual search using the active turn
        search_queries.append(current_line["text"][:100])

    hits = retrieval.search(db, [doc_id], search_queries, top_k=6)
    formatted_chunks: List[str] = []
    citations: List[dict] = []
    total_chars = 0

    for hit in hits:
        page = hit.get("page")
        text = (hit.get("text") or "").strip()
        page_prefix = f"[page {page}] " if page is not None else ""
        chunk_str = f"{page_prefix}{text}"

        if total_chars + len(chunk_str) > max_chars and formatted_chunks:
            break

        formatted_chunks.append(chunk_str)
        total_chars += len(chunk_str) + 2
        citations.append({
            "chunk_id": hit.get("chunk_id"),
            "doc_id": hit.get("doc_id", doc_id),
            "page": page,
            "text": text[:300],
        })

    context = "\n\n".join(formatted_chunks)
    return context, citations


async def generate_interruption_dialogue(
    document_title: str,
    user_question: str,
    context: str,
    current_line: Optional[dict],
    next_line: Optional[dict],
    user_id: Optional[int] = None,
    history: Optional[List[dict]] = None,
) -> List[dict]:
    """Generate interruption dialogue exchanges between hosts answering the listener.

    Args:
        document_title: Title of the document or podcast topic.
        user_question: Question asked by the user.
        context: Retrieved context passages.
        current_line: Line being spoken when paused.
        next_line: Upcoming line after the pause.
        user_id: Optional user ID for custom LLM configuration.

    Returns:
        List of dialogue lines with speaker and text.
    """
    prompt = render_jinja(
        "podcast/interrupt",
        document_title=document_title,
        user_question=user_question,
        context=context,
        current_line=current_line,
        next_line=next_line,
        history=history,
    )

    async with llm_semaphore():
        active = get_active_llm(user_id)
        model = ChatOpenAI(
            base_url=active["base_url"],
            model=active["model"],
            api_key=active["api_key"] or "not-needed",
            timeout=45,
            max_tokens=1500,
        )
        response = await model.ainvoke([HumanMessage(content=prompt)])
        raw_content = response.content if hasattr(response, "content") else str(response)

    data: Any = {}
    try:
        data = extract_json(raw_content)
    except Exception as e:
        logger.warning(
            f"Strict JSON extraction failed on interruption dialogue ({e}); "
            "attempting regex recovery on completed lines"
        )

    cleaned_lines: List[dict] = []
    raw_lines = data.get("lines", []) if isinstance(data, dict) else []
    for line_dict in raw_lines:
        if not isinstance(line_dict, dict):
            continue
        speaker = str(line_dict.get("speaker", "A")).strip().upper()
        if speaker not in ("A", "B"):
            speaker = "A"
        text = str(line_dict.get("text", "")).strip()
        if text:
            cleaned_lines.append({"speaker": speaker, "text": text})

    # If strict JSON didn't yield lines, salvage any completed speaker lines from raw_content
    if not cleaned_lines and raw_content:
        line_matches = re.finditer(
            r'\{\s*"speaker"\s*:\s*"([ABab])"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
            raw_content,
        )
        for m in line_matches:
            spk = m.group(1).upper()
            try:
                txt = m.group(2).encode("utf-8").decode("unicode_escape").strip()
            except Exception:
                txt = m.group(2).strip()
            if txt:
                cleaned_lines.append({"speaker": spk, "text": txt})

    if not cleaned_lines:
        cleaned_lines = [
            {
                "speaker": "A",
                "text": "That is an interesting question about what we are discussing.",
            },
            {
                "speaker": "B",
                "text": "Let us dive right back into where we left off.",
            },
        ]

    return cleaned_lines


async def synthesize_interruption_audio(
    lines: List[dict],
    interrupt_id: str,
) -> Tuple[str, float]:
    """Synthesize speech audio for the generated interruption dialogue lines.

    Args:
        lines: List of dialogue lines.
        interrupt_id: Unique identifier for the interruption.

    Returns:
        Tuple of (output_file_path, duration_seconds).
    """
    output_dir = os.path.join(settings.data_dir, "podcast_interrupts")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{interrupt_id}.wav")

    async def _synth_single_line(client: httpx.AsyncClient, line: dict) -> bytes:
        speaker = line.get("speaker", "A")
        voice = SPEAKER_VOICE.get(speaker, SPEAKER_VOICE["A"])
        resp = await client.post(
            f"{settings.tts_url}/tts",
            json={"text": line["text"][:1000], "voice": voice},
        )
        if resp.status_code != 200:
            logger.error(
                f"TTS synthesis failed with status {resp.status_code}: {resp.text}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"TTS synthesis failed: {resp.text}",
            )
        return resp.content

    async with httpx.AsyncClient(timeout=30) as client:
        wav_bytes_list = await asyncio.gather(*[_synth_single_line(client, line) for line in lines])

    if not wav_bytes_list:
        raise ValueError("No audio clips to synthesize")

    stitch_result = _stitch_wavs(wav_bytes_list, output_path)
    if isinstance(stitch_result, tuple):
        duration = stitch_result[0]
    else:
        duration = float(stitch_result)

    return output_path, duration


async def transcribe_question(
    audio_bytes: bytes,
    filename: str = "question.wav",
) -> str:
    """Transcribe spoken question audio using Meta Model API ASR.

    Args:
        audio_bytes: Raw audio byte payload.
        filename: Optional filename for multipart upload.

    Returns:
        Transcribed question text.
    """
    if settings.effective_meta_api_key:
        url = settings.meta_asr_url
        headers = {"Authorization": f"Bearer {settings.effective_meta_api_key}"}
        files = {
            "request": (
                None,
                json.dumps({
                    "mode": "PUSH_TO_TALK",
                    "model": settings.meta_asr_model,
                    "audioEncoding": "WAV",
                }),
                "application/json",
            ),
            "audio": (filename, audio_bytes, "audio/wav"),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, files=files)
            if resp.status_code >= 400:
                logger.error(f"Meta ASR error {resp.status_code}: {resp.text}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"ASR service error: {resp.text}",
                )
            data = resp.json()
            return data.get("transcript", "").strip()
    else:
        raise HTTPException(
            status_code=503,
            detail="Meta Model API key not configured",
        )


async def process_interruption(
    db: Session,
    podcast: Podcast,
    user_id: int,
    question: str,
    current_time: float,
    line_index: Optional[int] = None,
    history: Optional[List[dict]] = None,
) -> PodcastInterruptResponse:
    """Orchestrate dialogue generation, audio synthesis, and response preparation.

    Args:
        db: Database session.
        podcast: Target podcast instance.
        user_id: Requesting user ID.
        question: User query text.
        current_time: Active playback time in seconds.
        line_index: Optional line index override.

    Returns:
        PodcastInterruptResponse populated with response metadata and audio URL.
    """
    current_line, next_line, idx = resolve_active_exchange(
        podcast.transcript, current_time, line_index
    )
    doc_title = (podcast.transcript or {}).get("title", f"Podcast {podcast.id}")
    context, citations = await retrieve_podcast_context(
        db,
        podcast.document_id,
        question,
        document_title=doc_title,
        user_id=user_id,
        current_line=current_line,
    )

    doc_title = (
        podcast.transcript.get("title", f"Podcast {podcast.id}")
        if podcast.transcript and isinstance(podcast.transcript, dict)
        else f"Podcast {podcast.id}"
    )

    lines = await generate_interruption_dialogue(
        document_title=doc_title,
        user_question=question,
        context=context,
        current_line=current_line,
        next_line=next_line,
        user_id=user_id,
        history=history,
    )

    interrupt_id = uuid.uuid4().hex
    output_path, duration = await synthesize_interruption_audio(lines, interrupt_id)

    # Rewind to the start of the interrupted sentence for natural conversational resumption!
    natural_resume_time = current_time
    if current_line and current_line.get("start_time") is not None:
        natural_resume_time = float(current_line["start_time"])
    else:
        natural_resume_time = max(0.0, current_time - 3.0)

    return PodcastInterruptResponse(
        interrupt_id=interrupt_id,
        audio_url=f"/api/podcasts/{podcast.id}/interrupts/{interrupt_id}/audio",
        duration=duration,
        lines=lines,
        current_line=current_line,
        next_line=next_line,
        resume_time=round(natural_resume_time, 3),
        citations=citations,
    )
