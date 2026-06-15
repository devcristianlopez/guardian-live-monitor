import logging
from typing import List

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected (%d active)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)
        logger.info("WebSocket client disconnected (%d active)", len(self.active_connections))

    async def broadcast(self, message: str) -> None:
        """Send a text message to every connected client.

        Iterates over a copy of the list so removals during iteration
        do not cause issues. Disconnected clients are cleaned up.
        """
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                self.disconnect(connection)


manager = ConnectionManager()
