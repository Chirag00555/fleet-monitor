import asyncio
import logging
import socket
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import async_session_factory
from app.models.entities import WorkItem, Event, Device
from app.worker.rules_engine import evaluate_event_rules, evaluate_device_reconnect, run_device_silence_check
from app.core.config import settings

logger = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

WORKER_ID = f"worker-{socket.gethostname()}"

async def claim_next_work_items(
    session: AsyncSession,
    batch_size: int = 10,
    lock_timeout_minutes: int = 5
) -> List[WorkItem]:
    """
    Claim available work items using safe concurrency.
    In PostgreSQL, FOR UPDATE SKIP LOCKED is used to prevent workers from contending or blocking.
    """
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(minutes=lock_timeout_minutes)

    query = (
        select(WorkItem)
        .options(selectinload(WorkItem.event))
        .where(
            or_(
                and_(WorkItem.status == "pending", WorkItem.available_at <= now),
                and_(WorkItem.status == "processing", WorkItem.locked_at < stale_threshold)
            )
        )
        .order_by(WorkItem.available_at.asc())
        .limit(batch_size)
    )

    try:
        query = query.with_for_update(skip_locked=True)
    except Exception:
        pass

    result = await session.execute(query)
    items = list(result.scalars().all())

    for item in items:
        item.status = "processing"
        item.locked_at = now
        item.locked_by = WORKER_ID
        item.attempts += 1

    if items:
        await session.commit()

    return items


async def process_work_item(session: AsyncSession, item: WorkItem) -> bool:
    """
    Execute the work unit for an accepted event:
    1. Loads event asynchronously (avoids sync lazy loading).
    2. Evaluates enabled rules (temperature & event types).
    3. Handles device reconnect auto-resolution of active silence incidents.
    4. Marks event and work item as completed.
    """
    now = datetime.now(timezone.utc)
    item_id = item.id
    attempts = item.attempts
    max_attempts = item.max_attempts
    try:
        # Explicitly query Event asynchronously using event_pk to avoid MissingGreenlet lazy-load
        event = await session.get(Event, item.event_pk)
        if not event:
            raise ValueError(f"Event with pk {item.event_pk} not found for WorkItem {item_id}")

        # 1. Evaluate event-based rules
        await evaluate_event_rules(session, event)

        # 2. Check and auto-resolve any active silence incidents on reconnect
        device = await session.get(Device, event.device_id)
        if device:
            await evaluate_device_reconnect(session, device, event.event_time)

        # 3. Mark processed
        event.processing_status = "processed"
        item.status = "completed"
        item.updated_at = now
        await session.commit()
        return True

    except Exception as e:
        logger.error(f"Error processing work item {item_id}: {e}")
        await session.rollback()

        # Update item error, status, and retry schedule
        item.last_error = str(e)
        if attempts >= max_attempts:
            item.status = "failed"
            logger.warning(f"WorkItem {item_id} permanently failed after {attempts} attempts.")
        else:
            item.status = "pending"
            backoff_delay = min(300, 2 ** attempts)
            item.available_at = now + timedelta(seconds=backoff_delay)
            logger.info(f"WorkItem {item_id} scheduled for retry at {item.available_at} (attempt {attempts})")

        item.locked_at = None
        item.locked_by = None
        session.add(item)
        await session.commit()
        return False


async def run_worker_loop(poll_interval: float = 1.0, stop_event: Optional[asyncio.Event] = None):
    """
    Continuous background worker execution loop.
    Interleaves durable work-item consumption with periodic silence detection.
    """
    logger.info(f"Starting background worker ({WORKER_ID})...")
    last_silence_check = datetime.now(timezone.utc)

    while stop_event is None or not stop_event.is_set():
        try:
            # 1. Periodic Device Silence Check
            now = datetime.now(timezone.utc)
            if (now - last_silence_check).total_seconds() >= settings.SILENCE_CHECK_INTERVAL_SECONDS:
                async with async_session_factory() as silence_session:
                    try:
                        await run_device_silence_check(silence_session)
                        last_silence_check = now
                    except Exception as se:
                        logger.error(f"Error running silence check: {se}")

            # 2. Claim and process queued work items
            async with async_session_factory() as session:
                items = await claim_next_work_items(session, batch_size=10)
                if items:
                    logger.info(f"Claimed {len(items)} work items for processing.")
                    for item in items:
                        async with async_session_factory() as item_session:
                            fresh_item = await item_session.get(WorkItem, item.id)
                            if fresh_item:
                                await process_work_item(item_session, fresh_item)
                else:
                    await asyncio.sleep(poll_interval)
        except Exception as e:
            logger.error(f"Unexpected worker loop exception: {e}", exc_info=True)
            await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
