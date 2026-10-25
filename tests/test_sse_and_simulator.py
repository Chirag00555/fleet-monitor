import pytest
import asyncio
from datetime import datetime, timezone
from app.core.broadcaster import Broadcaster, broadcaster
from simulator.device_simulator import DeviceSimulator

@pytest.mark.asyncio
async def test_sse_broadcaster_subscribe_and_dispatch():
    """Verify SSE broadcaster dispatches formatted payloads to subscribers."""
    b = Broadcaster(max_queue_size=10)
    q = b.subscribe()
    assert len(b.subscribers) == 1

    await b.broadcast("incident_created", {"id": 101, "status": "open", "severity": "critical"})

    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    assert msg["type"] == "incident_created"
    assert msg["data"]["id"] == 101
    assert msg["data"]["severity"] == "critical"

    b.unsubscribe(q)
    assert len(b.subscribers) == 0


@pytest.mark.asyncio
async def test_sse_endpoint_delivery(client):
    """Verify GET /api/events/stream establishes stream, receives event, and cleanly closes."""
    async def push_test_and_close():
        await asyncio.sleep(0.05)
        await broadcaster.broadcast("incident_created", {"id": 999, "status": "open", "title": "Test SSE"})
        await asyncio.sleep(0.05)
        await broadcaster.broadcast("_close_stream", {})

    task = asyncio.create_task(push_test_and_close())

    response = await client.get("/api/events/stream")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "connected to fleet-monitor" in text
    assert "Test SSE" in text

    await task


def test_simulator_buffering_and_stable_ids():
    """Verify simulator offline buffering maintains stable UUIDs and payload schema."""
    sim = DeviceSimulator(device_id="dev-sim-test", base_url="http://mock")
    events = [sim.generate_event() for _ in range(5)]
    
    event_ids = [e["event_id"] for e in events]
    assert len(set(event_ids)) == 5
    for e in events:
        assert e["device_id"] == "dev-sim-test"
        assert "temperature" in e["payload"]
        assert e["event_type"] in ["temperature", "heartbeat"]

    for e in events:
        sim.buffer.append(e)
    assert len(sim.buffer) == 5
