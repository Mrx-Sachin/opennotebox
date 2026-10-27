"""Ollama proxy router.

Forwards requests to the remote Ollama instance. Useful for the frontend to
test connections and list available models without needing direct access.
"""
from fastapi import APIRouter, Depends, HTTPException
import httpx

from app.config import settings
from app.routers.auth import get_current_user_from_token
from app.models import User

router = APIRouter(prefix="/api/ollama", tags=["ollama"])


@router.get("/models")
async def list_models(user: User = Depends(get_current_user_from_token)):
    """List available models from Ollama."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_base_url}/models")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat")
async def chat_proxy(body: dict, user: User = Depends(get_current_user_from_token)):
    """Proxy a chat completion request to Ollama."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/chat",
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))