"""WebSocket progress notifications for async document processing."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import get_db

router = APIRouter(tags=["websocket"])

# A user can have multiple live connections at once (a second browser tab, a
# second device), so this fans out per user_id rather than keeping one slot
# per user - a single-slot registry meant opening a second tab silently
# evicted the first connection's progress updates. Each connection also gets
# its own opaque id so disconnects only ever remove that one socket.
active_connections: dict[str, dict[int, WebSocket]] = {}
_next_connection_id = 0


def _extract_token(websocket: WebSocket) -> str | None:
    """Extract auth token from WebSocket connection.

    Prefers the subprotocol header (``Sec-WebSocket-Protocol: token <jwt>``)
    which does not leak into server/proxy access logs. Falls back to the
    query-parameter method for backward compat with older clients.
    """
    # Subprotocol auth: "token <jwt>"
    proto = websocket.headers.get("sec-websocket-protocol", "")
    for p in proto.split(","):
        p = p.strip()
        if p.startswith("token "):
            return p[6:].strip()

    # Query-param auth (legacy - query params end up in access logs)
    return websocket.query_params.get("token")


@router.websocket("/ws/progress")
async def progress_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time document processing progress."""
    global _next_connection_id

    auth_token = _extract_token(websocket)
    if not auth_token:
        await websocket.close(code=4001, reason="Missing auth token")
        return

    from app.services.auth_service import get_current_user

    # get_db() is a generator dependency normally driven by FastAPI's
    # Depends() machinery, which closes it after the request. Called
    # directly with next() like this, nothing ever drives it to
    # completion, so the session (and its pooled connection) was never
    # released - a leak of one pool connection per WS connect/reconnect.
    # Managing it as a context-managed generator here ensures close()
    # always runs, including on early-return auth failures below.
    db_gen = get_db()
    db = next(db_gen)
    try:
        user = get_current_user(db, auth_token)
    finally:
        db_gen.close()

    if not user:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    client_id = f"user_{user.id}"
    connection_id = _next_connection_id
    _next_connection_id += 1
    active_connections.setdefault(client_id, {})[connection_id] = websocket

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        conns = active_connections.get(client_id)
        if conns is not None:
            conns.pop(connection_id, None)
            if not conns:
                active_connections.pop(client_id, None)


async def send_progress(client_id: str, data: dict):
    """Send a progress update to every live connection for a user (all of
    their open tabs/devices), not just one."""
    conns = active_connections.get(client_id)
    if not conns:
        return
    stale = []
    for connection_id, ws in list(conns.items()):
        try:
            await ws.send_json(data)
        except Exception:
            stale.append(connection_id)
    for connection_id in stale:
        conns.pop(connection_id, None)
    if not conns:
        active_connections.pop(client_id, None)


def get_client_id(user_id: int) -> str:
    """Get WebSocket client ID for user."""
    return f"user_{user_id}"
