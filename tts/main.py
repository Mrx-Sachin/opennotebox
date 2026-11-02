"""Kokoro TTS HTTP server wrapper."""
import asyncio
import gc
import os
import re
import subprocess
import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

app = FastAPI(title="TTS Service")


class ClientGoneError(Exception):
    """Raised when the client disconnects or abandons synthesis."""


KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_MODEL_URLS = {
    "/models/kokoro-v1.0.onnx": f"{KOKORO_RELEASE}/kokoro-v1.0.onnx",
    "/models/voices-v1.0.bin": f"{KOKORO_RELEASE}/voices-v1.0.bin",
}

# Kokoro voice IDs -> (native kokoro voice name, language code). Curated from
# the model's own graded voice list (huggingface.co/hexgrad/Kokoro-82M/VOICES.md)
# to the top-graded voice per gender/accent rather than exposing all ~26.
KOKORO_VOICES = {
    "kokoro-af_heart": ("af_heart", "en-us"),
    "kokoro-af_bella": ("af_bella", "en-us"),
    "kokoro-am_michael": ("am_michael", "en-us"),
    "kokoro-am_fenrir": ("am_fenrir", "en-us"),
    "kokoro-bf_emma": ("bf_emma", "en-gb"),
    "kokoro-bm_george": ("bm_george", "en-gb"),
}


class TTSTextRequest(BaseModel):
    text: str
    voice: str = "kokoro-af_heart"
    speed: float = 1.0

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, voice: str) -> str:
        if voice not in KOKORO_VOICES:
            raise ValueError(f"Unsupported voice '{voice}'. Valid voices: {list(KOKORO_VOICES.keys())}")
        return voice


# Kokoro phonemizes through espeak-ng via phonemizer-fork.
# espeak-ng expands a clean digit string correctly ("10000000" -> "ten million")
# but breaks on two common real-world number formats:
#   - thousands-separator commas: "10,000,000" phonemizes as "ten, zero
#     zero zero, zero zero zero" instead of "ten million" - it reads each
#     comma-group as its own broken fragment rather than one number.
#   - digits separated by whitespace: "1 0 0" -> "one zero zero" instead of
#     "one hundred". This is usually a PDF extraction artifact (a multi-
#     digit number's characters got split into separate "words" during
#     column/table extraction) rather than the source text itself.
# Fix by normalizing to a single clean digit run before it reaches the
# phonemizer - confirmed via direct phonemizer testing that a
# bare "100"/"10000000" is read correctly, only the formatting breaks it.
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")
_SPACED_DIGITS_RE = re.compile(r"(?<!\d)\d(?:[ 	]+\d)+(?!\d)")


def normalize_numbers_for_speech(text: str) -> str:
    text = _THOUSANDS_COMMA_RE.sub("", text)
    text = _SPACED_DIGITS_RE.sub(lambda m: m.group(0).replace(" ", "").replace("	", ""), text)
    return text

_kokoro = None

# Proactive recycling counter - rebuilt every KOKORO_RECYCLE_AFTER requests
# to keep the CUDA arena from growing large enough to trigger OOM.
_kokoro_request_count = 0
KOKORO_RECYCLE_AFTER = 20

# Caps how many Kokoro inferences run at once. ONNX Runtime's CPU session
# grabs threads for intra-op parallelism per call; letting requests pile up
# unbounded makes them contend for the same cores and DEGRADES every one of
# them instead of processing more in parallel. Measured on this deployment's
# CPU: 1-at-a-time ~9-10s/call (stable), 2 concurrent ~11-13s/call (mild,
# net win), 4 concurrent ~17-25s/call (severe - worse than just queuing).
# 2 was the best throughput/latency balance found on CPU. GPU acceleration
# (see get_kokoro) removes most of that contention - a single GPU call is
# fast enough that a higher queuing depth (4) is a net win instead of a
# regression. Built lazily in get_kokoro(), once CUDA-active status is
# actually known (see below for why that can't be checked naively).
_kokoro_gate = None

# Serializes Kokoro session CONSTRUCTION (see get_kokoro). Separate lock
# from _kokoro_oom_lock on purpose: that one is acquired INSIDE get_kokoro's
# callers on the OOM-retry path, and a reentrant call would deadlock on it.
# Construction is never nested, so this lock is always taken by at most one
# layer of callers at a time.
_kokoro_build_lock = threading.Lock()

# How many requests are currently holding (or about to hold) a reference to
# the Kokoro session - i.e. inside _create_with_oom_retry from just before
# get_kokoro() through just after create(). The background recycle (below)
# waits for this to reach 0 before dropping the session: a request that has
# already called get_kokoro() holds a local reference to the session object,
# so its VRAM stays allocated until the local ref is dropped - the
# concurrency gate (a Semaphore) does NOT cover that window, and on a card
# where two sessions cannot coexist, dropping while such a ref is live is
# what causes the two-session OOM. _kokoro_session_cond guards the count AND
# is held across the background drop, so the "drain check" and the "drop"
# are atomic: a new request cannot bump the counter (and grab the old
# session) in the gap between them.
_kokoro_inflight = 0
_kokoro_session_cond = threading.Condition()

