import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

# Singleton Redis connection used by both the worker and the API.
# Created once at startup, reused throughout the application lifetime.
_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Return the shared async Redis connection (create it on first call)."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
            socket_timeout=30,          # allow long idle on pubsub
            retry_on_timeout=True,
            health_check_interval=15,   # ping every 15s to keep alive
        )
        logger.info("Shared Redis connection created")
    return _redis


async def close_redis() -> None:
    """Close the shared Redis connection (called at shutdown)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Shared Redis connection closed")


async def publish_event(redis_client: redis.Redis, event_dict: dict[str, Any]) -> None:
    """Publish an event as JSON to the live_events channel."""
    payload = json.dumps(event_dict, default=str)
    await redis_client.publish("live_events", payload)
    logger.debug("Event published to live_events channel: %s", event_dict.get("id"))
