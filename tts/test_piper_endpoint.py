import asyncio
import threading
import time
import unittest
import unittest.mock
from fastapi import HTTPException

import main
from main import synthesize, TTSTextRequest, ClientGoneError


class FakeRequest:
    def __init__(self, disconnected=False):
        self._disconnected = disconnected

    async def is_disconnected(self):
        return self._disconnected


class TestPiperEndpoint(unittest.IsolatedAsyncioTestCase):

    async def test_timeout_yields_http_504(self):
        req = TTSTextRequest(text="Timeout test sentence.", voice="default")
        fake_http = FakeRequest(disconnected=False)

        orig_wait_for = asyncio.wait_for

        async def short_wait_for(fut, timeout=None):
            # Patch timeout down to 0.05s without global recursion
            return await orig_wait_for(fut, timeout=0.05)

        slow_duration = 0.30

        def slow_synth(*args, **kwargs):
            time.sleep(slow_duration)
            return b"RIFFfake_wav"

        with unittest.mock.patch("main.synthesize_piper_in_memory", side_effect=slow_synth):
            with unittest.mock.patch("main.asyncio.wait_for", side_effect=short_wait_for):
                t0 = time.perf_counter()
                with self.assertRaises(HTTPException) as ctx:
                    await synthesize(req, fake_http)
                elapsed = time.perf_counter() - t0

                self.assertEqual(ctx.exception.status_code, 504)
                self.assertIn("timed out", ctx.exception.detail.lower())
                # Verify timeout triggered promptly without waiting for the slow thread to finish
                self.assertLess(elapsed, slow_duration * 0.7)

    async def test_queued_cancellation_yields_499_no_extra_synthesis(self):
        req = TTSTextRequest(text="Cancellation test sentence.", voice="default")
        fake_http = FakeRequest(disconnected=True)

        call_count = 0

        def cancelling_synth(text, model_path, speed=1.0, sentence_silence=0.1, cancelled=None):
            nonlocal call_count
            call_count += 1
            # Poll for the watcher task to observe disconnect and set cancelled
            for _ in range(50):
                if cancelled and cancelled.is_set():
                    raise ClientGoneError("Client disconnected")
                time.sleep(0.01)
            return b"RIFFfake_wav"

        with unittest.mock.patch("main.synthesize_piper_in_memory", side_effect=cancelling_synth):
            with self.assertRaises(HTTPException) as ctx:
                await synthesize(req, fake_http)

            self.assertEqual(ctx.exception.status_code, 499)
            self.assertEqual(call_count, 1)

    async def test_handler_responsiveness_during_synthesis_wait(self):
        req = TTSTextRequest(text="Concurrency check sentence.", voice="default")
        fake_http = FakeRequest(disconnected=False)

        worker_started = threading.Event()
        worker_finished = threading.Event()

        def delayed_synth(*args, **kwargs):
            worker_started.set()
            time.sleep(0.15)
            worker_finished.set()
            return b"RIFFfake_wav"

        ticks_in_flight = 0

        async def concurrent_health_probe():
            nonlocal ticks_in_flight
            while not worker_started.is_set():
                await asyncio.sleep(0.005)
            for _ in range(10):
                await asyncio.sleep(0.02)
                if not worker_finished.is_set():
                    ticks_in_flight += 1

        with unittest.mock.patch("main.synthesize_piper_in_memory", side_effect=delayed_synth):
            synth_task = asyncio.create_task(synthesize(req, fake_http))
            probe_task = asyncio.create_task(concurrent_health_probe())

            await asyncio.gather(synth_task, probe_task)

            # Assert probe executed and recorded ticks strictly while synthesis was in-flight
            self.assertGreaterEqual(ticks_in_flight, 1)
            self.assertTrue(worker_finished.is_set())

    async def test_kokoro_shares_client_gone_error_semantics(self):
        req = TTSTextRequest(text="Kokoro cancellation test.", voice="kokoro-af_bella")
        fake_http = FakeRequest(disconnected=True)

        def mock_kokoro_synth(text, voice_id, speed, cancelled=None):
            for _ in range(50):
                if cancelled and cancelled.is_set():
                    raise ClientGoneError("Kokoro client disconnected")
                time.sleep(0.01)
            return b"RIFFfake_wav"

        with unittest.mock.patch("main.synthesize_kokoro", side_effect=mock_kokoro_synth):
            with self.assertRaises(HTTPException) as ctx:
                await synthesize(req, fake_http)

            self.assertEqual(ctx.exception.status_code, 499)
            self.assertEqual(ctx.exception.detail, "Client disconnected")


if __name__ == "__main__":
    unittest.main()
