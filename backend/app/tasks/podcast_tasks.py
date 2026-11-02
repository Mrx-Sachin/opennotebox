"""Celery task for generating a two-host podcast dialogue (script + stitched audio)."""
import os
import re
import tempfile
import wave
from typing import List, Optional

import httpx
from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, Summary, ExtractedText, LibraryEntry, Podcast
from app.services.prompts import render_jinja
from app.services.json_extract import extract_json
from app.services.llm_settings import get_active_llm
from app.services.llm_semaphore import background_llm_semaphore
from app.config import settings
from app.tasks.shared import run_async
from app.services.library_access import (
    get_document_for_user,
    get_library_entry,
    visible_document_title,
)

SILENCE_SECONDS = 0.3
SPEAKER_VOICE = {"A": "kokoro-am_michael", "B": "kokoro-af_bella"}
CONTINUATION_TAIL_LINES = 3  # dialogue lines carried into the next segment's prompt

# length -> (per-segment exchange-count guidance, per-segment target minutes,
# source content char budget PER SEGMENT, number of sequential generation
# segments). A single LLM call writing an hour of dialogue is unreliable
# (slow under GPU contention, and one 100k-char slice can't cover a 600-page
# book anyway) so "deep_dive" is generated as several continuation-aware
# passes over successive chunks of the source material and stitched together.
LENGTH_CONFIG = {
    "short": {"exchange_range": "8 to 12", "target_minutes": "5", "char_budget": 60000, "segments": 1},
    "medium": {"exchange_range": "16 to 24", "target_minutes": "10", "char_budget": 60000, "segments": 1},
    "long": {"exchange_range": "30 to 45", "target_minutes": "20", "char_budget": 100000, "segments": 1},
    # Calibrated from a real run: measured ~12.3s of synthesized audio per
    # exchange (not the ~24s a naive "9 min in 20 exchanges" guess assumes),
    # so hitting an actual 35-40 min episode needs ~170-200 total exchanges.
    # Kept as 7 segments of ~25-30 rather than fewer/bigger segments so no
    # single generation call is asked to sustain more dialogue than the
    # "long" tier already does reliably.
    "deep_dive": {"exchange_range": "24 to 30", "target_minutes": "6", "char_budget": 70000, "segments": 7},
}


def _split_content_segments(content: str, n: int) -> List[str]:
    """Split gathered content into n roughly-equal chunks on page boundaries.

    Falls back to a straight character split if the content has no
    "[page N]" markers (e.g. a section podcast's single block of text).
    """
    if n <= 1:
        return [content]

    blocks = re.split(r"(?=\[page \d+\])", content)
    blocks = [b for b in blocks if b.strip()]
    if len(blocks) < n:
        # No usable page markers (or too few pages to fill every segment) -
        # split the raw text into n even-sized pieces instead.
        size = max(1, len(content) // n)
        return [content[i:i + size] for i in range(0, len(content), size)][:n] or [content]

    target = len(content) / n
    segments: List[str] = []
    buf = ""
    for block in blocks:
        if buf and len(buf) + len(block) > target and len(segments) < n - 1:
            segments.append(buf)
            buf = block
        else:
            buf += block
    segments.append(buf)
    return segments


def _gather_content(
    db: Session, doc_id: int, scope: str, section_title: Optional[str], user_id: int
) -> str:
    """Build the source material text for the dialogue prompt."""
    if scope == "section":
        section = db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "section",
            Summary.section_title == section_title,
            or_(
                Summary.user_id == user_id,
                (Summary.user_id.is_(None) & (Summary.content == "")),
            ),
        ).order_by(Summary.user_id.is_(None)).first()
        if section:
            # The section's `content` is its generated summary, which is an
            # EMPTY stub unless the user already generated it via the
            # Sections tab - that used to make section-scope podcasts fail
            # with "No content available". Pull the actual page text for the
            # section's page range instead (same source generate_single_
            # section_summary uses), falling back to the summary content
            # only if the section has no page range recorded.
            if section.start_page is not None or section.end_page is not None:
                query = db.query(ExtractedText).filter(ExtractedText.document_id == doc_id)
                if section.start_page is not None:
                    query = query.filter(ExtractedText.page_number >= section.start_page)
                if section.end_page is not None:
                    query = query.filter(ExtractedText.page_number <= section.end_page)
                pages = query.order_by(ExtractedText.page_number).all()
                if pages:
                    return "\n\n".join(f"[page {p.page_number}]\n{p.content}" for p in pages)
            return section.content if section.user_id == user_id else ""
        return ""

    summaries = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.mode == "page",
        Summary.user_id == user_id,
        Summary.content != "",
    ).order_by(Summary.page_number).all()

    if summaries:
        return "\n\n".join(f"[page {s.page_number}]\n{s.content}" for s in summaries)

    pages = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id
    ).order_by(ExtractedText.page_number).all()
    return "\n\n".join(f"[page {p.page_number}]\n{p.content}" for p in pages)


