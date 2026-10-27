import os
import threading
from typing import Optional
from loguru import logger
import redis
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TTSCache

SESSION_TTL_SECONDS = 300
CHUNK_HOLD_TTL_SECONDS = 600
ACTIVE_COUNTER_TTL_SECONDS = 12000

_redis_client_lock = threading.Lock()
_cached_redis_client: Optional[redis.Redis] = None
_testing_redis_client: Optional[object] = None

LUA_REGISTER_COUNTER = """
local target_key = KEYS[1]
local ttl_seconds = tonumber(ARGV[1])
local current_val = redis.call('INCR', target_key)
redis.call('EXPIRE', target_key, ttl_seconds)
return current_val
"""

LUA_UNREGISTER_COUNTER = """
local target_key = KEYS[1]
local current_val = redis.call('DECR', target_key)
if current_val <= 0 then
    redis.call('DEL', target_key)
    return 0
end
return current_val
"""

LUA_CLAIM_SESSION_CHUNK = """
local user_key = KEYS[1]
local session_chunks_key = KEYS[2]
local chunk_sessions_key = KEYS[3]

local target_user_id = ARGV[1]
local active_session_id = ARGV[2]
local target_cache_id = ARGV[3]
local session_ttl = tonumber(ARGV[4])
local chunk_ttl = tonumber(ARGV[5])

if redis.call('EXISTS', 'tts:closed:' .. active_session_id) == 1 then
    return -2
end

local existing_user = redis.call('GET', user_key)
if existing_user and existing_user ~= target_user_id then
    return -1
end

redis.call('SET', user_key, target_user_id, 'EX', session_ttl)
redis.call('HINCRBY', session_chunks_key, target_cache_id, 1)
redis.call('EXPIRE', session_chunks_key, session_ttl)
redis.call('SADD', chunk_sessions_key, active_session_id)
redis.call('EXPIRE', chunk_sessions_key, chunk_ttl)
return 1
"""

LUA_RENEW_SESSION = """
local user_key = KEYS[1]
local session_chunks_key = KEYS[2]
local target_user_id = ARGV[1]
local active_session_id = ARGV[2]
local session_ttl = tonumber(ARGV[3])
local chunk_ttl = tonumber(ARGV[4])

local existing_user = redis.call('GET', user_key)
if not existing_user or existing_user ~= target_user_id then
    return 0
end

redis.call('EXPIRE', user_key, session_ttl)
redis.call('EXPIRE', session_chunks_key, session_ttl)

local held_chunks = redis.call('HKEYS', session_chunks_key)
for _, chunk_id_raw in ipairs(held_chunks) do
    local chunk_key = 'tts:chunk_sessions:' .. chunk_id_raw
    redis.call('EXPIRE', chunk_key, chunk_ttl)
end
return 1
"""

LUA_RELEASE_SESSION_CHUNKS = """
local user_key = KEYS[1]
local session_chunks_key = KEYS[2]

local target_user_id = ARGV[1]
local active_session_id = ARGV[2]
local all_session_flag = ARGV[3]
local cache_id_count = tonumber(ARGV[4])
local closed_session_ttl = tonumber(ARGV[5])

local existing_user = redis.call('GET', user_key)
if existing_user and existing_user ~= target_user_id then
    return { -1 }
end

local target_ids = {}
if all_session_flag == "1" then
    local held = redis.call('HKEYS', session_chunks_key)
    for _, item in ipairs(held) do
        table.insert(target_ids, item)
    end
    for index = 1, cache_id_count do
        table.insert(target_ids, ARGV[5 + index])
    end
    redis.call('DEL', session_chunks_key)
    redis.call('DEL', user_key)
    redis.call('SET', 'tts:closed:' .. active_session_id, '1', 'EX', closed_session_ttl)
else
    for index = 1, cache_id_count do
        local cid = ARGV[5 + index]
        local references = tonumber(redis.call('HGET', session_chunks_key, cid) or '0')
        if references > 1 then
            redis.call('HINCRBY', session_chunks_key, cid, -1)
        elseif references == 1 then
            redis.call('HDEL', session_chunks_key, cid)
            table.insert(target_ids, cid)
        else
            table.insert(target_ids, cid)
        end
    end
    if redis.call('HLEN', session_chunks_key) == 0 then
        redis.call('DEL', session_chunks_key)
        redis.call('DEL', user_key)
    end
end

local safe_to_delete = {}
for _, cid in ipairs(target_ids) do
    local chunk_key = 'tts:chunk_sessions:' .. cid
    redis.call('SREM', chunk_key, active_session_id)
    for _, owner in ipairs(redis.call('SMEMBERS', chunk_key)) do
        if redis.call('HEXISTS', 'tts:session_chunks:' .. owner, cid) == 0 then
            redis.call('SREM', chunk_key, owner)
        end
    end
    local remaining_sessions = redis.call('SCARD', chunk_key)
    local reader_count = tonumber(redis.call('GET', 'tts:reader:' .. cid) or "0")
    if remaining_sessions == 0 and reader_count <= 0 then
        table.insert(safe_to_delete, tonumber(cid))
    end
end

return safe_to_delete
"""


