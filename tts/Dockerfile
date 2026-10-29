FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    libsndfile1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# onnxruntime-gpu, not plain onnxruntime: lets Kokoro use CUDA when the host
# has GPU passthrough configured (see docker-compose.yml comment near the
# "tts" service for the one-time host setup this requires), while still
# running CPU-only exactly like plain onnxruntime does when no GPU is
# visible - kokoro-onnx's own Kokoro.__init__ already picks
# rt.get_available_providers() over a hardcoded CPU list whenever
# onnxruntime-gpu is the installed package (see main.py's preload_dlls call
# for the other half of this).
#
# kokoro-onnx unconditionally depends on plain "onnxruntime" (its "gpu"
# extra only adds onnxruntime-gpu on top, it doesn't replace the base dep),
# and both packages ship the same importable "onnxruntime" top-level module
# - having both installed leaves it to install order which one's files win
# in site-packages. Avoid that ambiguity: install kokoro-onnx first (pulling
# in plain onnxruntime transitively), uninstall plain onnxruntime, then
# install onnxruntime-gpu clean so there's exactly one "onnxruntime" on disk.
#
# Pinned to 1.26.0, the last release built against CUDA 12.x/cuDNN 9 by
# default (1.27+ switched the PyPI default to CUDA 13.0) - CUDA 12.x is the
# better-proven pip-only (no CUDA base image) target and this host's driver
# (CUDA 13.2 max supported) runs CUDA 12.x binaries fine via driver back-
# compat. The nvidia-*-cu12 packages below are the shared libraries
# onnxruntime.preload_dlls() (called in main.py) loads explicitly at
# startup - onnxruntime-gpu wheels dynamically link CUDA/cuDNN and silently
# lose CUDAExecutionProvider if those libs aren't found via LD_LIBRARY_PATH,
# even when the package itself installed fine (microsoft/onnxruntime
# #21354, #23736, #21825).
RUN pip install --no-cache-dir fastapi uvicorn piper-tts kokoro-onnx soundfile \
    && pip uninstall -y onnxruntime \
    && pip install --no-cache-dir \
        onnxruntime-gpu==1.26.0 \
        nvidia-cudnn-cu12 \
        nvidia-cublas-cu12 \
        nvidia-cuda-nvrtc-cu12 \
        nvidia-cuda-runtime-cu12 \
        nvidia-curand-cu12 \
        nvidia-cufft-cu12

WORKDIR /app

RUN mkdir -p /models

# Kokoro-82M: a much more natural/expressive neural TTS than Piper, still
# ONNX/CPU-viable. Using the unquantized f32 build (~310MB), NOT int8
# (~88MB) despite int8 being the usual CPU-speed choice - measured on this
# deployment's CPU (no AVX-512-VNNI), int8 was 24-33s per short sentence
# vs f32's 4-8s. ONNX Runtime's int8 GEMM kernels need VNNI to be fast;
# without it they fall back to a slow dequantize path that's SLOWER than
# just running f32 directly. Re-benchmark before switching back to int8
# on different hardware.
RUN wget -q -O /models/kokoro-v1.0.onnx \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
    && wget -q -O /models/voices-v1.0.bin \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
    || echo "Kokoro model download failed, will need manual setup"

# "default" narration voice: libritts_r-medium reads noticeably more natural
# than the lessac/ryan "medium" voices for long-form page/section narration.
RUN wget -q -O /models/en_US-libritts_r-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx \
    && wget -q -O /models/en_US-libritts_r-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json \
    || echo "Model download failed, will need manual setup"

# "host_a" (male) / "host_b" (female) for podcast dialogues, upgraded to the
# "high" quality tier - noticeably less robotic than "medium", at the cost of
# slower synthesis.
RUN wget -q -O /models/en_US-ryan-high.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/high/en_US-ryan-high.onnx \
    && wget -q -O /models/en_US-ryan-high.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/high/en_US-ryan-high.onnx.json \
    || echo "Model download failed, will need manual setup"

RUN wget -q -O /models/en_US-lessac-high.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/high/en_US-lessac-high.onnx \
    && wget -q -O /models/en_US-lessac-high.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/high/en_US-lessac-high.onnx.json \
    || echo "Model download failed, will need manual setup"

# Kept for backward compatibility with any cached direct-name references.
RUN wget -q -O /models/en_US-lessac-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
    && wget -q -O /models/en_US-lessac-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json \
    || echo "Model download failed, will need manual setup"

RUN wget -q -O /models/en_US-ryan-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/medium/en_US-ryan-medium.onnx \
    && wget -q -O /models/en_US-ryan-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json \
    || echo "Model download failed, will need manual setup"

# Copy application code last so model download layers stay cached when
# main.py changes (models take the bulk of build time on a fresh image).
COPY main.py piper_pool.py ./

EXPOSE 5111
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5111"]
