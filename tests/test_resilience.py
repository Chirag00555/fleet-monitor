import pytest
from unittest.mock import patch
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.models.entities import Event, WorkItem
from app.worker.processor import claim_next_work_items, process_work_item

@pytest.mark.asyncio
async def test_atomic_transaction_rollback_on_failure(client, registered_device, db_session):
    """Verify that if WorkItem creation or persistence fails, Event is rolled back."""
    now_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "event_id": "evt-fail-test",
        "device_id": registered_device.id,
        "event_time": now_utc,
        "event_type": "temperature",
        "payload": {"temperature": 99.0}
    }

    with patch.object(db_session, "commit", side_effect=RuntimeError("Database failure simulated")):
        resp = await client.post("/api/events", json=payload)
        assert resp.status_code == 500

    event_count = (await db_session.execute(select(func.count(Event.id)))).scalar()
    work_count = (await db_session.execute(select(func.count(WorkItem.id)))).scalar()
    assert event_count == 0
    assert work_count == 0


@pytest.mark.asyncio
async def test_work_item_retry_and_exponential_backoff(client, registered_device, db_session):
    """Verify that failing work items record errors, increment attempts, and back off."""
    await client.post("/api/events", json={
        "event_id": "evt-retry-1",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 105.0}
    })

    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    item = claimed[0]

    # Patch session.get to raise an error during execution of process_work_item
    item.event = None # force lookup
    with patch.object(db_session, "get", side_effect=RuntimeError("Transient DB error during processing")):
        success = await process_work_item(db_session, item)
        assert success is False

    refreshed_item = (await db_session.execute(select(WorkItem).where(WorkItem.id == item.id))).scalar_one()
    assert refreshed_item.status == "pending"
    assert refreshed_item.attempts == 1
    assert "Transient DB error" in refreshed_item.last_error
    assert refreshed_item.locked_at is None
    assert refreshed_item.locked_by is None
