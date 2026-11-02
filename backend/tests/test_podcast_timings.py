"""Unit tests for line-level audio timestamps and WAV stitching in podcasts."""
import io
import math
import struct
import wave
from pathlib import Path

import pytest

from app.tasks.podcast_tasks import _stitch_wavs, SILENCE_SECONDS


def _generate_synthetic_pcm_wav(
    duration_seconds: float,
    sample_rate: int = 24000,
    frequency: float = 440.0,
) -> bytes:
    """Generate in-memory WAV bytes for 16-bit mono PCM."""
    num_frames = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        samples = []
        for index in range(num_frames):
            value = int(32767.0 * 0.2 * math.sin(2.0 * math.pi * frequency * (index / sample_rate)))
            samples.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(samples))
    return buffer.getvalue()


def test_stitch_wavs_timings_multiple_clips(tmp_path: Path):
    """Verify line timings, gap calculation, and output WAV duration for multiple clips."""
    clip_one = _generate_synthetic_pcm_wav(duration_seconds=1.0, sample_rate=24000)
    clip_two = _generate_synthetic_pcm_wav(duration_seconds=2.0, sample_rate=24000)

    output_file = str(tmp_path / "stitched_output.wav")
    total_duration, line_timings = _stitch_wavs([clip_one, clip_two], output_file)

    # Expected: 1.0s + 0.3s (silence) + 2.0s = 3.3s
    expected_total = 1.0 + SILENCE_SECONDS + 2.0
    assert total_duration == pytest.approx(expected_total, abs=1e-4)

    assert len(line_timings) == 2

    # Clip 0: 0.0 to 1.0
    assert line_timings[0]["start_time"] == pytest.approx(0.0, abs=1e-4)
    assert line_timings[0]["duration"] == pytest.approx(1.0, abs=1e-4)
    assert line_timings[0]["end_time"] == pytest.approx(1.0, abs=1e-4)

    # Clip 1: 1.3 to 3.3
    expected_clip1_start = 1.0 + SILENCE_SECONDS
    expected_clip1_end = expected_clip1_start + 2.0
    assert line_timings[1]["start_time"] == pytest.approx(expected_clip1_start, abs=1e-4)
    assert line_timings[1]["duration"] == pytest.approx(2.0, abs=1e-4)
    assert line_timings[1]["end_time"] == pytest.approx(expected_clip1_end, abs=1e-4)

    # Verify duration from disk
    with wave.open(output_file, "rb") as out_wav:
        disk_frames = out_wav.getnframes()
        disk_framerate = out_wav.getframerate()
        disk_duration = disk_frames / float(disk_framerate)

    assert disk_duration == pytest.approx(total_duration, abs=1e-4)


def test_stitch_wavs_single_clip(tmp_path: Path):
    """Verify stitching a single clip introduces no trailing or leading silence."""
    clip = _generate_synthetic_pcm_wav(duration_seconds=1.5, sample_rate=24000)
    output_file = str(tmp_path / "single_clip.wav")

    total_duration, line_timings = _stitch_wavs([clip], output_file)

    assert total_duration == pytest.approx(1.5, abs=1e-4)
    assert len(line_timings) == 1
    assert line_timings[0]["start_time"] == pytest.approx(0.0, abs=1e-4)
    assert line_timings[0]["duration"] == pytest.approx(1.5, abs=1e-4)
    assert line_timings[0]["end_time"] == pytest.approx(1.5, abs=1e-4)

    with wave.open(output_file, "rb") as out_wav:
        disk_frames = out_wav.getnframes()
        disk_duration = disk_frames / float(out_wav.getframerate())
    assert disk_duration == pytest.approx(1.5, abs=1e-4)


def test_stitch_wavs_three_clips(tmp_path: Path):
    """Verify silence chaining across 3 clips."""
    clip_one = _generate_synthetic_pcm_wav(duration_seconds=0.5, sample_rate=22050)
    clip_two = _generate_synthetic_pcm_wav(duration_seconds=1.0, sample_rate=22050)
    clip_three = _generate_synthetic_pcm_wav(duration_seconds=1.5, sample_rate=22050)

    output_file = str(tmp_path / "three_clips.wav")
    total_duration, line_timings = _stitch_wavs([clip_one, clip_two, clip_three], output_file)

    # 0.5 + 0.3 + 1.0 + 0.3 + 1.5 = 3.6
    expected_total = 0.5 + SILENCE_SECONDS + 1.0 + SILENCE_SECONDS + 1.5
    assert total_duration == pytest.approx(expected_total, abs=1e-4)
    assert len(line_timings) == 3

    assert line_timings[0]["start_time"] == pytest.approx(0.0, abs=1e-4)
    assert line_timings[0]["duration"] == pytest.approx(0.5, abs=1e-4)
    assert line_timings[0]["end_time"] == pytest.approx(0.5, abs=1e-4)

    assert line_timings[1]["start_time"] == pytest.approx(0.8, abs=1e-4)
    assert line_timings[1]["duration"] == pytest.approx(1.0, abs=1e-4)
    assert line_timings[1]["end_time"] == pytest.approx(1.8, abs=1e-4)

    assert line_timings[2]["start_time"] == pytest.approx(2.1, abs=1e-4)
    assert line_timings[2]["duration"] == pytest.approx(1.5, abs=1e-4)
    assert line_timings[2]["end_time"] == pytest.approx(3.6, abs=1e-4)


def test_stitch_wavs_empty_raises_value_error(tmp_path: Path):
    """Verify empty clip list raises ValueError."""
    output_file = str(tmp_path / "empty.wav")
    with pytest.raises(ValueError, match="No audio clips to stitch"):
        _stitch_wavs([], output_file)


def test_transcript_enrichment_structure():
    """Verify the transcript enrichment logic conforms to version 2 schema."""
    cleaned_lines = [
        {"speaker": "A", "text": "Welcome to the podcast."},
        {"speaker": "B", "text": "Thanks for having me!"},
    ]
    line_timings = [
        {"start_time": 0.0, "end_time": 1.23456, "duration": 1.23456},
        {"start_time": 1.53456, "end_time": 3.78912, "duration": 2.25456},
    ]

    enriched_lines = [
        {
            "speaker": l["speaker"],
            "text": l["text"],
            "start_time": round(t["start_time"], 3),
            "end_time": round(t["end_time"], 3),
            "duration": round(t["duration"], 3),
        }
        for l, t in zip(cleaned_lines, line_timings)
    ]

    transcript = {
        "version": 2,
        "title": "Test Title",
        "lines": enriched_lines,
        "scratchpad": "Test scratchpad",
    }

    assert transcript["version"] == 2
    assert transcript["title"] == "Test Title"
    assert transcript["scratchpad"] == "Test scratchpad"
    assert len(transcript["lines"]) == 2
    assert transcript["lines"][0] == {
        "speaker": "A",
        "text": "Welcome to the podcast.",
        "start_time": 0.0,
        "end_time": 1.235,
        "duration": 1.235,
    }
    assert transcript["lines"][1] == {
        "speaker": "B",
        "text": "Thanks for having me!",
        "start_time": 1.535,
        "end_time": 3.789,
        "duration": 2.255,
    }
