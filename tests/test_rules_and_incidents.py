import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from app.models.entities import Rule, Incident, IncidentEvent, Device, Event
from app.worker.processor import claim_next_work_items, process_work_item
from app.worker.rules_engine import run_device_silence_check

@pytest.mark.asyncio
async def test_temperature_rule_matching_and_deduplication(client, registered_device, db_session):
    """
    Verify:
    1. Temperature below threshold creates no incident.
    2. Temperature matching threshold creates an incident using explicit '>=' semantics.
    3. Repeated matching events attach to the same active incident (increment count).
    """
    # 1. Create a temperature rule (threshold: 80.0, comparator: '>=')
    rule_resp = await client.post("/api/rules", json={
        "name": "High Temp Alert",
        "rule_type": "temperature_threshold",
        "comparator": ">=",
        "threshold_value": 80.0,
        "payload_field": "temperature",
        "severity": "critical"
    })
    assert rule_resp.status_code == 201
    rule_id = rule_resp.json()["id"]

    # 2. Ingest event with temperature = 75.0 (below threshold)
    await client.post("/api/events", json={
        "event_id": "evt-sub-threshold",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 75.0}
    })
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    await process_work_item(db_session, claimed[0])

    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 0

    # 3. Ingest event with temperature = 80.0 (exact match on '>=')
    await client.post("/api/events", json={
        "event_id": "evt-match-1",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 80.0}
    })
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    await process_work_item(db_session, claimed[0])

    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.status == "open"
    assert inc.rule_id == rule_id
    assert inc.device_id == registered_device.id
    assert inc.event_count == 1
    assert inc.severity == "critical"

    # 4. Ingest second matching event (temperature = 85.5) -> verify deduplication
    await client.post("/api/events", json={
        "event_id": "evt-match-2",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": 85.5}
    })
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    await process_work_item(db_session, claimed[0])

    # Should still have exactly 1 active incident, with event_count incremented
    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 1
    assert incidents[0].id == inc.id
    assert incidents[0].event_count == 2

    # Check incident events association
    assocs = (await db_session.execute(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))).scalars().all()
    assert len(assocs) == 2


@pytest.mark.asyncio
async def test_event_type_rule_and_disabled_rules(client, registered_device, db_session):
    """Verify event-type matching and that disabled rules do not trigger."""
    # 1. Create enabled event-type rule for 'emergency_stop'
    r1 = await client.post("/api/rules", json={
        "name": "E-Stop Triggered",
        "rule_type": "event_type",
        "target_event_type": "emergency_stop",
        "severity": "critical"
    })
    assert r1.status_code == 201

    # 2. Create disabled event-type rule for 'battery_critical'
    r2 = await client.post("/api/rules", json={
        "name": "Battery Alert",
        "rule_type": "event_type",
        "target_event_type": "battery_critical",
        "is_enabled": False,
        "severity": "warning"
    })
    assert r2.status_code == 201

    # Ingest emergency_stop event
    await client.post("/api/events", json={
        "event_id": "evt-estop",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "emergency_stop",
        "payload": {"reason": "Manual operator button"}
    })
    # Ingest battery_critical event
    await client.post("/api/events", json={
        "event_id": "evt-battery",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "battery_critical",
        "payload": {"level": 5}
    })

    claimed = await claim_next_work_items(db_session, batch_size=10)
    for item in claimed:
        await process_work_item(db_session, item)

    # Only 1 incident should be created (from enabled emergency_stop rule)
    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 1
    assert "E-Stop Triggered" in incidents[0].title


@pytest.mark.asyncio
async def test_malformed_temperature_field_handling(client, registered_device, db_session):
    """Verify missing or non-numerical temperature fields do not crash and create no false incidents."""
    await client.post("/api/rules", json={
        "name": "Temp Rule",
        "rule_type": "temperature_threshold",
        "threshold_value": 70.0
    })

    # Missing field
    await client.post("/api/events", json={
        "event_id": "evt-no-field",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"humidity": 45}
    })
    # Non-numeric string
    await client.post("/api/events", json={
        "event_id": "evt-string-field",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {"temperature": "NOT_A_NUMBER"}
    })

    claimed = await claim_next_work_items(db_session, batch_size=10)
    for item in claimed:
        success = await process_work_item(db_session, item)
        assert success is True # Should process gracefully without throwing

    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 0


