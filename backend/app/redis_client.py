import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)


async def get_redis() -> redis.Redis:
    """Return an async Redis connection."""
    return redis.from_url(settings.redis_url, decode_responses=True)


async def publish_event(redis_client: redis.Redis, event_dict: dict[str, Any]) -> None:
    """Publish an event as JSON to the live_events channel."""
    payload = json.dumps(event_dict, default=str)
    await redis_client.publish("live_events", payload)
    logger.debug("Event published to live_events channel: %s", event_dict.get("id"))
