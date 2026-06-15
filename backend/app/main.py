import asyncio
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.config import settings
from app.database import init_db
from app.redis_client import get_redis, close_redis
from app.routers import events
from app.ws_manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Guardian Live Monitor API")

app.include_router(events.router, prefix="/api")

redis_worker_task: asyncio.Task | None = None


async def redis_worker() -> None:
    """Background task: listen to Redis live_events and broadcast via WebSocket."""
    retry_delay = 1

    while True:
        try:
            redis_conn = await get_redis()
            pubsub = redis_conn.pubsub()
            await pubsub.subscribe("live_events")
            logger.info("Redis worker subscribed to live_events channel")
            retry_delay = 1  # reset on successful connection

            async for message in pubsub.listen():
                if message["type"] == "message":
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    await manager.broadcast(raw)

        except asyncio.CancelledError:
            logger.info("Redis worker cancelled")
            break
        except Exception as exc:
            logger.error(
                "Redis worker error (retry in %ss): %s", retry_delay, exc,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)  # exponential backoff


@app.on_event("startup")
async def on_startup() -> None:
    """Initialise database tables and launch the Redis worker."""
    global redis_worker_task
    await init_db()
    # Pre-warm the shared Redis connection
    await get_redis()
    redis_worker_task = asyncio.create_task(redis_worker())
    logger.info("Application started")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Cancel background tasks and clean up."""
    global redis_worker_task
    if redis_worker_task and not redis_worker_task.done():
        redis_worker_task.cancel()
        try:
            await redis_worker_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    logger.info("Application shut down")


@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; incoming messages are ignored
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
        manager.disconnect(websocket)