async def _generate_script(prompt: str, user_id: Optional[int] = None) -> str:
    active = get_active_llm(user_id)
    model = ChatOpenAI(
        base_url=active["base_url"],
        model=active["model"],
        api_key=active["api_key"],
        temperature=0.6,
        # Per-segment generation is capped at ~24 exchanges, but a single
        # GPU shared with chat/summary jobs can still be slow - keep this
        # well above normal generation time rather than risk a mid-episode
        # timeout on a deep_dive's later segments.
        timeout=480,
    )
    async with background_llm_semaphore():
        response = await model.ainvoke([
            SystemMessage(content="You produce strict JSON only, no prose, no markdown fences."),
            HumanMessage(content=prompt),
        ])
    return response.content


def _synthesize_line(text: str, voice: str) -> bytes:
    """Call the TTS service for a single dialogue line and return WAV bytes."""
    response = httpx.post(
        f"{settings.tts_url}/tts",
        json={"text": text[:2000], "voice": voice},
        timeout=180,
    )
    response.raise_for_status()
    return response.content


def _stitch_wavs(wav_bytes_list: List[bytes], output_path: str) -> tuple[float, List[dict]]:
    """Concatenate WAV clips with a short silence gap between them.

    Assumes all clips share the same sample format. Returns total duration in seconds and line timing metadata.
    """
    tmp_paths = []
    try:
        params = None
        frame_chunks = []
        clip_frame_counts = []

        for data in wav_bytes_list:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(data)
                tmp_paths.append(tmp.name)

            with wave.open(tmp_paths[-1], "rb") as wf:
                this_params = wf.getparams()
                if params is None:
                    params = this_params
                elif (this_params.nchannels, this_params.sampwidth, this_params.framerate) != \
                     (params.nchannels, params.sampwidth, params.framerate):
                    logger.warning(
                        "Podcast line audio params differ from first clip "
                        f"({this_params} vs {params}); stitching anyway"
                    )
                clip_frames = wf.getnframes()
                frame_chunks.append(wf.readframes(clip_frames))
                clip_frame_counts.append(clip_frames)

        if params is None:
            raise ValueError("No audio clips to stitch")

        silence_frames = int(SILENCE_SECONDS * params.framerate)
        silence_bytes = b"\x00" * (silence_frames * params.sampwidth * params.nchannels)

        with wave.open(output_path, "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            for i, chunk in enumerate(frame_chunks):
                out.writeframes(chunk)
                if i < len(frame_chunks) - 1:
                    out.writeframes(silence_bytes)

        with wave.open(output_path, "rb") as out:
            total_frames = out.getnframes()
            total_duration = total_frames / float(out.getframerate())

        line_timings: List[dict] = []
        previous_end_time = 0.0
        for i, clip_frames in enumerate(clip_frame_counts):
            clip_duration = clip_frames / float(params.framerate)
            if i == 0:
                start_time = 0.0
            else:
                start_time = previous_end_time + SILENCE_SECONDS
            end_time = start_time + clip_duration
            line_timings.append({
                "start_time": start_time,
                "end_time": end_time,
                "duration": clip_duration,
            })
            previous_end_time = end_time

        return total_duration, line_timings
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def _clean_lines(raw_lines: list) -> List[dict]:
    cleaned = []
    for line in raw_lines:
        speaker = str(line.get("speaker", "A")).strip().upper()
        if speaker not in SPEAKER_VOICE:
            speaker = "A"
        text = (line.get("text") or "").strip()
        if text:
            cleaned.append({"speaker": speaker, "text": text})
    return cleaned


def _generate_dialogue_segments(
    doc_title: str,
    section_title: Optional[str],
    content: str,
    length_cfg: dict,
    user_id: Optional[int] = None,
) -> tuple[str, Optional[str], List[dict]]:
    """Generate the full dialogue, in one or several continuation-aware passes.

    Returns (title, scratchpad_for_qa, all_cleaned_lines).
    """
    segment_count = length_cfg["segments"]
    chunks = _split_content_segments(content[: length_cfg["char_budget"] * segment_count], segment_count)

    title: Optional[str] = None
    scratchpads: List[str] = []
    all_lines: List[dict] = []
    tail = ""

    for i, chunk in enumerate(chunks, start=1):
        prompt = render_jinja(
            "podcast/dialogue",
            document_title=doc_title,
            section_title=section_title,
            content=chunk,
            exchange_range=length_cfg["exchange_range"],
            target_minutes=length_cfg["target_minutes"],
            segment_index=i,
            segment_count=segment_count,
            continuation_tail=tail,
        )
        raw = run_async(_generate_script(prompt, user_id))
        script = extract_json(raw)
        if not isinstance(script, dict) or "lines" not in script:
            raise ValueError(f"Dialogue response for segment {i}/{segment_count} had no 'lines' array")

        segment_lines = _clean_lines(script.get("lines") or [])
        if not segment_lines:
            raise ValueError(f"Dialogue segment {i}/{segment_count} had no usable lines")

        if title is None:
            title = script.get("title") or None
        if script.get("scratchpad"):
            scratchpads.append(f"[segment {i}/{segment_count}] {script['scratchpad']}")

        all_lines.extend(segment_lines)
        tail = "\n".join(
            f"{line['speaker']}: {line['text']}" for line in segment_lines[-CONTINUATION_TAIL_LINES:]
        )

    return title, ("\n\n".join(scratchpads) or None), all_lines


@celery_app.task(bind=True, max_retries=1)
def generate_podcast(self, podcast_id: int) -> dict:
    """Generate a two-host podcast dialogue script and its stitched audio."""
    db: Session = SessionLocal()
    output_path: Optional[str] = None
    try:
        podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
        if not podcast:
            raise ValueError(f"Podcast {podcast_id} not found")
        if (
            podcast.status == "completed"
            and podcast.audio_path
            and os.path.exists(podcast.audio_path)
        ):
            return {
                "podcast_id": podcast_id,
                "status": "completed",
                "duration": podcast.duration or 0.0,
            }

        requesting_user_id = podcast.user_id
        document_id = podcast.document_id

        doc = get_document_for_user(db, requesting_user_id, document_id)
        if not doc:
            raise ValueError(f"Document {document_id} not found for user {requesting_user_id}")

        length_cfg = LENGTH_CONFIG.get(podcast.length or "medium", LENGTH_CONFIG["medium"])

        content = _gather_content(
            db, doc.id, podcast.scope, podcast.section_title, requesting_user_id
        )
        if not content.strip():
            raise ValueError("No content available to generate a podcast from")

        document_title = visible_document_title(
            doc, get_library_entry(db, requesting_user_id, document_id)
        )
        title, scratchpad, cleaned_lines = _generate_dialogue_segments(
            document_title, podcast.section_title, content, length_cfg, requesting_user_id
        )

        db.rollback()
        membership = db.query(LibraryEntry.id).filter(
            LibraryEntry.user_id == requesting_user_id,
            LibraryEntry.document_id == document_id,
        ).first()
        if not membership:
            current_podcast = db.query(Podcast).filter(
                Podcast.id == podcast_id,
                Podcast.user_id == requesting_user_id,
            ).first()
            if current_podcast:
                current_podcast.status = "failed"
                current_podcast.error = "Requesting user no longer has library access"
                db.commit()
            return {"podcast_id": podcast_id, "status": "not_accessible"}

        wav_clips = []
        for line in cleaned_lines:
            voice = SPEAKER_VOICE[line["speaker"]]
            wav_clips.append(_synthesize_line(line["text"], voice))

        db.commit()
        db.expunge_all()
        membership = db.query(LibraryEntry.id).filter(
            LibraryEntry.user_id == requesting_user_id,
            LibraryEntry.document_id == document_id,
        ).with_for_update().first()
        if not membership:
            current_podcast = db.query(Podcast).filter(
                Podcast.id == podcast_id,
                Podcast.user_id == requesting_user_id,
            ).first()
            if current_podcast:
                current_podcast.status = "failed"
                current_podcast.error = "Requesting user no longer has library access"
                db.commit()
            return {"podcast_id": podcast_id, "status": "not_accessible"}

        podcast = db.query(Podcast).filter(
            Podcast.id == podcast_id,
            Podcast.user_id == requesting_user_id,
        ).populate_existing().with_for_update().first()
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not podcast or not doc:
            db.rollback()
            return {"podcast_id": podcast_id, "status": "not_accessible"}

        if podcast.status == "completed" and podcast.audio_path and os.path.exists(podcast.audio_path):
            db.rollback()
            return {
                "podcast_id": podcast_id,
                "status": "completed",
                "duration": podcast.duration or 0.0,
            }

        output_dir = os.path.join(settings.data_dir, "podcasts")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{podcast_id}.wav")

        stitch_result = _stitch_wavs(wav_clips, output_path)
        if isinstance(stitch_result, tuple):
            duration, line_timings = stitch_result
        else:
            duration, line_timings = stitch_result, []

        enriched_lines = [
            {
                "speaker": l["speaker"],
                "text": l["text"],
                "start_time": round(t["start_time"], 3),
                "end_time": round(t["end_time"], 3),
                "duration": round(t["duration"], 3),
            }
            for l, t in zip(cleaned_lines, line_timings)
        ] if line_timings else cleaned_lines

        podcast.status = "completed"
        podcast.audio_path = output_path
        podcast.duration = duration
        podcast.transcript = {
            "version": 2,
            "title": title or document_title,
            "lines": enriched_lines,
            # Kept for QA on dialogue quality, not shown in the player UI.
            "scratchpad": scratchpad,
        }
        podcast.error = None
        db.commit()

        logger.info(f"Podcast {podcast_id} generated: {len(cleaned_lines)} lines, {duration:.1f}s")
        return {"podcast_id": podcast_id, "status": "completed", "duration": duration}

    except Exception as e:
        logger.error(f"Podcast generation failed for {podcast_id}: {e}")
        db.rollback()
        db.expunge_all()
        try:
            podcast = (
                db.query(Podcast)
                .filter(Podcast.id == podcast_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            completed_ref = (
                db.query(Podcast.id)
                .filter(
                    Podcast.audio_path == output_path,
                    Podcast.status == "completed",
                )
                .first()
                if output_path
                else None
            )
            if not completed_ref and output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            if podcast and podcast.status != "completed":
                podcast.status = "failed"
                podcast.error = str(e)[:2000]
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
        return {"podcast_id": podcast_id, "status": "failed", "error": str(e)}
    finally:
        db.close()