# CUDA can drop out of a built session for reasons that clear in seconds:
# transient VRAM pressure from a concurrent build or another GPU consumer
# (the desktop compositor, a browser). _build_kokoro_retrying() distinguishes
# that from a genuinely GPU-less host so a one-time hiccup costs one request
# ~1s of retries instead of silently pinning the whole service to CPU
# synthesis for as long as the process lives.
_KOKORO_BUILD_ATTEMPTS = 3
_KOKORO_BUILD_RETRY_DELAY_SECONDS = 1.0

# CPU-only value from the measurement above; used whenever CUDA isn't live
# and TTS_MAX_CONCURRENCY isn't set.
_KOKORO_GATE_CPU_DEFAULT = 2
# GPU headroom is far larger than CPU's - a single GPU call is fast enough
# that a deeper queue is a throughput win rather than the contention penalty
# seen on CPU.
_KOKORO_GATE_GPU_DEFAULT = 4


def _total_gpu_memory_mib() -> Optional[int]:
    """Total VRAM of the first visible GPU, or None if it can't be read.

    Shells out to nvidia-smi rather than adding a CUDA-binding dependency:
    the query is trivial, and the tool is necessarily present whenever the
    GPU is actually usable from inside this container.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _gpu_concurrency_default() -> int:
    """Concurrency for a live-CUDA session, scaled to the card's VRAM.

    A flat "4 because CUDA" was wrong and shipped a real outage: each
    concurrent Kokoro inference allocates its own activation buffers on top
    of an ONNX Runtime arena that grows to several GB, so on a 6GB laptop
    GPU - shared with the user's desktop compositor and browser - four in
    flight exhausted VRAM. Synthesis then failed with
    "BFC_Arena Failed to allocate memory", the API's stream died mid-body,
    and the browser reported it as an unrelated CORS error. Small cards must
    stay conservative; the env var remains the escape hatch either way.
    """
    total = _total_gpu_memory_mib()
    if total is None:
        return 2
    if total >= 16000:
        return 4
    if total >= 10000:
        return 3
    # Small cards run ONE inference at a time. A single Kokoro pass over a
    # normal ~320-char narration chunk allocates activations in the hundreds
    # of MB; two of those overlapping on a 6GB card that is also driving a
    # desktop is what exhausted VRAM in the first place. Serialising costs
    # throughput but the client already prefetches ahead, so the listener
    # does not hear the difference - whereas an OOM stops narration dead.
    return 1


def _resolve_kokoro_concurrency(cuda_active: bool) -> int:
    """Pick the Kokoro gate depth: TTS_MAX_CONCURRENCY env override if set
    and valid, else a VRAM-scaled default when CUDA is actually live (see
    _gpu_concurrency_default), else 2 (see the CPU contention measurements
    above _KOKORO_GATE_CPU_DEFAULT).

    cuda_active must come from inspecting the constructed session's
    get_providers() (see _construct_kokoro_with_gpu_providers's docstring),
    not onnxruntime.get_available_providers() - the latter lists
    CUDAExecutionProvider as soon as onnxruntime-gpu is installed, even if
    the session that was actually built ended up falling back to CPU (no
    GPU present, driver mismatch, nvidia-container-toolkit missing, etc).
    Trusting it here would size the gate for GPU throughput on a CPU-bound
    service and reintroduce the exact contention this gate exists to avoid.
    """
    env_val = os.environ.get("TTS_MAX_CONCURRENCY")
    if env_val:
        try:
            parsed = int(env_val)
            if parsed > 0:
                return parsed
            print(f"TTS_MAX_CONCURRENCY={env_val!r} must be positive, ignoring")
        except ValueError:
            print(f"TTS_MAX_CONCURRENCY={env_val!r} is not an int, ignoring")
    return _gpu_concurrency_default() if cuda_active else _KOKORO_GATE_CPU_DEFAULT


def _preload_gpu_dlls():
    """Explicitly load the pip-installed CUDA/cuDNN shared libraries before
    any InferenceSession is created.

    onnxruntime-gpu wheels dynamically link CUDA/cuDNN rather than bundling
    them - without this, CUDAExecutionProvider can be silently absent from
    get_available_providers() (or fail to initialize) even though the
    package installed fine and a GPU is present, because the libs pip put
    in site-packages/nvidia/... aren't on LD_LIBRARY_PATH (see
    microsoft/onnxruntime #21354, #23736, #21825). preload_dlls() (added in
    onnxruntime-gpu 1.21+) finds and loads them from those pip locations
    directly. This is a no-op on plain CPU-only onnxruntime installs
    (no preload_dlls attribute) and must never be allowed to crash the
    service - CPU synthesis has to keep working with no GPU, no
    nvidia-container-toolkit, or a mismatched driver.
    """
    import onnxruntime

    try:
        if hasattr(onnxruntime, "preload_dlls"):
            onnxruntime.preload_dlls()
    except Exception as e:
        print(f"onnxruntime.preload_dlls() failed, continuing CPU-only: {e}")

    try:
        print(f"onnxruntime available providers: {onnxruntime.get_available_providers()}")
    except Exception as e:
        print(f"Could not query onnxruntime providers: {e}")


def _construct_kokoro_with_gpu_providers(model_path: str, voices_path: str):
    """Build the Kokoro session, forcing CUDA to actually be tried.

    kokoro-onnx's own GPU detection is broken: it gates on
    `importlib.util.find_spec("onnxruntime-gpu")`, but there is no
    importable module by that name - both the "onnxruntime" and
    "onnxruntime-gpu" PyPI packages install to the same "onnxruntime"
    module, so that check is always None and it silently never asks for
    anything but CPUExecutionProvider, even with onnxruntime-gpu installed
    and a working GPU present. Confirmed directly: get_available_providers()
    correctly lists CUDAExecutionProvider, but kokoro.sess.get_providers()
    came back CPU-only until this was forced.

    Worked around by temporarily wrapping onnxruntime.InferenceSession so
    Kokoro.__init__'s call to it gets an explicit provider list - ONNX
    Runtime itself handles the CUDA-unavailable case by falling through to
    the next entry, so this is not a hard requirement on CUDA being
    present, just a fix for kokoro-onnx never asking.
    """
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    original_session = rt.InferenceSession

    # Cap the CUDA arena rather than letting it grow without bound.
    #
    # ONNX Runtime's default BFC arena extends geometrically and never gives
    # memory back, so a long narration session ratchets VRAM upward until the
    # card is full - measured here going from 1.9GB to 5.7GB of a 6GB card
    # across a handful of requests, after which every synthesis failed with
    # "Failed to allocate memory". On a dedicated inference box that is
    # merely untidy; on a laptop GPU shared with the desktop compositor and a
    # browser it is fatal, and it presented as narration mysteriously dying
    # part way through a reading session.
    #
    # kSameAsRequested stops the geometric over-allocation, and gpu_mem_limit
    # gives the arena a hard ceiling so the card cannot be starved.
    #
    # Size the ceiling as "everything except a reserve for the rest of the
    # machine" rather than a small fixed number. A cap that is too tight is
    # its own outage: at 2GB, real book-length chunks (~320 chars, far bigger
    # activations than a short test phrase) failed with "Available memory of
    # 582144 is smaller than requested bytes of 31272448" - the arena could
    # not grow to satisfy a legitimate single inference. The reserve keeps a
    # desktop's compositor and browser alive on a shared laptop GPU, which is
    # the case that motivated capping at all.
    total_vram = _total_gpu_memory_mib()
    # Scale the reserve based on total VRAM - small cards need less reserved
    # because the desktop compositor itself uses less on integrated/hybrid
    # setups. On a 6GB laptop GPU, actual desktop+browser measured ~400-600MB.
    # On a 24GB desktop GPU, the compositor and browser tabs can easily hit
    # 2GB+. A flat 2GB reserve wasted too much headroom on small cards.
    if total_vram and total_vram <= 8000:
        DESKTOP_RESERVE_MIB = min(768, total_vram // 8)
    else:
        DESKTOP_RESERVE_MIB = 2048
    MIN_ARENA_MIB = 1536
    if total_vram:
        mem_limit_mib = max(total_vram - DESKTOP_RESERVE_MIB, MIN_ARENA_MIB)
    else:
        mem_limit_mib = MIN_ARENA_MIB
    cuda_options = {
        "arena_extend_strategy": "kSameAsRequested",
        "gpu_mem_limit": mem_limit_mib * 1024 * 1024,
    }
    print(f"CUDA provider options: {cuda_options} (total VRAM {total_vram} MiB, reserve {DESKTOP_RESERVE_MIB} MiB)")

    def _session_with_cuda(path, providers=None, **kwargs):
        return original_session(
            path,
            providers=[("CUDAExecutionProvider", cuda_options), "CPUExecutionProvider"],
            **kwargs,
        )

    rt.InferenceSession = _session_with_cuda
    try:
        kokoro = Kokoro(model_path, voices_path)
    finally:
        rt.InferenceSession = original_session

    _serialize_phonemizer(kokoro)
    print(f"Kokoro session providers: {kokoro.sess.get_providers()}")
    return kokoro


# espeak-ng, which Kokoro phonemizes through, keeps global state and is not
# safe to call from several threads at once. Two overlapping requests
# interleave inside it and one request's phonemes come back attributed to the
# other's input, which the phonemizer then rejects:
#
#   Kokoro synthesis failed: number of lines in input and output must be
#   equal, we have: input=2, output=4
#
# That surfaced to the reader as a 502 and a "speech engine could not
# synthesize this passage" toast, which reads like a GPU problem and is not
# one - it only appeared when the concurrency gate was above 1, i.e. after a
# CPU fallback widened it, so it looked like a symptom of the fallback rather
# than a race of its own.
#
# Only phonemization is serialized, not inference: the ONNX session is
# thread-safe and is the part worth overlapping, so a GPU with a gate above 1
# keeps its throughput.
#
# The lock is module-level, NOT per-session, and that distinction is the whole
# fix. A per-session lock still failed, because the session is rebuilt - by
# proactive recycling and by the OOM retry path - and during a rebuild the old
# and new Kokoro objects briefly coexist. Two sessions meant two locks, which
# excluded nothing, while the espeak state they were both driving stayed
# global. Anything guarding a process-wide resource has to outlive the objects
# that happen to reach it.
_ESPEAK_LOCK = threading.Lock()


def _serialize_phonemizer(kokoro) -> None:
    tokenizer = getattr(kokoro, "tokenizer", None)
    phonemize = getattr(tokenizer, "phonemize", None)
    if phonemize is None:
        # A kokoro-onnx that no longer exposes this is not a reason to refuse
        # to synthesize; log loudly instead, since the race would be back.
        print("WARNING: kokoro tokenizer has no phonemize(); cannot serialize espeak")
        return
    if getattr(phonemize, "_espeak_serialized", False):
        return  # already wrapped, don't nest a second wrapper on a rebuild

    def locked_phonemize(*args, **kwargs):
        with _ESPEAK_LOCK:
            return phonemize(*args, **kwargs)

    locked_phonemize._espeak_serialized = True
    tokenizer.phonemize = locked_phonemize


def _cuda_in(kokoro) -> bool:
    return "CUDAExecutionProvider" in kokoro.sess.get_providers()


def _build_kokoro_retrying(model_path: str, voices_path: str):
    """Build the Kokoro session, retrying when it SILENTLY fell back to CPU
    on a machine that demonstrably has a GPU.

    _construct_kokoro_with_gpu_providers forces CUDAExecutionProvider first,
    but ONNX Runtime degrades to CPUExecutionProvider without raising when it
    can't get the GPU at that instant - and the usual trigger is transient:
    another session build racing the same card, or a moment of VRAM pressure
    from the desktop compositor on a shared laptop GPU. Without a retry, the
    first such build quietly pinned the service to CPU synthesis (measured
    4-8s per sentence here vs 0.28s on CUDA) until the next process restart
    or rebuild.

    Retrying is bounded and only when a GPU is actually present (nvidia-smi
    can read one): a genuinely GPU-less host builds CPU once and accepts it,
    so a cold start without a GPU costs nothing extra."""
    _preload_gpu_dlls()
    gpu_present = _total_gpu_memory_mib() is not None
    attempt = 0
    while True:
        attempt += 1
        kokoro = _construct_kokoro_with_gpu_providers(model_path, voices_path)
        if _cuda_in(kokoro) or not gpu_present or attempt >= _KOKORO_BUILD_ATTEMPTS:
            return kokoro
        print(
            f"WARNING: Kokoro session built CPU-only although a GPU is present "
            f"(attempt {attempt}/{_KOKORO_BUILD_ATTEMPTS}) - likely transient VRAM "
            f"contention, retrying in {_KOKORO_BUILD_RETRY_DELAY_SECONDS}s"
        )
        time.sleep(_KOKORO_BUILD_RETRY_DELAY_SECONDS)


def get_kokoro():
    """Lazily construct the Kokoro ONNX session on first use - no reason
    to pay that load cost at startup before the first synthesis request arrives.

    Also sizes _kokoro_gate here, on first construction, since whether CUDA
    actually came up is only knowable after the session exists (see
    _resolve_kokoro_concurrency's docstring for why that check can't be done
    naively/earlier).

    Construction is serialized: a rebuild (idle release, 20-request recycle,
    OOM reset) lands in the middle of a narration session, where the entry
    chunk plus its prefetches arrive at once. Without the lock, every
    concurrent cold request built its own session at the same time - each
    reserving most of the card's VRAM for its arena - and the losers fell
    back to CPU while the LAST write to the global won the service, so a
    racing CPU build could pin the whole service to 4-8s-per-sentence
    synthesis. With the lock, one build serves all of them and the rest
    return immediately once it is published."""
    global _kokoro, _kokoro_gate
    if _kokoro is not None:
        return _kokoro
    with _kokoro_build_lock:
        if _kokoro is None:  # another thread may have built it while we waited
            _kokoro = _build_kokoro_retrying(
                "/models/kokoro-v1.0.onnx", "/models/voices-v1.0.bin"
            )
            cuda_active = _cuda_in(_kokoro)
            if not cuda_active and _total_gpu_memory_mib() is not None:
                print(
                    "WARNING: Kokoro running CPU-only on a GPU machine after "
                    f"{_KOKORO_BUILD_ATTEMPTS} attempts - every synthesis will "
                    "take seconds instead of ~0.3s. Check VRAM (nvidia-smi) and "
                    "restart the service."
                )
            _kokoro_gate = threading.Semaphore(_resolve_kokoro_concurrency(cuda_active))
            print(f"Kokoro concurrency gate set to {_kokoro_gate._value} (cuda_active={cuda_active})")
    return _kokoro


def release_kokoro():
    """Explicitly drop the Kokoro session so its GPU VRAM is freed.

    Used at shutdown and by the idle-release timer. gc.collect() is
    mandatory: ONNX Runtime's C++ objects are only freed when Python
    releases the last reference, and the GIL/ref-count dance can leave them
    hanging indefinitely without an explicit collection pass.

    Must wait for in-flight inference (same lock as _schedule_kokoro_rebuild):
    dropping _kokoro_gate while a request is in _create_with_oom_retry made
    `_kokoro_gate.acquire()` raise AttributeError and 500 the click.
    """
    global _kokoro, _kokoro_gate, _kokoro_request_count
    with _kokoro_session_cond:
        if not _kokoro_session_cond.wait_for(lambda: _kokoro_inflight == 0, timeout=5):
            print("WARNING: release_kokoro timed out waiting for in-flight inference")
            return
        if _kokoro is None:
            return
        _kokoro = None
        _kokoro_gate = None
        _kokoro_request_count = 0
    gc.collect()


# Deferred proactive recycle: the KOKORO_RECYCLE_AFTER-th request runs its
# inference on the warm session and only then schedules a background
# rebuild. It does NOT drop or rebuild itself - a full drop + gc.collect() +
# ONNX rebuild costs ~1s, and doing it inside the request the counter hit
# meant exactly one user's click-to-audio in every ~20 (the entry chunk of a
# fresh page) paid that on top of its own synthesis. The background thread
# instead waits until NO inference is in flight, then drops + rebuilds while
# idle, so no user request ever pays the ~1s rebuild cost.
_kokoro_rebuild_in_progress = False
_kokoro_rebuild_lock = threading.Lock()


def _schedule_kokoro_rebuild():
    """Drop + rebuild the Kokoro session in a background thread, only once
    every in-flight inference has finished.

    Called from _create_with_oom_retry AFTER the current request's own
    inference completed on the warm session, so the caller never blocks on
    the rebuild. The background thread must not drop the session while any
    inference is in flight: an in-flight inference holds a local reference
    to the session object (its VRAM stays allocated until create() returns
    and the local ref drops), and on this card two sessions cannot coexist -
    dropping mid-traffic would let another request build a second session
    against the old one, the exact OOM _reset_kokoro_session's docstring
    describes.

    The drop is therefore preceded by waiting on _kokoro_inflight hitting 0,
    and BOTH the wait and the drop happen while holding _kokoro_session_cond
    (the same lock a request takes to bump _kokoro_inflight), making the
    drain-check and the drop atomic: a new request cannot slip in between
    them and grab the old session. The drop+rebuild then run under
    _kokoro_build_lock, so a concurrent request that does see _kokoro=None
    just blocks on the build lock and picks up the fresh session - it never
    builds a duplicate and never pays the cost in its own request path.

    At most one background thread runs at a time; further schedules while
    one is pending are no-ops (the drop resets _kokoro_request_count, so the
    next rebuild is another KOKORO_RECYCLE_AFTER requests away)."""
    global _kokoro_rebuild_in_progress
    with _kokoro_rebuild_lock:
        if _kokoro_rebuild_in_progress:
            return
        _kokoro_rebuild_in_progress = True

    def _rebuild_bg():
        global _kokoro_rebuild_in_progress, _kokoro, _kokoro_gate, _kokoro_request_count
        try:
            # 1. Wait for every in-flight inference to finish, then drop the
            #    old session, atomically (same lock the counter bumps under),
            #    so no new request can grab the old session in the gap.
            with _kokoro_session_cond:
                _kokoro_session_cond.wait_for(lambda: _kokoro_inflight == 0)
                _kokoro = None
                _kokoro_gate = None
                _kokoro_request_count = 0
            gc.collect()
            # 2. Rebuild a fresh session so the NEXT request is warm.
            #    Serialized by the build lock: a concurrent request that hit
            #    _kokoro=None blocks here and then finds the fresh session.
            with _kokoro_build_lock:
                if _kokoro is None:
                    _kokoro = _build_kokoro_retrying(
                        "/models/kokoro-v1.0.onnx", "/models/voices-v1.0.bin"
                    )
                    cuda_active = _cuda_in(_kokoro)
                    if not cuda_active and _total_gpu_memory_mib() is not None:
                        print(
                            "WARNING: Kokoro running CPU-only on a GPU machine "
                            "after background rebuild - check VRAM (nvidia-smi)."
                        )
                    if _kokoro_gate is None:
                        _kokoro_gate = threading.Semaphore(
                            _resolve_kokoro_concurrency(cuda_active)
                        )
                        print(
                            f"Kokoro concurrency gate set to "
                            f"{_kokoro_gate._value} (cuda_active={cuda_active})"
                        )
        except Exception as exc:
            # A failed rebuild leaves _kokoro=None; the next request rebuilds
            # through get_kokoro's own retry path. A background thread must
            # never take the service down with it.
            print(f"WARNING: background Kokoro rebuild failed: {exc}")
        with _kokoro_rebuild_lock:
            _kokoro_rebuild_in_progress = False
    threading.Thread(target=_rebuild_bg, daemon=True).start()


# Kokoro's phonemizer degrades on very long inputs - split on sentence
# boundaries and concatenate the synthesized audio instead of passing the
# whole request text through in one call. Callers already send small chunks
# (frontend targets ~280 chars) but /play/text allows up to 5000, and
# nothing stops a future caller from sending a whole page at once.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
KOKORO_MAX_CHARS = 400


def _kokoro_chunks(text: str) -> list:
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    chunks: list = []
    buf = ""
    for part in parts:
        if buf and len(buf) + len(part) + 1 > KOKORO_MAX_CHARS:
            chunks.append(buf)
            buf = part
        else:
            buf = f"{buf} {part}".strip()
    if buf:
        chunks.append(buf)
    return chunks or [text]


# Kokoro asserts 0.5 <= speed <= 2.0 and raises otherwise - clamp rather
# than let a caller-requested speed above 2.0 (e.g. a user's 3x reading
# preference) crash the request. Values beyond this range are handled by
# the caller applying the remainder as audio.playbackRate client-side.
KOKORO_MIN_SPEED = 0.5
KOKORO_MAX_SPEED = 2.0


# Serialises every Kokoro call during OOM recovery. Held for the whole
# retry so no other request can be mid-inference (and holding VRAM) while
# the retry runs - a retry that races the very allocations that caused the
# OOM just fails again.
_kokoro_oom_lock = threading.Lock()


# Matches both the BFC arena's own allocation failures AND the lower-level
# CUDA/cuBLAS/cuDNN resource failures that show up once VRAM is exhausted
# system-wide (e.g. "CUBLAS failure 3: the resource allocation failed" while
# creating a cuBLAS handle during session INITIALIZATION - a real failure
# mode hit in testing, and one the arena-only message check below used to
# miss entirely, so it skipped retry and went straight to a 500).
_OOM_MARKERS = (
    "failed to allocate memory",
    "out of memory",
    "resource allocation failed",
    "cuda_error_out_of_memory",
    "cublas_status_alloc_failed",
    "cudnn_status_alloc_failed",
)

# The BFC arena's actual per-node failure never uses any of the phrases above.
# It reports the node that failed and the shortfall instead, e.g.:
#   "...bfc_arena.cc:358 ... AllocateRawInternal(...) Available memory of
#   4639488 is smaller than requested bytes of 51041024"
# which is the message this codebase hits in practice (confirmed against
# real container logs) - so _OOM_MARKERS alone silently never matched a real
# arena exhaustion and every request went straight to a 500 with no retry
# and no session rebuild, defeating the whole point of this module.
_ARENA_SHORTFALL_RE = re.compile(r"available memory of \d+ is smaller than requested bytes of \d+")


def _is_oom(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _OOM_MARKERS) or bool(_ARENA_SHORTFALL_RE.search(msg))


def _reset_kokoro_session():
    """Drop every reference to the current Kokoro session and force garbage
    collection, so the next get_kokoro() call builds a fresh one with an
    empty CUDA arena - and, critically, so the OLD session's VRAM is
    actually freed BEFORE the new one starts allocating.

    ONNX Runtime's BFC arena only grows, it never shrinks - a long
    sequential run (a podcast synthesizes dozens of dialogue lines back to
    back, one at a time, with no concurrency involved at all) can walk the
    arena all the way up to its gpu_mem_limit ceiling over the course of the
    run and simply stay there. Once that happens, even a fully-serialized
    retry with no other request in flight still OOMs, because there is
    nothing left to reclaim - a bigger allocation than anything seen so far
    just doesn't fit.

    Rebuilding the session is the only way to give that memory back, but a
    first attempt at this (setting the module global to None and letting
    get_kokoro() rebuild) still failed under real load: the CALLER's local
    `kokoro` variable was reassigned as `kokoro = get_kokoro()`, and Python
    evaluates the right-hand side BEFORE rebinding the name - so the OLD
    session object was still referenced (and its VRAM still held) at the
    exact moment the new session tried to allocate its own cuBLAS/cuDNN
    handles, on a card with no room for both at once. gc.collect() here
    forces prompt collection regardless of what reference graph the caller
    holds, rather than relying on it to drop its own local var first.
    """
    global _kokoro
    print("Kokoro GPU OOM persisted through serial retry - rebuilding session to reclaim VRAM")
    _kokoro = None
    gc.collect()


def _create_with_oom_retry(
    chunk: str, native_voice: str, speed: float, lang: str, cancelled: Optional[threading.Event] = None
):
    """Run one Kokoro inference, retrying on a GPU OOM with escalating fixes.

    If `cancelled` is set (the HTTP client disconnected), the request
    abandons as soon as it can: at the GPU-slot wait (before or between
    poll ticks) and right after the uninterruptible create() returns.

    VRAM pressure has two distinct causes, handled in order:
    1. Transient contention - several requests overlapping, or another app on
       a shared GPU (a browser, a compositor) grabbing memory right now. The
       first attempt takes the normal concurrency gate; if that fails, a
       retry under an exclusive lock (nothing else mid-inference) is the
       condition most likely to succeed.
    2. Sustained arena exhaustion from a long run (see _reset_kokoro_session).
       If even the exclusive-lock retry OOMs, contention isn't the problem -
       rebuild the session to reclaim the arena and try once more.

    Proactive recycling: after KOKORO_RECYCLE_AFTER requests the session is
    rebuilt preemptively (before it ever OOMs). This avoids the worst case
    where the arena grows so large that even a rebuild fails because there
    is no room for both old and new sessions simultaneously. Recycling also
    avoids the user-facing 502 entirely for sustained reading sessions.

    get_kokoro() itself (including session RECONSTRUCTION, not just
    inference) is inside the try/except here for exactly that reason - a
    rebuild that itself fails to allocate is still an OOM this function
    should recognize, not an uncaught exception that skips retry entirely.

    Degrading to "slower" beats failing outright: the previous behaviour
    propagated any OOM as a 500 immediately, which for narration killed the
    API's already-started audio stream, and for a podcast meant losing all
    prior synthesized lines and the whole generation job failing near the end.
    """
    # Proactive recycling: after KOKORO_RECYCLE_AFTER requests the session is
    # refreshed so the CUDA arena never grows large enough to trigger an OOM.
    # The counting is cheap; the expensive drop+rebuild is deferred to a
    # background thread (see _schedule_kokoro_rebuild) that runs AFTER this
    # request's own inference completes, so this request - often the entry
    # chunk of a fresh page the user is actively waiting on - never pays the
    # ~1s rebuild cost. (Both globals must be declared: the finally block
    # below does `_kokoro_inflight -= 1`, and without the declaration Python
    # would treat that as a local variable and raise UnboundLocalError.)
    global _kokoro_request_count, _kokoro_inflight
    recycle_after_this = False
    with _kokoro_session_cond:
        _kokoro_request_count += 1
        if _kokoro_request_count >= KOKORO_RECYCLE_AFTER:
            recycle_after_this = True
        _kokoro_inflight += 1
    try:
        try:
            kokoro = get_kokoro()
            # Wait for the GPU slot while still honouring cancellation. A
            # blocking acquire() would let an abandoned request (client
            # disconnected mid-stream) queue up behind everything else and
            # make the NEW request it was replaced by wait for its full
            # synthesis - the single-slot gate turns that into several
            # seconds of perceived latency on a page jump. Polling lets
            # dead requests leave the queue immediately.
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise ClientGoneError()
                if _kokoro_gate.acquire(timeout=0.25):
                    break
            try:
                result = kokoro.create(chunk, voice=native_voice, speed=speed, lang=lang)
            finally:
                _kokoro_gate.release()
            if cancelled is not None and cancelled.is_set():
                raise ClientGoneError()
            return result
        except Exception as exc:
            if not _is_oom(exc):
                raise
            print(f"Kokoro GPU OOM, retrying serially: {str(exc)[:160]}")
            try:
                with _kokoro_oom_lock:
                    kokoro = get_kokoro()
                    with _kokoro_gate:
                        return kokoro.create(chunk, voice=native_voice, speed=speed, lang=lang)
            except Exception as exc2:
                if not _is_oom(exc2):
                    raise
                with _kokoro_oom_lock:
                    # Drop this frame's own reference before rebuilding - see
                    # _reset_kokoro_session's docstring for why this matters.
                    del kokoro
                    _reset_kokoro_session()
                    kokoro = get_kokoro()
                    with _kokoro_gate:
                        return kokoro.create(chunk, voice=native_voice, speed=speed, lang=lang)
    finally:
        with _kokoro_session_cond:
            _kokoro_inflight -= 1
            _kokoro_session_cond.notify_all()
        if recycle_after_this:
            _schedule_kokoro_rebuild()


# kokoro-onnx truncates its internal phoneme sequence to a 510-length buffer
# but then indexes it up to (and including) position 510 - an off-by-one in
# the library itself, not our code - raising "index 510 is out of bounds for
# axis 0 with size 510" once a chunk's TEXT phonemizes to more than the
# model's context window. _kokoro_chunks' KOKORO_MAX_CHARS cap does not
# reliably prevent this: it bounds character count, but the phoneme:character
# ratio is not fixed - unusual text (proper nouns, foreign words, dense
# jargon, or in the pathological case that surfaced this, non-word gibberish)
# can phonemize far less compactly than ordinary prose and blow past the
# limit at a character count well under the cap. Bisecting the chunk and
# retrying each half sidesteps the bug entirely rather than trying to predict
# phoneme count from character count up front.
_KOKORO_PHONEME_BUG_MARKER = "is out of bounds for axis 0 with size"
_KOKORO_BISECT_MAX_DEPTH = 4


def _synthesize_chunk_with_bisection(
    chunk: str, native_voice: str, speed: float, lang: str, depth: int = 0, cancelled: Optional[threading.Event] = None
):
    try:
        return _create_with_oom_retry(chunk, native_voice, speed, lang, cancelled)
    except IndexError as exc:
        if _KOKORO_PHONEME_BUG_MARKER not in str(exc):
            raise
        words = chunk.split()
        if depth >= _KOKORO_BISECT_MAX_DEPTH or len(words) < 2:
            # A single "word" that alone overflows the phoneme buffer (e.g. a
            # very long garbled token) can't be split further - let it fail
            # as a real error rather than looping.
            raise
        print(
            f"Kokoro phoneme-length bug hit (depth {depth}), splitting chunk of "
            f"{len(words)} words and retrying: {str(exc)[:120]}"
        )
        mid = len(words) // 2
        left = " ".join(words[:mid])
        right = " ".join(words[mid:])
        import numpy as np
        s1, sr = _synthesize_chunk_with_bisection(left, native_voice, speed, lang, depth + 1)
        s2, _ = _synthesize_chunk_with_bisection(right, native_voice, speed, lang, depth + 1)
        return np.concatenate([s1, s2]), sr


def synthesize_kokoro(
    text: str, voice_id: str, speed: float, cancelled: Optional[threading.Event] = None
) -> bytes:
    import io
    import numpy as np
    import soundfile as sf

    # Not resolved here: _create_with_oom_retry calls get_kokoro() itself,
    # per chunk. That matters across a multi-chunk text (long podcast line
    # split by _kokoro_chunks) - if an earlier chunk in this same call
    # triggered a session rebuild, later chunks must pick up the NEW
    # session rather than one captured before the loop started.
    native_voice, lang = KOKORO_VOICES[voice_id]
    speed = max(KOKORO_MIN_SPEED, min(KOKORO_MAX_SPEED, speed))

    all_samples = []
    sample_rate = 24000
    for chunk in _kokoro_chunks(text):
        if cancelled is not None and cancelled.is_set():
            raise ClientGoneError()
        samples, sample_rate = _synthesize_chunk_with_bisection(
            chunk, native_voice, speed, lang, 0, cancelled
        )
        all_samples.append(samples)
    if cancelled is not None and cancelled.is_set():
        raise ClientGoneError()

    combined = np.concatenate(all_samples) if len(all_samples) > 1 else all_samples[0]
    buf = io.BytesIO()
    sf.write(buf, combined, sample_rate, format="WAV")
    return buf.getvalue()

# Kokoro model files are large (~310MB) - give urlretrieve enough socket timeout
# headroom for a slow link instead of the interpreter default.
MODEL_DOWNLOAD_TIMEOUT_SECONDS = 300


# Idle-release: release Kokoro's GPU VRAM after this many seconds with no
# Kokoro request in flight. Prevents GPU memory sitting idle when the user
# has closed the app or left the tab. The timer is reset on every Kokoro
# request start and cancelled on request completion. Set to 0 to disable.
_IDLE_RELEASE_SECONDS = int(os.environ.get("TTS_IDLE_RELEASE_SECONDS", "300"))
_idle_timer: Optional[threading.Timer] = None


def _start_idle_timer():
    """Start the idle-release timer. Call when a Kokoro request finishes so
    that after _IDLE_RELEASE_SECONDS with no new requests, VRAM is freed."""
    global _idle_timer
    if _IDLE_RELEASE_SECONDS <= 0:
        return
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer = threading.Timer(_IDLE_RELEASE_SECONDS, release_kokoro)
    _idle_timer.daemon = True
    _idle_timer.start()


def _cancel_idle_timer():
    """Cancel the idle-release timer. Call at the start of a new Kokoro
    request to prevent a stale timer from releasing the session mid-work."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


