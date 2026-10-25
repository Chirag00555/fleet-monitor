import pytest
from datetime import datetime, timezone
from app.worker.processor import claim_next_work_items, process_work_item

@pytest.mark.asyncio
async def test_end_to_end_simulator_to_incident_and_inspection(client, db_session):
    """
    End-to-End integration verification:
    1. Register simulated device.
    2. Create alert rule (temperature >= 85.0).
    3. Simulator generates and ingests telemetry crossing threshold.
    4. Worker claims work item, evaluates rule, and opens incident.
    5. Incident is inspectable via API with associated telemetry events.
    6. Operator acknowledges and resolves incident.
    """
    # 1. Register device
    dev_resp = await client.post("/api/devices", json={
        "id": "dev-e2e-001",
        "name": "E2E Cryogenic Freezer",
        "location": "Pharma Lab B",
        "is_enabled": True
    })
    assert dev_resp.status_code == 201

    # 2. Define alert rule
    rule_resp = await client.post("/api/rules", json={
        "name": "Freezer Warming Alert",
        "rule_type": "temperature_threshold",
        "comparator": ">=",
        "threshold_value": 85.0,
        "payload_field": "temperature",
        "severity": "critical"
    })
    assert rule_resp.status_code == 201
    rule_id = rule_resp.json()["id"]

    # 3. Simulate high temperature event ingestion
    event_payload = {
        "event_id": "evt-e2e-spike-100",
        "device_id": "dev-e2e-001",
        "event_time": datetime.now(timezone.utc).isoformat(),
        "event_type": "temperature",
        "payload": {
            "temperature": 89.4,
            "unit": "celsius",
            "compressor_active": False
        }
    }
    ingest_resp = await client.post("/api/events", json=event_payload)
    assert ingest_resp.status_code == 202
    assert ingest_resp.json()["status"] == "accepted"

    # 4. Worker claims and processes work item
    claimed = await claim_next_work_items(db_session, batch_size=10)
    assert len(claimed) >= 1
    target_item = next(item for item in claimed if item.event.event_id == "evt-e2e-spike-100")
    success = await process_work_item(db_session, target_item)
    assert success is True

    # 5. Verify incident created and inspectable
    inc_list_resp = await client.get("/api/incidents?device_id=dev-e2e-001&status=open")
    assert inc_list_resp.status_code == 200
    incidents = inc_list_resp.json()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["severity"] == "critical"
    assert incident["rule_id"] == rule_id

    # Inspect incident details & associated event
    detail_resp = await client.get(f"/api/incidents/{incident['id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["events"]) == 1
    assert detail["events"][0]["event_id"] == "evt-e2e-spike-100"
    assert detail["events"][0]["payload"]["temperature"] == 89.4

    # 6. Operator workflow: Acknowledge & Resolve
    ack_resp = await client.post(f"/api/incidents/{incident['id']}/acknowledge")
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "acknowledged"

    res_resp = await client.post(f"/api/incidents/{incident['id']}/resolve", json={
        "resolution_note": "Compressor backup power engaged"
    })
    assert res_resp.status_code == 200
    assert res_resp.json()["status"] == "resolved"
    assert res_resp.json()["resolution_note"] == "Compressor backup power engaged"
