"""Persistent in-memory Piper voice pool for low-latency synthesis.

Eliminates CLI subprocess overhead by keeping PiperVoice models loaded in memory
and serving synthesis requests without disk tempfile thrashing.
"""
from __future__ import annotations

import io
import logging
import threading
import wave
from typing import Dict, Optional
from piper import PiperVoice
from piper.config import SynthesisConfig

logger = logging.getLogger(__name__)


class ClientGoneError(Exception):
    """Raised when the client disconnects or abandons synthesis."""
    pass


_voice_cache: Dict[str, PiperVoice] = {}
_cache_lock = threading.Lock()
_synthesis_lock = threading.Lock()


def get_piper_voice(model_path: str) -> PiperVoice:
    """Load or retrieve a cached PiperVoice model instance."""
    with _cache_lock:
        if model_path not in _voice_cache:
            _voice_cache[model_path] = PiperVoice.load(model_path)
        return _voice_cache[model_path]


def synthesize_piper_in_memory(
    text: str,
    model_path: str,
    speed: float = 1.0,
    sentence_silence: float = 0.1,
    cancelled: Optional[threading.Event] = None,
) -> bytes:
    """Synthesize text using in-memory PiperVoice and return WAV bytes.

    Thread-safe and generates WAV format directly in memory without tempfiles.
    Cooperatively checks the `cancelled` event while waiting for the synthesis
    lock and between sentence chunks so abandoned requests do not perform
    unnecessary inference.
    Preserves original sentence_silence (0.1s default pause between sentences).
    """
    if cancelled is not None and cancelled.is_set():
        raise ClientGoneError("Client disconnected before synthesis started")

    voice = get_piper_voice(model_path)
    length_scale = 1.0 / max(speed, 0.1) if abs(speed - 1.0) > 0.01 else 1.0
    syn_config = SynthesisConfig(
        length_scale=length_scale,
    )

    # Acquire synthesis lock cooperatively; bail out if request was canceled
    while not _synthesis_lock.acquire(timeout=0.1):
        if cancelled is not None and cancelled.is_set():
            raise ClientGoneError("Client disconnected while waiting for synthesis lock")

    try:
        if cancelled is not None and cancelled.is_set():
            raise ClientGoneError("Client disconnected before synthesis")

        chunks = []
        for chunk in voice.synthesize(text, syn_config=syn_config):
            if cancelled is not None and cancelled.is_set():
                raise ClientGoneError("Client disconnected during synthesis")
            chunks.append(chunk)

        silence_bytes = (
            bytes(int(voice.config.sample_rate * sentence_silence * 2))
            if sentence_silence > 0
            else b""
        )
        pcm_chunks = []
        for i, chunk in enumerate(chunks):
            if i > 0 and silence_bytes:
                pcm_chunks.append(silence_bytes)
            pcm_chunks.append(chunk.audio_int16_bytes)

        raw_pcm = b"".join(pcm_chunks)
    finally:
        _synthesis_lock.release()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(voice.config.sample_rate)
        wav_file.writeframes(raw_pcm)

    return buf.getvalue()
