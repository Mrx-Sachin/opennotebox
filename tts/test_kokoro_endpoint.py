import asyncio
import os
import sys
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(__file__))

from main import KOKORO_VOICES, ClientGoneError, TTSTextRequest, app, synthesize


class FakeRequest:
    def __init__(self, disconnected=False):
        self._disconnected = disconnected

    async def is_disconnected(self):
        return self._disconnected


class TestKokoroEndpoint(unittest.IsolatedAsyncioTestCase):

    async def test_accepted_kokoro_voices_synthesize_wav(self):
        fake_http = FakeRequest(disconnected=False)
        fake_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

        for voice_id in KOKORO_VOICES:
            req = TTSTextRequest(text="Hello world.", voice=voice_id)
            with patch("main.synthesize_kokoro", return_value=fake_wav) as mock_synth:
                response = await synthesize(req, fake_http)
                self.assertEqual(response.media_type, "audio/wav")
                self.assertEqual(response.body, fake_wav)
                mock_synth.assert_called_once()
                args, _ = mock_synth.call_args
                self.assertEqual(args[0], "Hello world.")
                self.assertEqual(args[1], voice_id)

    async def test_rejected_piper_and_unknown_voices_fail_validation(self):
        unsupported_voices = [
            "en_US-lessac-medium",
            "en_US-lessac-high",
            "en_US-ryan-medium",
            "en_US-ryan-high",
            "en_US-libritts_r-medium",
            "default",
            "host_a",
            "host_b",
            "random-voice",
        ]

        for voice in unsupported_voices:
            with self.assertRaises(ValidationError):
                TTSTextRequest(text="Test rejected voice", voice=voice)

        with patch("main.synthesize_kokoro") as mock_synth:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                for voice in unsupported_voices:
                    response = await client.post("/tts", json={"text": "Removed voice", "voice": voice})
                    self.assertEqual(response.status_code, 422)
            mock_synth.assert_not_called()

    async def test_timeout_yields_http_504(self):
        req = TTSTextRequest(text="Timeout test sentence.", voice="kokoro-af_heart")
        fake_http = FakeRequest(disconnected=False)

        orig_wait_for = asyncio.wait_for

        async def short_wait_for(fut, timeout=None):
            return await orig_wait_for(fut, timeout=0.05)

        slow_duration = 0.30

        def slow_synth(*args, **kwargs):
            time.sleep(slow_duration)
            return b"RIFFfake_wav"

        with (
            patch("main.synthesize_kokoro", side_effect=slow_synth),
            patch("main.asyncio.wait_for", side_effect=short_wait_for),
        ):
                t0 = time.perf_counter()
                with self.assertRaises(HTTPException) as ctx:
                    await synthesize(req, fake_http)
                elapsed = time.perf_counter() - t0

                self.assertEqual(ctx.exception.status_code, 504)
                self.assertIn("timed out", ctx.exception.detail.lower())
                self.assertLess(elapsed, slow_duration * 0.7)

    async def test_queued_cancellation_yields_499_no_extra_synthesis(self):
        req = TTSTextRequest(text="Cancellation test sentence.", voice="kokoro-af_heart")
        fake_http = FakeRequest(disconnected=True)

        call_count = 0

        def cancelling_synth(text, voice, speed=1.0, cancelled=None):
            nonlocal call_count
            call_count += 1
            for _ in range(50):
                if cancelled and cancelled.is_set():
                    raise ClientGoneError("Client disconnected")
                time.sleep(0.01)
            return b"RIFFfake_wav"

        with patch("main.synthesize_kokoro", side_effect=cancelling_synth):
            with self.assertRaises(HTTPException) as ctx:
                await synthesize(req, fake_http)

            self.assertEqual(ctx.exception.status_code, 499)
            self.assertEqual(call_count, 1)

    async def test_number_normalization_before_synthesis(self):
        req = TTSTextRequest(text="There are 10,000,000 items and 1 0 0 boxes.", voice="kokoro-af_heart")
        fake_http = FakeRequest(disconnected=False)

        with patch("main.synthesize_kokoro", return_value=b"RIFFwav") as mock_synth:
            await synthesize(req, fake_http)
            mock_synth.assert_called_once()
            args, _ = mock_synth.call_args
            self.assertEqual(args[0], "There are 10000000 items and 100 boxes.")


if __name__ == "__main__":
    unittest.main()
