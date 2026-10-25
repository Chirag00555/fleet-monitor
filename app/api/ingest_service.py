from datetime import datetime, timezone
from typing import List, Tuple, Optional
import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import Device, Event, WorkItem
from app.schemas.events import EventIngestItem, IngestStatusDetail
from app.core.broadcaster import broadcaster

logger = logging.getLogger("ingest")

class DeviceAuthenticationError(Exception):
    pass

async def ingest_events_batch(
    session: AsyncSession,
    items: List[EventIngestItem],
    device_token: Optional[str] = None
) -> Tuple[int, int, List[IngestStatusDetail]]:
    """
    Ingest a batch of events within a single database transaction.
    - Validates device registration, enabled status, and token-gated auth when configured.
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

    # Check for unknown or disabled devices, and validate token
    for dev_id in device_ids:
        if dev_id not in existing_devices:
            raise ValueError(f"Unregistered device '{dev_id}'. Registration required before sending telemetry.")
        
        dev = existing_devices[dev_id]
        if not dev.is_enabled:
            raise ValueError(f"Device '{dev_id}' is disabled.")
            
        # Token validation: If device has an api_token configured, request must supply matching token
        if dev.api_token:
            if not device_token or device_token != dev.api_token:
                raise DeviceAuthenticationError(f"Invalid or missing device API token for device '{dev_id}'.")

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
                    message="Event already ingested"
                ))
                continue

            event_record = Event(
                device_id=item.device_id,
                event_id=item.event_id,
                event_type=item.event_type,
                event_time=item.event_time,
                received_at=now_server,
                payload=item.payload,
                processing_status="pending",
            )
            session.add(event_record)
            await session.flush()

            work_item = WorkItem(
                event_pk=event_record.id,
                status="pending",
                attempts=0,
                available_at=now_server,
            )
            session.add(work_item)

            dev = existing_devices[item.device_id]
            if dev.latest_report_time is None or item.event_time > dev.latest_report_time:
                dev.latest_report_time = item.event_time
            dev.server_last_received_at = now_server

            accepted_count += 1
            details.append(IngestStatusDetail(
                event_id=item.event_id,
                status="accepted",
                message=None
            ))

            accepted_events_data.append({
                "id": event_record.id,
                "event_id": item.event_id,
                "device_id": item.device_id,
                "event_type": item.event_type,
                "event_time": item.event_time.isoformat(),
                "received_at": now_server.isoformat(),
                "payload": item.payload,
                "processing_status": "pending",
            })

        await session.commit()

    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to ingest event batch: {e}", exc_info=True)
        raise

    for event_data in accepted_events_data:
        try:
            await broadcaster.broadcast("event_received", event_data)
        except Exception as e:
            logger.warning(f"Failed to broadcast event_received SSE for {event_data['event_id']}: {e}")

    return accepted_count, duplicate_count, details
