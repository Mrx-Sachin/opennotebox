"""Authoritative catalog and validation for TTS voices."""
from typing import Literal, TypeGuard, get_args

KokoroVoiceId = Literal[
    "kokoro-af_heart",
    "kokoro-af_bella",
    "kokoro-am_michael",
    "kokoro-am_fenrir",
    "kokoro-bf_emma",
    "kokoro-bm_george",
]

DEFAULT_VOICE: KokoroVoiceId = "kokoro-af_heart"
KOKORO_VOICE_IDS: tuple[str, ...] = get_args(KokoroVoiceId)

VOICE_CATALOG: list[dict[str, str]] = [
    {
        "id": "kokoro-af_heart",
        "label": "Heart (female, US, Kokoro)",
        "description": "Top-graded Kokoro voice. Natural, expressive.",
    },
    {
        "id": "kokoro-af_bella",
        "label": "Bella (female, US, Kokoro)",
        "description": "Natural, expressive.",
    },
    {
        "id": "kokoro-am_michael",
        "label": "Michael (male, US, Kokoro)",
        "description": "Natural, expressive.",
    },
    {
        "id": "kokoro-am_fenrir",
        "label": "Fenrir (male, US, Kokoro)",
        "description": "Natural, expressive.",
    },
    {
        "id": "kokoro-bf_emma",
        "label": "Emma (female, British, Kokoro)",
        "description": "Natural, expressive.",
    },
    {
        "id": "kokoro-bm_george",
        "label": "George (male, British, Kokoro)",
        "description": "Natural, expressive.",
    },
]


def is_valid_voice(voice: object) -> TypeGuard[KokoroVoiceId]:
    """Return True if the given voice identifier belongs to the Kokoro catalog."""
    return isinstance(voice, str) and voice in KOKORO_VOICE_IDS


def normalize_voice(voice: object) -> KokoroVoiceId:
    """Normalize legacy, invalid, or missing voices to the default Kokoro voice."""
    if is_valid_voice(voice):
        return voice
    return DEFAULT_VOICE