@pytest.mark.asyncio
async def test_device_silence_detection_and_reconnection_resolution(client, registered_device, db_session):
    """
    Verify:
    1. Device exceeding silence duration creates one incident.
    2. Repeated silence checks do not duplicate incidents.
    3. Monotonic report time is respected.
    4. Reconnecting and sending fresh event auto-resolves the silence incident.
    """
    # Create silence rule: 30 seconds threshold
    await client.post("/api/rules", json={
        "name": "Silence Rule",
        "rule_type": "device_silence",
        "silence_duration_seconds": 30,
        "severity": "warning"
    })

    # Set device latest report to 60 seconds ago
    now = datetime.now(timezone.utc)
    past_report_time = now - timedelta(seconds=60)
    dev = await db_session.get(Device, registered_device.id)
    dev.latest_report_time = past_report_time
    await db_session.commit()

    # 1. Run silence check -> should create 1 incident
    count = await run_device_silence_check(db_session)
    assert count == 1
    incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(incidents) == 1
    assert incidents[0].status == "open"
    assert "not reporting" in incidents[0].title
    silence_inc_id = incidents[0].id

    # 2. Run silence check again -> should NOT create duplicate
    count2 = await run_device_silence_check(db_session)
    assert count2 == 1
    all_incidents = (await db_session.execute(select(Incident))).scalars().all()
    assert len(all_incidents) == 1
    assert all_incidents[0].id == silence_inc_id

    # 3. Device reconnects and sends fresh event -> should auto-resolve silence incident
    fresh_time = datetime.now(timezone.utc)
    await client.post("/api/events", json={
        "event_id": "evt-reconnect",
        "device_id": registered_device.id,
        "event_time": fresh_time.isoformat(),
        "event_type": "heartbeat",
        "payload": {"status": "online"}
    })
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) == 1
    await process_work_item(db_session, claimed[0])

    # Verify incident auto-resolved
    resolved_inc = await db_session.get(Incident, silence_inc_id)
    assert resolved_inc.status == "resolved"
    assert resolved_inc.resolved_at is not None
    assert "Auto-resolved" in (resolved_inc.resolution_note or "")


@pytest.mark.asyncio
async def test_incident_lifecycle_apis(client, registered_device, db_session):
    """Verify incident listing, inspection, acknowledge and resolve API transitions."""
    # Create rule and trigger incident
    await client.post("/api/rules", json={
        "name": "Fault Rule",
        "rule_type": "event_type",
        "target_event_type": "system_fault",
        "severity": "critical"
    })
    await client.post("/api/events", json={
        "event_id": "evt-fault-01",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "system_fault",
        "payload": {"code": 500}
    })
    claimed = await claim_next_work_items(db_session, batch_size=10)
    await process_work_item(db_session, claimed[0])

    # 1. List incidents
    resp = await client.get("/api/incidents?status=open")
    assert resp.status_code == 200
    inc_list = resp.json()
    assert len(inc_list) == 1
    inc_id = inc_list[0]["id"]
    assert inc_list[0]["status"] == "open"

    # 2. Get incident details
    detail_resp = await client.get(f"/api/incidents/{inc_id}")
    assert detail_resp.status_code == 200
    details = detail_resp.json()
    assert len(details["events"]) == 1
    assert details["events"][0]["event_id"] == "evt-fault-01"

    # 3. Acknowledge incident
    ack_resp = await client.post(f"/api/incidents/{inc_id}/acknowledge")
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "acknowledged"
    assert ack_resp.json()["acknowledged_at"] is not None

    # Cannot acknowledge already acknowledged incident
    ack_fail = await client.post(f"/api/incidents/{inc_id}/acknowledge")
    assert ack_fail.status_code == 400

    # 4. Resolve incident
    res_resp = await client.post(f"/api/incidents/{inc_id}/resolve", json={"resolution_note": "Replaced fuse"})
    assert res_resp.status_code == 200
    assert res_resp.json()["status"] == "resolved"
    assert res_resp.json()["resolved_at"] is not None
    assert res_resp.json()["resolution_note"] == "Replaced fuse"

    # Cannot resolve already resolved incident
    res_fail = await client.post(f"/api/incidents/{inc_id}/resolve")
    assert res_fail.status_code == 400


@pytest.mark.asyncio
async def test_worker_retry_idempotency_for_incidents(client, registered_device, db_session):
    """Verify that re-processing a work item does not create duplicate incidents or event associations."""
    await client.post("/api/rules", json={
        "name": "Repeat Rule",
        "rule_type": "event_type",
        "target_event_type": "sensor_error"
    })
    await client.post("/api/events", json={
        "event_id": "evt-repeat-01",
        "device_id": registered_device.id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "sensor_error",
        "payload": {}
    })

    claimed = await claim_next_work_items(db_session, batch_size=10)
    item = claimed[0]

    # Process first time
    await process_work_item(db_session, item)
    inc_count_1 = (await db_session.execute(select(func.count(Incident.id)))).scalar()
    assoc_count_1 = (await db_session.execute(select(func.count(IncidentEvent.incident_id)))).scalar()
    assert inc_count_1 == 1
    assert assoc_count_1 == 1

    # Simulate retry: process item again
    await process_work_item(db_session, item)
    inc_count_2 = (await db_session.execute(select(func.count(Incident.id)))).scalar()
    assoc_count_2 = (await db_session.execute(select(func.count(IncidentEvent.incident_id)))).scalar()
    assert inc_count_2 == 1
    assert assoc_count_2 == 1
