import argparse
import time
import uuid
import random
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [SIMULATOR]: %(message)s")
logger = logging.getLogger("simulator")

class DeviceSimulator:
    """
    Simulates edge device behavior:
    - Periodic telemetry generation (temperature, status, heartbeat).
    - Normal readings vs threshold-crossing anomalies.
    - Temporary network disconnection with offline buffering.
    - Reconnection and burst replay via /api/events/batch.
    - Stable UUIDv4 event IDs for idempotent delivery.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        device_id: str = "device-sim-001",
        device_name: str = "Simulated Cold Storage Monitor",
        location: str = "Facility Alpha - Unit 3",
        interval: float = 2.0,
        temp_min: float = 65.0,
        temp_max: float = 95.0,
        spike_prob: float = 0.2,
        spike_threshold: float = 85.0,
        batch_size: int = 50,
        pairing_code: Optional[str] = None,
        api_token: Optional[str] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        import os
        base_dir = "/app/credentials" if os.path.isdir("/app/credentials") else "."
        self.credentials_file = os.path.join(base_dir, f".device_{device_id}_creds.json")
        self.pairing_code = pairing_code
        self.api_token = api_token or self._load_saved_token()
        self.device_name = device_name
        self.location = location
        self.interval = interval
        self.temp_min = temp_min
        self.temp_max = temp_max
        self.spike_prob = spike_prob
        self.spike_threshold = spike_threshold
        self.batch_size = batch_size

        self.client = httpx.Client(timeout=5.0)
        self.buffer: List[Dict[str, Any]] = []
        self.is_disconnected = False



    def _load_saved_token(self) -> Optional[str]:
        """Load previously paired and persisted API token from local disk."""
        import json
        import os
        if os.path.exists(self.credentials_file):
            try:
                with open(self.credentials_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    tok = data.get("api_token")
                    if tok:
                        logger.info(f"Loaded persistent API token for {self.device_id} from {self.credentials_file}")
                        return tok
            except Exception as e:
                logger.warning(f"Could not read credentials file {self.credentials_file}: {e}")
        return None

    def _save_token(self, token: str):
        """Persist paired API token locally so device remains connected across restarts."""
        import json
        try:
            with open(self.credentials_file, "w", encoding="utf-8") as f:
                json.dump({"device_id": self.device_id, "api_token": token}, f)
            logger.info(f"Persisted API token to {self.credentials_file}")
        except Exception as e:
            logger.warning(f"Failed to persist credentials: {e}")

    def claim_pairing_code(self) -> bool:
        """Claim pairing code and save assigned API token."""
        if not self.pairing_code:
            return False
        url = f"{self.base_url}/api/devices/pairing/claim"
        try:
            resp = self.client.post(url, json={"pairing_code": self.pairing_code})
            if resp.status_code == 200:
                data = resp.json()
                self.api_token = data.get("api_token")
                self.device_id = data.get("device_id", self.device_id)
                self._save_token(self.api_token)
                logger.info(f"Successfully claimed pairing code. Assigned token: {self.api_token[:12]}... for {self.device_id}")
                return True
            else:
                logger.warning(f"Failed to claim pairing code: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error claiming pairing code: {e}")
            return False

    def register_device(self) -> bool:
        """Ensure device has a valid API token before transmitting telemetry."""
        if self.api_token:
            return True

        if self.pairing_code:
            return self.claim_pairing_code()

        logger.warning(
            f"Device '{self.device_id}' is NOT paired. No API token found.\n"
            f"Please generate a Pairing Code in the Operator Console and pass --pairing-code <CODE>."
        )
        return False

    def generate_event(self) -> Dict[str, Any]:
        """Generate a single telemetry event with a stable UUID."""
        now = datetime.now(timezone.utc)
        event_id = str(uuid.uuid4())

        # Determine if this reading should simulate a temperature spike
        if random.random() < self.spike_prob:
            temp = round(random.uniform(self.spike_threshold, self.temp_max + 10.0), 2)
            event_type = "temperature"
        else:
            temp = round(random.uniform(75.0, 84.9), 2) if random.random() < 0.45 else round(random.uniform(self.temp_min, 74.0), 2)
            event_type = "temperature" if random.random() < 0.8 else "heartbeat"

        payload: Dict[str, Any] = {
            "temperature": temp,
            "humidity": round(random.uniform(30.0, 60.0), 1),
            "battery_pct": round(random.uniform(70.0, 100.0), 1)
        }

        return {
            "event_id": event_id,
            "device_id": self.device_id,
            "event_time": now.isoformat(),
            "event_type": event_type,
            "payload": payload
        }

    def send_single_event(self, event: Dict[str, Any]) -> bool:
        """Send a single event to /api/events."""
        url = f"{self.base_url}/api/events"
        headers = {}
        if self.api_token:
            headers["X-Device-Token"] = self.api_token
        try:
            resp = self.client.post(url, json=event, headers=headers)
            if resp.status_code == 202:
                logger.info(f"Sent event {event['event_id']} (temp: {event['payload'].get('temperature')}) -> status: {resp.json().get('status')}")
                return True
            else:
                logger.warning(f"Event send failed: HTTP {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            logger.warning(f"Network error while sending event: {e}")
            return False

    def replay_buffered_events(self) -> int:
        """Replay all buffered events via batch ingestion."""
        if not self.buffer:
            return 0

        total_replayed = 0
        url = f"{self.base_url}/api/events/batch"
        logger.info(f"Reconnecting... Replaying {len(self.buffer)} buffered events in chunks of {self.batch_size}...")

        headers = {}
        if self.api_token:
            headers["X-Device-Token"] = self.api_token
        while self.buffer:
            chunk = self.buffer[:self.batch_size]
            try:
                resp = self.client.post(url, json={"events": chunk}, headers=headers)
                if resp.status_code == 202:
                    result = resp.json()
                    accepted = result.get("accepted", 0)
                    duplicates = result.get("duplicates", 0)
                    total_replayed += len(chunk)
                    logger.info(f"Replayed chunk of {len(chunk)} events -> accepted: {accepted}, duplicates: {duplicates}")
                    self.buffer = self.buffer[self.batch_size:]
                else:
                    logger.error(f"Batch replay failed: HTTP {resp.status_code} - {resp.text}")
                    break
            except Exception as e:
                logger.error(f"Replay connection failed: {e}")
                break

        return total_replayed

    def run_simulation(
        self,
        duration_seconds: int = 30,
        disconnect_at: Optional[int] = 10,
        disconnect_duration: Optional[int] = 10,
        resend_duplicate: bool = True
    ):
        """Run complete demonstration loop."""
        is_ready = self.register_device()
        if not is_ready and not self.api_token:
            logger.error(f"Aborting telemetry loop: Device '{self.device_id}' has not been paired by an operator.")
            return

        start_time = time.time()
        step = 0
        last_sent_event: Optional[Dict[str, Any]] = None

        logger.info(f"Starting simulation loop for {duration_seconds}s (interval={self.interval}s)...")

        while (time.time() - start_time) < duration_seconds:
            elapsed = time.time() - start_time
            step += 1

            # Check disconnection state
            if disconnect_at and disconnect_duration:
                if disconnect_at <= elapsed < (disconnect_at + disconnect_duration):
                    if not self.is_disconnected:
                        logger.warning(f"[OUTAGE TRIGGERED] Device disconnected at {elapsed:.1f}s. Buffering events locally...")
                        self.is_disconnected = True
                else:
                    if self.is_disconnected:
                        logger.info(f"[OUTAGE RESTORED] Device reconnected at {elapsed:.1f}s. Starting replay...")
                        self.is_disconnected = False
                        self.replay_buffered_events()

            # Generate next reading
            event = self.generate_event()

            if self.is_disconnected:
                self.buffer.append(event)
                logger.info(f"Buffered offline event {event['event_id']} (Total in buffer: {len(self.buffer)})")
            else:
                success = self.send_single_event(event)
                if success:
                    last_sent_event = event

                # Optionally demonstrate duplicate delivery
                if resend_duplicate and step == 4 and last_sent_event:
                    logger.info(f"[DEMO DUPLICATE] Deliberately re-transmitting event {last_sent_event['event_id']}...")
                    self.send_single_event(last_sent_event)

            time.sleep(self.interval)

        # Flush any remaining buffer if still connected at end
        if self.buffer and not self.is_disconnected:
            self.replay_buffered_events()

        logger.info("Simulation completed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fleet Operations Device Simulator")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--device-id", default="device-sim-001", help="Device ID")
    parser.add_argument("--device-name", default="Cold Storage Sensor 1", help="Device display name")
    parser.add_argument("--location", default="Warehouse Section B", help="Device location")
    parser.add_argument("--interval", type=float, default=2.0, help="Reporting interval in seconds")
    parser.add_argument("--duration", type=int, default=30, help="Total simulation duration in seconds")
    parser.add_argument("--disconnect-at", type=int, default=10, help="Seconds from start to simulate network outage")
    parser.add_argument("--disconnect-duration", type=int, default=10, help="Duration of network outage in seconds")
    parser.add_argument("--threshold", type=float, default=85.0, help="Spike threshold value")
    parser.add_argument("--spike-prob", type=float, default=0.25, help="Probability of high temperature spike")
    parser.add_argument("--no-duplicate-demo", action="store_true", help="Disable duplicate demonstration")
    parser.add_argument("--pairing-code", default=None, help="One-time pairing code to claim upon startup")
    parser.add_argument("--token", default=None, help="Pre-provisioned device API token")

    args = parser.parse_args()

    import os
    token = args.token or os.environ.get("DEVICE_API_TOKEN")
    pairing_code = args.pairing_code or os.environ.get("DEVICE_PAIRING_CODE")

    sim = DeviceSimulator(
        base_url=args.url,
        device_id=args.device_id,
        device_name=args.device_name,
        location=args.location,
        interval=args.interval,
        spike_threshold=args.threshold,
        spike_prob=args.spike_prob,
        pairing_code=pairing_code,
        api_token=token
    )
    sim.run_simulation(
        duration_seconds=args.duration,
        disconnect_at=args.disconnect_at,
        disconnect_duration=args.disconnect_duration,
        resend_duplicate=not args.no_duplicate_demo
    )
