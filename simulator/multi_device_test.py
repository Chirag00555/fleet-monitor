import time
import uuid
import threading
import logging
from datetime import datetime, timezone
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s")

BASE_URL = "http://localhost:8000/api"

def get_client():
    return httpx.Client(timeout=10.0)

def register(dev_id, name, location):
    with get_client() as client:
        try:
            client.post(f"{BASE_URL}/devices", json={"id": dev_id, "name": name, "location": location, "is_enabled": True})
        except Exception as e:
            pass

def send_event(dev_id, event_type, payload, event_id=None, event_time=None):
    if not event_id:
        event_id = str(uuid.uuid4())
    if not event_time:
        event_time = datetime.now(timezone.utc).isoformat()
    with get_client() as client:
        resp = client.post(f"{BASE_URL}/events", json={
            "event_id": event_id,
            "device_id": dev_id,
            "event_time": event_time,
            "event_type": event_type,
            "payload": payload
        })
        return resp.json()

def send_batch(events):
    with get_client() as client:
        resp = client.post(f"{BASE_URL}/events/batch", json={"events": events})
        return resp.json()

# DEVICE 1: Overheating Spike & Incident Deduplication
def run_device_1():
    log = logging.getLogger("DEVICE-1")
    dev_id = "device-01-freezer"
    register(dev_id, "Cryo-Freezer Unit 1", "Pharma Lab A")
    log.info("Registered. Sending normal readings...")

    # Normal events
    for _ in range(3):
        send_event(dev_id, "temperature", {"temperature": 73.2, "status": "nominal"})
        time.sleep(2)

    # Overheating Spike (threshold is >= 85.0°C)
    log.warning("[ALERT TRIGGER] Sending Overheating Spike 89.5°C...")
    send_event(dev_id, "temperature", {"temperature": 89.5, "status": "overheating"})
    time.sleep(2)

    # Repeated high readings to prove DEDUPLICATION (Events counter increments)
    for i in range(3):
        temp = 91.0 + i
        log.warning(f"[DEDUP TEST] Sending repeated high temperature {temp}°C (increments count)...")
        send_event(dev_id, "temperature", {"temperature": temp, "status": "overheating"})
        time.sleep(2)

    # Duplicate submission
    dup_id = "dev1-duplicate-evt-id"
    log.info("[IDEMPOTENCY TEST] Sending duplicate event...")
    send_event(dev_id, "temperature", {"temperature": 93.0}, event_id=dup_id)
    time.sleep(1)
    send_event(dev_id, "temperature", {"temperature": 93.0}, event_id=dup_id)
    log.info("Device 1 scenario finished.")


# DEVICE 2: Disconnection, Offline Buffering & Burst Replay + Emergency Stop
def run_device_2():
    log = logging.getLogger("DEVICE-2")
    dev_id = "device-02-conveyor"
    register(dev_id, "Assembly Conveyor Motor", "Factory Floor Bay 3")
    log.info("Registered. Sending initial readings...")

    for _ in range(2):
        send_event(dev_id, "temperature", {"temperature": 68.0, "rpm": 1200})
        time.sleep(2)

    # Disconnect & Buffer locally
    log.error("[OUTAGE TRIGGERED] Disconnected from network! Buffering events in local memory...")
    buffer = []
    for i in range(4):
        buffer.append({
            "event_id": str(uuid.uuid4()),
            "device_id": dev_id,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "event_type": "temperature",
            "payload": {"temperature": 70.0 + i, "rpm": 1200, "buffered": True}
        })
        log.info(f"Buffered item {i+1} of 4 locally")
        time.sleep(2)

    # Reconnect & Replay
    log.info(f"[OUTAGE RESTORED] Reconnected! Replaying {len(buffer)} buffered events via batch ingestion...")
    res = send_batch(buffer)
    log.info(f"Batch replay complete: {res}")
    time.sleep(2)

    # Trigger Emergency Stop event
    log.critical("[EMERGENCY EVENT] Triggering Emergency Stop event...")
    send_event(dev_id, "emergency_stop", {"reason": "Conveyor Obstruction Detected"})
    log.info("Device 2 scenario finished.")


# DEVICE 3: Silence Detection & Auto-Resolution on Reconnect
def run_device_3():
    log = logging.getLogger("DEVICE-3")
    dev_id = "device-03-generator"
    register(dev_id, "Backup Diesel Generator", "Substation Outdoor")
    log.info("Registered. Sending first heartbeat...")

    send_event(dev_id, "heartbeat", {"fuel_level_pct": 94, "voltage": 240})
    log.info("Reported once. Going SILENT for 24 seconds (Silence threshold is 20s)...")

    # Wait for silence scanner to trigger WARNING incident
    time.sleep(24)

    # Reconnect & send fresh telemetry -> auto resolves silence incident!
    log.info("[DEVICE RECONNECTED] Coming back online! Sending fresh heartbeat...")
    send_event(dev_id, "heartbeat", {"fuel_level_pct": 93, "voltage": 240, "status": "online"})
    log.info("Device 3 scenario finished. Silence incident auto-resolved!")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("STARTING 3-DEVICE MULTI-FEATURE TEST SIMULATION (Safe Client Isolation)")
    print("="*70 + "\n")

    t1 = threading.Thread(target=run_device_1)
    t2 = threading.Thread(target=run_device_2)
    t3 = threading.Thread(target=run_device_3)

    t1.start()
    time.sleep(0.5)
    t2.start()
    time.sleep(0.5)
    t3.start()

    t1.join()
    t2.join()
    t3.join()

    print("\n" + "="*70)
    print("ALL 3 DEVICE SIMULATIONS COMPLETED SUCCESSFULLY")
    print("="*70 + "\n")
