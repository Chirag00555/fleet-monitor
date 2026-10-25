import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from app.models.entities import Event, WorkItem, Device
from app.worker.processor import claim_next_work_items, process_work_item

@pytest.mark.asyncio
async def test_health_and_readiness(client):
    """Verify liveness /health and readiness /ready probes."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_single_event_ingestion(client, registered_device, db_session):
    """Verify valid single event ingestion creates Event and WorkItem in single transaction."""
    now_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "event_id": "evt-001",
        "device_id": registered_device.id,
        "event_time": now_utc,
        "event_type": "temperature",
        "payload": {"temperature": 75.4, "unit": "celsius"}
    }

    resp = await client.post("/api/events", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["event_id"] == "evt-001"
    assert data["status"] == "accepted"

    # Verify Event record created
    events = (await db_session.execute(select(Event))).scalars().all()
    assert len(events) == 1
    assert events[0].event_id == "evt-001"
    assert events[0].payload["temperature"] == 75.4

    # Verify WorkItem record created atomically
    work_items = (await db_session.execute(select(WorkItem))).scalars().all()
    assert len(work_items) == 1
    assert work_items[0].event_pk == events[0].id
    assert work_items[0].status == "pending"

    # Verify Device latest_report_time updated
    dev = await db_session.get(Device, registered_device.id)
    assert dev.latest_report_time is not None
    assert dev.server_last_received_at is not None


@pytest.mark.asyncio
async def test_batch_event_ingestion(client, registered_device, db_session):
    """Verify batch ingestion handles multiple events atomically."""
    t1 = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    t2 = datetime.now(timezone.utc).isoformat()
    batch_payload = {
        "events": [
            {
                "event_id": "evt-batch-1",
                "device_id": registered_device.id,
                "event_time": t1,
                "event_type": "status",
                "payload": {"state": "nominal"}
            },
            {
                "event_id": "evt-batch-2",
                "device_id": registered_device.id,
                "event_time": t2,
                "event_type": "temperature",
                "payload": {"temperature": 82.0}
            }
        ]
    }

    resp = await client.post("/api/events/batch", json=batch_payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 2
    assert body["duplicates"] == 0
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_unregistered_device_rejection(client):
    """Verify events from unknown/unregistered devices are rejected."""
    now_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "event_id": "evt-unknown",
        "device_id": "ghost-device-999",
        "event_time": now_utc,
        "event_type": "temperature",
        "payload": {"temperature": 60.0}
    }

    resp = await client.post("/api/events", json=payload)
    assert resp.status_code == 400
    assert "Unregistered device" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_malformed_event_rejection(client, registered_device):
    """Verify malformed payloads missing mandatory fields are rejected."""
    # Missing event_type and event_time
    payload = {
        "event_id": "evt-bad",
        "device_id": registered_device.id
    }
    resp = await client.post("/api/events", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_event_submission_idempotency(client, registered_device, db_session):
    """Verify duplicate event submissions are idempotent and do not duplicate events or work items."""
    now_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "event_id": "evt-dup-100",
        "device_id": registered_device.id,
        "event_time": now_utc,
        "event_type": "heartbeat",
        "payload": {"battery": 98}
    }

    # First submission
    r1 = await client.post("/api/events", json=payload)
    assert r1.status_code == 202
    assert r1.json()["status"] == "accepted"

    # Second submission (identical device_id and event_id)
    r2 = await client.post("/api/events", json=payload)
    assert r2.status_code == 202
    assert r2.json()["status"] == "duplicate"

    # Assert exactly 1 event and 1 work_item exist in DB
    event_count = (await db_session.execute(select(func.count(Event.id)))).scalar()
    work_count = (await db_session.execute(select(func.count(WorkItem.id)))).scalar()
    assert event_count == 1
    assert work_count == 1


@pytest.mark.asyncio
async def test_delayed_out_of_order_monotonic_time(client, registered_device, db_session):
    """Verify older replayed events do not roll back device's latest_report_time."""
    newer_time = datetime(2026, 10, 24, 18, 0, 0, tzinfo=timezone.utc)
    older_time = datetime(2026, 10, 24, 17, 30, 0, tzinfo=timezone.utc)

    # Ingest newer event
    await client.post("/api/events", json={
        "event_id": "evt-newer",
        "device_id": registered_device.id,
        "event_time": newer_time.isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 70.0}
    })

    dev = await db_session.get(Device, registered_device.id)
    assert dev.latest_report_time == newer_time

    # Ingest delayed older event
    await client.post("/api/events", json={
        "event_id": "evt-older",
        "device_id": registered_device.id,
        "event_time": older_time.isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 68.0}
    })

    # Refresh device and verify latest_report_time was NOT rolled back
    await db_session.refresh(dev)
    assert dev.latest_report_time == newer_time


@pytest.mark.asyncio
async def test_work_item_claiming_and_processing(client, registered_device, db_session):
    """Verify durable processing claim and status transition."""
    await client.post("/api/events", json={
        "event_id": "evt-work-1",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "status",
        "payload": {"mode": "auto"}
    })

    # Worker claims work items
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1

    # Worker processes item
    success = await process_work_item(db_session, claimed[0])
    assert success is True
    assert claimed[0].status == "completed"

    # Event is marked processed
    event = await db_session.get(Event, claimed[0].event_pk)
    assert event.processing_status == "processed"