def set_redis_client_for_testing(test_client: Optional[object]) -> None:
    global _testing_redis_client
    _testing_redis_client = test_client


def get_redis_client() -> Optional[redis.Redis]:
    global _cached_redis_client
    if _testing_redis_client is not None:
        return _testing_redis_client

    if _cached_redis_client is not None:
        return _cached_redis_client

    with _redis_client_lock:
        if _cached_redis_client is not None:
            return _cached_redis_client
        configured_url = settings.celery_broker_url
        if not configured_url:
            return None
        try:
            client_instance = redis.from_url(
                configured_url,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
            client_instance.ping()
            _cached_redis_client = client_instance
            return _cached_redis_client
        except Exception as connection_error:
            logger.warning(f"Failed connecting to configured Redis broker: {connection_error}")
            return None


def register_producer(user_id: int, text_hash: str) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    producer_key = f"tts:producer:{user_id}:{text_hash}"
    try:
        redis_client.eval(LUA_REGISTER_COUNTER, 1, producer_key, ACTIVE_COUNTER_TTL_SECONDS)
    except Exception as redis_error:
        logger.warning(f"register_producer failed in Redis: {redis_error}")


def unregister_producer(user_id: int, text_hash: str) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    producer_key = f"tts:producer:{user_id}:{text_hash}"
    try:
        redis_client.eval(LUA_UNREGISTER_COUNTER, 1, producer_key)
    except Exception as redis_error:
        logger.warning(f"unregister_producer failed in Redis: {redis_error}")


def is_producing(user_id: int, text_hash: str) -> bool:
    redis_client = get_redis_client()
    if not redis_client:
        return True
    producer_key = f"tts:producer:{user_id}:{text_hash}"
    try:
        raw_value = redis_client.get(producer_key)
        return bool(raw_value and int(raw_value) > 0)
    except Exception as redis_error:
        logger.warning(f"is_producing failed in Redis: {redis_error}")
        return True


def register_reader(cache_id: int) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    reader_key = f"tts:reader:{cache_id}"
    try:
        redis_client.eval(LUA_REGISTER_COUNTER, 1, reader_key, ACTIVE_COUNTER_TTL_SECONDS)
    except Exception as redis_error:
        logger.warning(f"register_reader failed in Redis: {redis_error}")


def unregister_reader(cache_id: int) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    reader_key = f"tts:reader:{cache_id}"
    try:
        redis_client.eval(LUA_UNREGISTER_COUNTER, 1, reader_key)
    except Exception as redis_error:
        logger.warning(f"unregister_reader failed in Redis: {redis_error}")


def is_reading(cache_id: int) -> bool:
    redis_client = get_redis_client()
    if not redis_client:
        return True
    reader_key = f"tts:reader:{cache_id}"
    try:
        raw_value = redis_client.get(reader_key)
        return bool(raw_value and int(raw_value) > 0)
    except Exception as redis_error:
        logger.warning(f"is_reading failed in Redis: {redis_error}")
        return True


def claim_session_chunk(user_id: int, session_id: str, cache_id: int) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    user_key = f"tts:session_user:{session_id}"
    session_chunks_key = f"tts:session_chunks:{session_id}"
    chunk_sessions_key = f"tts:chunk_sessions:{cache_id}"
    try:
        eval_result = redis_client.eval(
            LUA_CLAIM_SESSION_CHUNK,
            3,
            user_key,
            session_chunks_key,
            chunk_sessions_key,
            str(user_id),
            session_id,
            str(cache_id),
            SESSION_TTL_SECONDS,
            CHUNK_HOLD_TTL_SECONDS,
        )
        if eval_result == -1:
            raise PermissionError("Session user verification failed")
        if eval_result == -2:
            raise ValueError("Reading session has ended")
    except (PermissionError, ValueError):
        raise
    except Exception as redis_error:
        logger.warning(f"claim_session_chunk failed in Redis: {redis_error}")


def is_chunk_claimed_by_session(session_id: str, cache_id: int) -> bool:
    redis_client = get_redis_client()
    if not redis_client:
        return False
    session_chunks_key = f"tts:session_chunks:{session_id}"
    try:
        return bool(redis_client.hexists(session_chunks_key, str(cache_id)))
    except Exception as redis_error:
        logger.warning(f"is_chunk_claimed_by_session failed in Redis: {redis_error}")
        return False


def heartbeat_session(user_id: int, session_id: str) -> bool:
    redis_client = get_redis_client()
    if not redis_client:
        return False
    user_key = f"tts:session_user:{session_id}"
    session_chunks_key = f"tts:session_chunks:{session_id}"
    try:
        eval_result = redis_client.eval(
            LUA_RENEW_SESSION,
            2,
            user_key,
            session_chunks_key,
            str(user_id),
            session_id,
            SESSION_TTL_SECONDS,
            CHUNK_HOLD_TTL_SECONDS,
        )
        return bool(eval_result and eval_result == 1)
    except Exception as redis_error:
        logger.warning(f"heartbeat_session failed in Redis: {redis_error}")
        return False


def safe_unlink_audio(audio_path: Optional[str]) -> bool:
    if not audio_path:
        return False
    real_cache_dir = os.path.realpath(os.path.join(settings.data_dir, "tts-cache"))
    real_target_path = os.path.realpath(audio_path)
    if not real_target_path.startswith(real_cache_dir + os.sep):
        return False
    if os.path.exists(real_target_path):
        try:
            os.remove(real_target_path)
            return True
        except OSError as unlink_error:
            logger.warning(f"Failed unlinking audio file {real_target_path}: {unlink_error}")
            return False
    return False


def release_session_chunks(
    db_session: Session,
    user_id: int,
    session_id: Optional[str] = None,
    cache_ids: Optional[list[int]] = None,
    release_all_session: bool = False,
) -> tuple[int, int]:
    redis_client = get_redis_client()
    if not redis_client:
        return 0, 0

    if not session_id:
        return 0, 0

    user_key = f"tts:session_user:{session_id}"
    session_chunks_key = f"tts:session_chunks:{session_id}"
    cache_ids_to_pass = [str(cid) for cid in (cache_ids or [])[:256] if cid > 0]

    try:
        eval_result = redis_client.eval(
            LUA_RELEASE_SESSION_CHUNKS,
            2,
            user_key,
            session_chunks_key,
            str(user_id),
            session_id,
            "1" if release_all_session else "0",
            len(cache_ids_to_pass),
            ACTIVE_COUNTER_TTL_SECONDS,
            *cache_ids_to_pass,
        )
    except Exception as redis_error:
        logger.warning(f"release_session_chunks failed in Redis: {redis_error}")
        return 0, 0

    if isinstance(eval_result, list) and len(eval_result) == 1 and eval_result[0] == -1:
        raise PermissionError("Session user verification failed")

    safe_ids: list[int] = [int(item) for item in (eval_result or []) if item is not None and item >= 0]
    released_count = len(cache_ids_to_pass) if not release_all_session else len(safe_ids)
    deleted_count = 0

    if not safe_ids:
        return released_count, deleted_count

    rows_to_delete = (
        db_session.query(TTSCache)
        .filter(
            TTSCache.id.in_(safe_ids),
            TTSCache.user_id == user_id,
            TTSCache.summary_id.is_(None),
        )
        .populate_existing()
        .with_for_update()
        .all()
    )

    unlink_paths: list[str] = []
    for cache_row in rows_to_delete:
        if is_chunk_protected(cache_row.id, user_id, cache_row.text_hash):
            continue

        if cache_row.audio_path:
            unlink_paths.append(cache_row.audio_path)
        db_session.delete(cache_row)
        deleted_count += 1

    db_session.commit()
    for target_path in unlink_paths:
        safe_unlink_audio(target_path)

    return released_count, deleted_count


def cleanup_aborted_or_abandoned_stream(
    db_session: Session,
    user_id: int,
    text_hash: str,
    cache_id: int,
    tmp_path: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if session_id:
        release_session_chunks(db_session, user_id, session_id, [cache_id])

    cache_row = (
        db_session.query(TTSCache)
        .filter(
            TTSCache.id == cache_id,
            TTSCache.user_id == user_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )

    if not cache_row:
        db_session.commit()
        return True

    if cache_row.summary_id is not None:
        db_session.commit()
        return False

    if not is_chunk_protected(cache_id, user_id, text_hash):
        audio_path_to_delete = cache_row.audio_path
        db_session.delete(cache_row)
        db_session.commit()
        safe_unlink_audio(audio_path_to_delete)
        return True

    db_session.commit()
    return False


def is_chunk_protected(cache_id: int, user_id: int, text_hash: str) -> bool:
    if is_reading(cache_id) or is_producing(user_id, text_hash):
        return True
    redis_client = get_redis_client()
    if not redis_client:
        return True
    try:
        chunk_sessions_key = f"tts:chunk_sessions:{cache_id}"
        for owner_session in redis_client.smembers(chunk_sessions_key):
            if redis_client.hexists(f"tts:session_chunks:{owner_session}", str(cache_id)):
                return True
            redis_client.srem(chunk_sessions_key, owner_session)
        return False
    except Exception as redis_error:
        logger.warning(f"is_chunk_protected failed in Redis: {redis_error}")
        return True
