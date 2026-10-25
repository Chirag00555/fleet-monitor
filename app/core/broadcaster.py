from typing import Set, Dict, Any, Optional
import asyncio
import json
import logging

logger = logging.getLogger("broadcaster")

class Broadcaster:
    """
    In-memory async broadcast hub for dispatching live Server-Sent Events (SSE).
    - Maintains a set of subscriber asyncio.Queue instances.
    - Each client queue is bounded to prevent slow/stalled browser clients from buffering indefinitely.
    - If a client queue overflows, older messages are dropped or the connection is safely handled.
    - Clean subscription and unsubscription prevents memory leaks.
    - Publishes event_received, incident_created, incident_updated, and device_status updates.
    """
    def __init__(self, max_queue_size: int = 100):
        self.subscribers: Set[asyncio.Queue] = set()
        self.max_queue_size = max_queue_size

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.subscribers.add(q)
        logger.info(f"Client connected to SSE broadcaster. Total active: {len(self.subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)
        logger.info(f"Client disconnected from SSE broadcaster. Total active: {len(self.subscribers)}")

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Dispatch an event to all connected subscriber queues non-blockingly.
        """
        payload = {
            "type": event_type,
            "data": data
        }
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue is full; dropping oldest message for slow consumer.")
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass

broadcaster = Broadcaster()
