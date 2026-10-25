from datetime import datetime, timezone
from typing import List, Tuple
import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import Device, Event, WorkItem
from app.schemas.events import EventIngestItem, IngestStatusDetail
from app.core.broadcaster import broadcaster

logger = logging.getLogger("ingest")

async def ingest_events_batch(
    session: AsyncSession,
    items: List[EventIngestItem]
) -> Tuple[int, int, List[IngestStatusDetail]]:
    """
    Ingest a batch of events within a single database transaction.
    - Validates device registration and enabled status.
    - Idempotently handles duplicates using DB constraint (device_id, event_id).
    - Inserts corresponding WorkItem for newly accepted events atomically.
    - Updates device latest_report_time monotonically (GREATEST) and server_last_received_at.
    - Dispatches SSE event notifications for accepted events post-commit.
    """
    if not items:
        return 0, 0, []

    # 1. Verify device registration
    device_ids = {item.device_id for item in items}
    devices_query = await session.execute(
        select(Device).where(Device.id.in_(device_ids))
    )
    existing_devices = {d.id: d for d in devices_query.scalars().all()}

    # Check for unknown or disabled devices
    for dev_id in device_ids:
        if dev_id not in existing_devices:
            raise ValueError(f"Unregistered device '{dev_id}'. Registration required before sending telemetry.")
        if not existing_devices[dev_id].is_enabled:
            raise ValueError(f"Device '{dev_id}' is disabled.")

    now_server = datetime.now(timezone.utc)
    accepted_count = 0
    duplicate_count = 0
    details: List[IngestStatusDetail] = []
    accepted_events_data = []

    try:
        # Process items in order
        for item in items:
            existing_event_query = await session.execute(
                select(Event.id).where(
                    and_(Event.device_id == item.device_id, Event.event_id == item.event_id)
                )
            )
            existing_event_id = existing_event_query.scalar_one_or_none()

            if existing_event_id is not None:
                duplicate_count += 1
                details.append(IngestStatusDetail(
                    event_id=item.event_id,
                    status="duplicate",
                    message="Event already accepted previously."
                ))
                continue

            # Create new event record
            new_event = Event(
                device_id=item.device_id,
                event_id=item.event_id,
                event_type=item.event_type,
                event_time=item.event_time,
                received_at=now_server,
                payload=item.payload,
                processing_status="pending",
            )
            session.add(new_event)
            await session.flush() # populate new_event.id

            # Insert durable work item in the same transaction
            work_item = WorkItem(
                event_pk=new_event.id,
                status="pending",
                attempts=0,
                available_at=now_server,
            )
            session.add(work_item)

            # Update device latest_report_time monotonically
            device = existing_devices[item.device_id]
            if device.latest_report_time is None or item.event_time > device.latest_report_time:
                device.latest_report_time = item.event_time
            device.server_last_received_at = now_server

            accepted_count += 1
            details.append(IngestStatusDetail(
                event_id=item.event_id,
                status="accepted",
                message="Event accepted and queued for durable processing."
            ))

            accepted_events_data.append({
                "id": new_event.id,
                "event_id": new_event.event_id,
                "device_id": new_event.device_id,
                "event_type": new_event.event_type,
                "event_time": new_event.event_time.isoformat(),
                "received_at": new_event.received_at.isoformat(),
                "payload": new_event.payload,
            })

        await session.commit()

        # Non-blocking broadcast of accepted events
        for evt in accepted_events_data:
            await broadcaster.broadcast("event_received", evt)

        return accepted_count, duplicate_count, details
    except Exception as e:
        await session.rollback()
        raise e