@app.on_event("startup")
def download_missing_models():
    """Fetch any missing voice models at startup.

    The Dockerfile bakes the models into the image, but the /models named
    volume masks image contents when the volume pre-dates the image (e.g. an
    existing deployment upgrading to a new voice registry), so download
    whichever files are missing.
    """
    import socket
    import urllib.request

    def fetch(path: str, src: str, label: str):
        if os.path.exists(path):
            return
        try:
            print(f"Downloading missing {label} model file {path} ...")
            # urlretrieve doesn't take a timeout kwarg directly - set the
            # global default socket timeout for the duration of the call.
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(MODEL_DOWNLOAD_TIMEOUT_SECONDS)
            try:
                urllib.request.urlretrieve(src, path)
            finally:
                socket.setdefaulttimeout(old_timeout)
        except Exception as e:
            print(f"Failed to download {src}: {e}")

    for model_path, url in KOKORO_MODEL_URLS.items():
        fetch(model_path, url, "Kokoro")


@app.on_event("shutdown")
def shutdown_event():
    """Release GPU memory on shutdown so the container exits cleanly."""
    release_kokoro()
    if _idle_timer is not None:
        _idle_timer.cancel()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/release-gpu")
def release_gpu_memory():
    """Manually release the Kokoro GPU session, freeing VRAM.

    The session will be lazily rebuilt on the next Kokoro request. Use this
    when the app is closing or to force GPU memory back to the system."""
    release_kokoro()
    return {"status": "released"}


@app.post("/tts")
async def synthesize(request: TTSTextRequest, http_request: Request):
    """Synthesize text to speech using Kokoro."""
    text = normalize_numbers_for_speech(request.text)

    _cancel_idle_timer()
    cancelled = threading.Event()

    async def _watch_disconnect():
        try:
            while not cancelled.is_set():
                if await http_request.is_disconnected():
                    cancelled.set()
                    return
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            pass

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        from fastapi.responses import Response

        try:
            audio_data = await asyncio.wait_for(
                run_in_threadpool(
                    synthesize_kokoro, text, request.voice, request.speed, cancelled
                ),
                timeout=120.0,
            )
        except (ClientGoneError, asyncio.CancelledError):
            if cancelled.is_set():
                raise ClientGoneError()
            raise
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Kokoro synthesis timed out")

        return Response(content=audio_data, media_type="audio/wav")
    except ClientGoneError:
        print("Kokoro synthesis abandoned: client disconnected")
        raise HTTPException(status_code=499, detail="Client disconnected")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kokoro synthesis failed: {e}")
    finally:
        cancelled.set()
        watcher.cancel()
        _start_idle_timer()
