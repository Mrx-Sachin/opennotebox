"""LLM call instrumentation for the chat engine.

Wraps the chat engine's key methods to record latency, token counts, error
rates, and active-call gauges without modifying the engine itself.
"""
import time
from typing import Any, Dict, Optional


from app.services.metrics import get_metrics


async def track_llm_call(
    fn, *args, call_type: str = "chat", user_id: Optional[int] = None, **kwargs
):
    """Run an async LLM call wrapped in metrics instrumentation.

    Usage in chat_engine:
        result = await track_llm_call(
            self._model(user_id).ainvoke, messages, call_type="answer", user_id=user_id
        )
    """
    m = get_metrics()
    m.llm_requests.increment()
    m.llm_active.set(m.llm_active.value + 1)

    start = time.perf_counter()
    success = False
    try:
        result = await fn(*args, **kwargs)
        success = True
        return result
    except Exception:
        m.llm_errors.increment()
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        m.llm_active.set(m.llm_active.value - 1)
        m.llm_latency.observe(elapsed_ms)

        m.llm_recent.push({
            "type": call_type,
            "user_id": user_id,
            "duration_ms": round(elapsed_ms, 1),
            "success": success,
        })


def record_tokens(input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Record token counts from a completed LLM call."""
    m = get_metrics()
    if input_tokens:
        m.llm_tokens_in.increment(input_tokens)
    if output_tokens:
        m.llm_tokens_out.increment(output_tokens)


def check_llm_health(
    base_url: str,
    model: str = "",
    api_key: str = "ollama",
    max_models: int = 200,
) -> Dict[str, Any]:
    """Probe an LLM endpoint to check reachability.

    Returns a dict with status, latency, and model info.
    """
    import httpx

    del model  # probe lists models; the configured id is unused
    start = time.perf_counter()
    try:
        clean_url = base_url.rstrip("/")
        if clean_url.endswith("/v1"):
            probe_url = f"{clean_url}/models"
        elif clean_url.endswith("/models"):
            probe_url = clean_url
        else:
            probe_url = f"{clean_url}/v1/models"

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            resp = httpx.get(probe_url, headers=headers, timeout=5.0)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            fallback_url = None
            if "host.docker.internal" in probe_url:
                fallback_url = probe_url.replace("host.docker.internal", "127.0.0.1")
            elif "127.0.0.1" in probe_url:
                fallback_url = probe_url.replace("127.0.0.1", "host.docker.internal")
            elif "localhost" in probe_url:
                fallback_url = probe_url.replace("localhost", "host.docker.internal")
            if not fallback_url:
                raise
            resp = httpx.get(fallback_url, headers=headers, timeout=5.0)
        latency_ms = (time.perf_counter() - start) * 1000
        if resp.status_code == 200:
            data = resp.json()
            raw_models: list = []
            if isinstance(data, dict):
                raw_models = data.get("data")
                if raw_models is None:
                    raw_models = data.get("models", [])
            elif isinstance(data, list):
                raw_models = data

            available: list[str] = []
            seen: set[str] = set()
            for item in raw_models or []:
                model_id = None
                if isinstance(item, dict):
                    model_id = item.get("id") or item.get("name")
                elif isinstance(item, str):
                    model_id = item
                if not model_id:
                    continue
                s_id = str(model_id).strip()
                if s_id and s_id not in seen:
                    seen.add(s_id)
                    available.append(s_id)
                    if len(available) >= max_models:
                        break

            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 1),
                "base_url": base_url,
                "available_models": available,
            }
        return {
            "status": "degraded",
            "http_status": resp.status_code,
            "latency_ms": round(latency_ms, 1),
            "base_url": base_url,
        }
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round(latency_ms, 1),
            "base_url": base_url,
        }
