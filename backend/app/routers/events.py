import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EventPayload, EventResponse
from app.redis_client import get_redis, publish_event
from app.schemas import Event

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/events", status_code=status.HTTP_201_CREATED, response_model=EventResponse)
async def create_event(
    payload: EventPayload,
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    """Receive and store a detection event, then broadcast it via Redis."""
    try:
        event = Event(
            camera_id=payload.camera_id,
            event_type=payload.event_type,
            severity=payload.severity,
            confidence=payload.confidence,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)

        # Build response dict and publish to Redis
        event_response = EventResponse(
            id=event.id,
            camera_id=event.camera_id,
            event_type=event.event_type,
            severity=event.severity,
            confidence=event.confidence,
            timestamp=event.timestamp,
        )

        # Serialize to dict for Redis (timestamp as ISO string)
        event_dict = event_response.model_dump(mode="json")

        redis_conn = await get_redis()
        if redis_conn:
            try:
                await publish_event(redis_conn, event_dict)
            except Exception as pub_err:
                logger.warning("Failed to publish event to Redis: %s", pub_err)
        else:
            logger.warning("Redis not available, event only stored in PostgreSQL")

        return event_response

    except Exception as exc:
        logger.error("Failed to create event: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not store event",
        ) from exc
