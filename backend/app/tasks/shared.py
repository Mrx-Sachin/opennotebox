"""Shared utilities for Celery tasks."""
import asyncio
import zipfile
from typing import TypeVar, Awaitable

T = TypeVar("T")

# Zip-bomb guard for the zip-based document formats (docx/pptx/epub). A
# 150 MB archive that decompresses to many GB used to OOM the worker mid-parse
# and burn all retries. Two independent limits:
#  - an ABSOLUTE decompressed size (the real OOM ceiling): a bomb's signature
#    is a huge expanded total, so capping it caps the damage regardless of
#    how it got there. Sized to 512 MB so it sits well under the worker's
#    768 MB per-child memory recycle threshold (celery_app.py), where an
#    XML tree of that size would actually get the process killed.
#  - a decompression RATIO, set high (1000x) so it catches the classic
#    42.zip bomb without rejecting legitimate highly-repetitive documents
#    (a page of the same character, repeated boilerplate), which can
#    compress well past 100x and would be a false positive at a lower ratio.
MAX_ZIP_DECOMPRESSION_RATIO = 1000
MAX_ZIP_DECOMPRESSED_BYTES = 512 * 1024 * 1024


def guard_zip_decompression(zip_path: str) -> None:
    """Reject a zip container that is a compression bomb.

    The central directory of a well-formed zip always carries the real
    per-member sizes, so checking it catches the standard bomb (the classic
    42.zip style: 1000x+ ratio). Three limits:
    1. decompressed/compressed ratio per member (legit XML compresses at
       most a few dozen times; bombs use 1000x+),
    2. absolute decompressed size per member,
    3. total decompressed size of the archive.

    One more check for the crafted-archive trick of declaring size 0 in the
    directory while carrying a huge real stream: when the directory claims
    nothing at all but the archive itself is substantial, the sizes are
    lying, so verify against the real (bounded) stream.
    """
    with zipfile.ZipFile(zip_path) as zf:
        total_c = 0
        total_u = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            c = info.compress_size or 0
            u = info.file_size or 0
            total_c += c
            total_u += u
            if c > 0 and u > MAX_ZIP_DECOMPRESSION_RATIO * c:
                raise ValueError(
                    f"Rejected: member {info.filename!r} decompresses at "
                    f"{u / c:.0f}x, above the {MAX_ZIP_DECOMPRESSION_RATIO}x "
                    "limit (possible zip bomb)"
                )
            if u > MAX_ZIP_DECOMPRESSED_BYTES:
                raise ValueError(
                    f"Rejected: member {info.filename!r} is larger than "
                    f"{MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB "
                    "decompressed (possible zip bomb)"
                )
        if total_u > MAX_ZIP_DECOMPRESSED_BYTES:
            raise ValueError(
                "Rejected: archive decompresses to more than "
                f"{MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB "
                "(possible zip bomb)"
            )
        # A member whose declared size is smaller than its compressed size is
        # impossible for real data (deflate output is at least as large as the
        # input, for anything of size) - the directory is lying, which is the
        # crafted-bomb trick of declaring size 0 and hiding the data. Verify
        # the real sizes by streaming, bounded by the hard cap so the check
        # itself cannot blow up.
        suspicious = [
            i for i in zf.infolist()
            if not i.is_dir()
            and (i.compress_size or 0) > 0
            and (i.file_size or 0) < (i.compress_size or 0)
        ]
        if suspicious:
            real = 0
            for info in suspicious:
                fh = zf.open(info, "r")
                # Read in FIXED-SIZE chunks, never read(-1): ZipExtFile.read(-1)
                # accumulates the ENTIRE remaining stream into one bytes object
                # (buf += self._read1(...) until EOF), so the size check below
                # would only run after a multi-GB bomb had already been
                # allocated - turning this guard itself into the OOM it is
                # meant to prevent. Chunked reads bound memory to one chunk;
                # the cap fires mid-stream, however big the member really is.
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    real += len(chunk)
                    if real > MAX_ZIP_DECOMPRESSED_BYTES:
                        fh.close()
                        raise ValueError(
                            "Rejected: archive decompresses to more than "
                            f"{MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB "
                            "(possible zip bomb)"
                        )
                fh.close()


def run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine from sync Celery task context.

    Handles three cases:
    1. No event loop (typical Celery prefork worker) - uses asyncio.run().
    2. Closed event loop - uses asyncio.run().
    3. Running event loop (rare, nested) - spawns a new thread with asyncio.run().
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
