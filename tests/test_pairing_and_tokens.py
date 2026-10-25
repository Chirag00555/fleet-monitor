import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_pairing_code_generation_and_claim(client: AsyncClient):
    # 1. Operator generates pairing code
    gen_res = await client.post("/api/devices/pairing/generate", json={
        "device_id": "test-paired-node-01",
        "device_name": "Paired Freezer Sensor",
        "location": "Warehouse B",
        "expires_in_minutes": 10
    })
    assert gen_res.status_code == 201
    data = gen_res.json()
    code = data["code"]
    assert len(code) == 8
    assert data["device_id"] == "test-paired-node-01"

    # 2. Edge device claims the pairing code
    claim_res = await client.post("/api/devices/pairing/claim", json={
        "pairing_code": code
    })
    assert claim_res.status_code == 200
    claim_data = claim_res.json()
    token = claim_data["api_token"]
    assert token.startswith("dev_")
    assert claim_data["device_id"] == "test-paired-node-01"

    # 3. Trying to claim the same code again fails (already used)
    claim_again_res = await client.post("/api/devices/pairing/claim", json={
        "pairing_code": code
    })
    assert claim_again_res.status_code == 400

    # 4. Ingest telemetry without token fails with 401 Unauthorized
    unauth_ingest = await client.post("/api/events", json={
        "event_id": "pair-evt-1",
        "device_id": "test-paired-node-01",
        "event_type": "heartbeat",
        "event_time": "2026-10-25T19:00:00Z",
        "payload": {"status": "ok"}
    })
    assert unauth_ingest.status_code == 401

    # 5. Ingest telemetry with wrong token fails with 401 Unauthorized
    wrong_token_ingest = await client.post(
        "/api/events",
        json={
            "event_id": "pair-evt-2",
            "device_id": "test-paired-node-01",
            "event_type": "heartbeat",
            "event_time": "2026-10-25T19:01:00Z",
            "payload": {"status": "ok"}
        },
        headers={"X-Device-Token": "dev_wrong_token_123"}
    )
    assert wrong_token_ingest.status_code == 401

    # 6. Ingest telemetry with valid token succeeds with 202 Accepted
    valid_ingest = await client.post(
        "/api/events",
        json={
            "event_id": "pair-evt-3",
            "device_id": "test-paired-node-01",
            "event_type": "heartbeat",
            "event_time": "2026-10-25T19:02:00Z",
            "payload": {"status": "ok"}
        },
        headers={"X-Device-Token": token}
    )
    assert valid_ingest.status_code == 202
