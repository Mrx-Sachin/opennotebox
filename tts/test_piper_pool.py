import io
import threading
import time
import unittest
import unittest.mock
import wave

from piper_pool import (
    ClientGoneError,
    _synthesis_lock,
    get_piper_voice,
    synthesize_piper_in_memory,
)


class TestPiperPool(unittest.TestCase):
    MODEL_PATH = "/models/en_US-libritts_r-medium.onnx"

    def test_in_memory_synthesis_returns_valid_wav(self):
        text = "Hello world."
        wav_bytes = synthesize_piper_in_memory(text, self.MODEL_PATH)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav_bytes[:16])

        # Verify it can be read as wave
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertGreater(wf.getnframes(), 0)

    def test_cancellation_before_synthesis_raises_client_gone(self):
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(ClientGoneError):
            synthesize_piper_in_memory("Unreachable text", self.MODEL_PATH, cancelled=cancelled)

        # Ensure lock remains available
        self.assertFalse(_synthesis_lock.locked())

    def test_cancellation_during_lock_contention(self):
        # Hold lock externally to simulate another request in flight
        cancelled = threading.Event()
        _synthesis_lock.acquire()

        result_err = []

        def worker():
            try:
                synthesize_piper_in_memory("Queued request", self.MODEL_PATH, cancelled=cancelled)
            except Exception as e:
                result_err.append(e)

        t = threading.Thread(target=worker)
        t.start()

        # Let worker enter lock acquire wait loop
        time.sleep(0.15)
        # Cancel while queued
        cancelled.set()
        t.join(timeout=2.0)

        _synthesis_lock.release()

        self.assertEqual(len(result_err), 1)
        self.assertIsInstance(result_err[0], ClientGoneError)
        self.assertFalse(_synthesis_lock.locked())

    def test_inter_chunk_sentence_silence_frame_count(self):
        sample_rate = 22050
        sentence_silence = 0.1
        expected_silence_frames = int(sample_rate * sentence_silence)

        chunk1_pcm = b"\x01\x00" * 1000
        chunk2_pcm = b"\x02\x00" * 1500

        class FakeChunk:
            def __init__(self, pcm):
                self.audio_int16_bytes = pcm

        class FakeConfig:
            sample_rate = 22050

        class FakeVoice:
            config = FakeConfig()

            def synthesize(self, text, syn_config=None):
                return [FakeChunk(chunk1_pcm), FakeChunk(chunk2_pcm)]

        with unittest.mock.patch("piper_pool.get_piper_voice", return_value=FakeVoice()):
            wav_bytes = synthesize_piper_in_memory(
                "Sentence one. Sentence two.",
                "dummy_path",
                sentence_silence=sentence_silence,
            )

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            total_frames = wf.getnframes()
            expected_total_frames = 1000 + expected_silence_frames + 1500
            self.assertEqual(total_frames, expected_total_frames)

            all_frames = wf.readframes(total_frames)
            self.assertEqual(all_frames[:2000], chunk1_pcm)
            silence_start = 2000
            silence_end = 2000 + (expected_silence_frames * 2)
            self.assertEqual(all_frames[silence_start:silence_end], bytes(expected_silence_frames * 2))
            self.assertEqual(all_frames[silence_end:], chunk2_pcm)


if __name__ == "__main__":
    unittest.main()
