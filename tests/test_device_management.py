import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_device_disable_and_enable(client: AsyncClient):
    # 1. Register device
    dev_id = "test-toggle-dev"
    reg_res = await client.post("/api/devices", json={
        "id": dev_id,
        "name": "Toggle Test Device",
        "location": "Lab 1",
        "is_enabled": True
    })
    assert reg_res.status_code == 201
    assert reg_res.json()["is_enabled"] is True

    # 2. Disable device via PATCH
    patch_res = await client.patch(f"/api/devices/{dev_id}", json={"is_enabled": False})
    assert patch_res.status_code == 200
    assert patch_res.json()["is_enabled"] is False

    # 3. Ingestion for disabled device should be rejected with 400
    ingest_res = await client.post("/api/events", json={
        "event_id": "evt-toggle-1",
        "device_id": dev_id,
        "event_type": "heartbeat",
        "event_time": "2026-10-25T12:00:00Z",
        "payload": {"status": "ok"}
    })
    assert ingest_res.status_code == 400
    assert "disabled" in ingest_res.json()["detail"].lower()

    # 4. Re-enable device
    enable_res = await client.patch(f"/api/devices/{dev_id}", json={"is_enabled": True})
    assert enable_res.status_code == 200
    assert enable_res.json()["is_enabled"] is True

    # 5. Ingestion succeeds when enabled
    ingest_res2 = await client.post("/api/events", json={
        "event_id": "evt-toggle-2",
        "device_id": dev_id,
        "event_type": "heartbeat",
        "event_time": "2026-10-25T12:01:00Z",
        "payload": {"status": "ok"}
    })
    assert ingest_res2.status_code == 202

@pytest.mark.asyncio
async def test_device_revoke_and_delete(client: AsyncClient):
    dev_id = "test-delete-dev"
    reg_res = await client.post("/api/devices", json={
        "id": dev_id,
        "name": "Delete Test Device"
    })
    assert reg_res.status_code == 201

    # Delete device
    del_res = await client.delete(f"/api/devices/{dev_id}")
    assert del_res.status_code == 204

    # Fetch devices - should not contain dev_id
    list_res = await client.get("/api/devices")
    devices = list_res.json()
    assert not any(d["id"] == dev_id for d in devices)

    # Ingesting now returns 400 (Unregistered device)
    ingest_res = await client.post("/api/events", json={
        "event_id": "evt-del-1",
        "device_id": dev_id,
        "event_type": "heartbeat",
        "event_time": "2026-10-25T12:00:00Z",
        "payload": {}
    })
    assert ingest_res.status_code == 400
    assert "unregistered" in ingest_res.json()["detail"].lower()
