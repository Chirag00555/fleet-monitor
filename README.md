# Fleet Operations Platform - Real-time Edge Device Operations & Monitoring

A complete, resilient platform for monitoring a fleet of distributed devices, ingesting high-throughput telemetry, evaluating rules, deduplicating active incidents, providing a live real-time dashboard via Server-Sent Events (SSE), and simulating lossy edge device connectivity.

---

## 1. Architecture & End-to-End Event Flow

`
┌─────────────────────────────────────────────────────────────┐
│ Edge Layer: Python Device Simulator (simulator/)            │
│  - Generates periodic telemetry (temperature, heartbeat)    │
│  - Simulates offline disconnection & local buffer queue     │
│  - Reconnects & replays buffered bursts with stable UUIDs   │
└──────────────────────────────┬──────────────────────────────┘
                               │ (HTTP POST /api/events or /api/events/batch)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI Ingestion Layer (app/api/)                          │
│  - Validates registered & enabled device status             │
│  - Monotonic clock updates (GREATEST timestamp guard)       │
│  - Composite unique constraint: (device_id, event_id)       │
│  - Transactional persistence (Event + WorkItem in one TX)   │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ PostgreSQL 16 (app/models/entities.py)                      │
│  - devices: Registry & monotonic latest_report_time         │
│  - events: Telemetry archive with receipt vs event time     │
│  - work_items: Durable transactional outbox                 │
│  - rules & incidents: Deduplicated incident state machine   │
└──────────────────────────────┬──────────────────────────────┘
                               │ (SELECT FOR UPDATE SKIP LOCKED)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Background Worker Processor (app/worker/)                   │
│  - Durable consumer with exponential retry backoff          │
│  - Immediate temperature & event-type rule evaluation       │
│  - Periodic device-silence scanner (configurable interval)  │
│  - Active incident deduplication (1 active per condition)   │
│  - Auto-resolves silence incidents on telemetry reconnect   │
└──────────────────────────────┬──────────────────────────────┘
                               │ (Broadcast updates)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ SSE Broadcaster & REST Endpoints (GET /api/events/stream)   │
│  - Non-blocking subscriber queues with overflow protection  │
│  - Heartbeat keep-alives (15s)                              │
└──────────────────────────────┬──────────────────────────────┘
                               │ (text/event-stream & REST)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Frontend Dashboard (React + TypeScript + Vite)              │
│  - Live fleet status matrix & operational counters          │
│  - Real-time telemetry event feed                           │
│  - Interactive incident inspector with associated events    │
│  - One-click Acknowledge & Resolve actions                  │
│  - Auto-reconcile on SSE reconnect                          │
└─────────────────────────────────────────────────────────────┘
`

---

## 2. Server-Sent Events (SSE) Route & Reconnection Model

- **Endpoint Route:** GET /api/events/stream
- **Content-Type:** 	ext/event-stream
- **Supported Event Types:**
  - event_received: Dispatched when an event is durably ingested.
  - incident_created: Dispatched when a new open incident is created.
  - incident_updated: Dispatched when repeated matching telemetry attaches to an active incident.
  - incident_acknowledged: Dispatched when an operator acknowledges an incident.
  - incident_resolved: Dispatched when an incident is resolved.
- **Replay / Reconnection Strategy:**
  - The SSE endpoint utilizes non-blocking memory-bounded subscriber queues to guarantee ingestion and worker performance is never compromised by slow browser clients.
  - **Reconnection Reconcile:** Instead of complex in-stream historical replay, the React frontend detects connection loss, displays a visible RECONNECTING... badge, and automatically issues a consolidated REST fetch to /api/incidents, /api/devices, and /api/events upon reconnection. This guarantees the UI recovers state from any offline gap.

---

## 3. Device Simulator & Offline Buffering

The simulator (simulator/device_simulator.py) demonstrates real-world edge telemetry conditions:
- **Registration:** Checks and registers the device (POST /api/devices).
- **Telemetry Generation:** Generates timestamped temperature readings with stable UUIDv4 event IDs.
- **Outage Simulation:** Simulates network outage by cutting off HTTP transmission and buffering events locally.
- **Reconnect & Batch Replay:** Replays all buffered events via POST /api/events/batch in configurable chunk sizes.
- **Deduplication:** Demonstrates idempotent resending of previously accepted events.

### Running the Simulator
`ash
uv run python -m simulator.device_simulator --duration 45 --interval 2 --threshold 85 --disconnect-at 10 --disconnect-duration 10
`

---

## 4. Local Setup, Run Instructions & Full Stack Demo

### Option A: Complete Direct Run (Recommended)

1. **Start PostgreSQL:**
   `powershell
   docker run --name local_postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=fleet_monitor -p 5432:5432 -d postgres:16-alpine
   `
2. **Apply Migrations:**
   `powershell
   uv run alembic upgrade head
   `
3. **Start FastAPI Backend (Terminal 1):**
   `powershell
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   `
4. **Start Background Worker (Terminal 2):**
   `powershell
   uv run python -m app.worker.processor
   `
5. **Start Frontend Operations Dashboard (Terminal 3):**
   `powershell
   cd frontend
   npm run dev
   `
   Open http://localhost:5173 in your browser.

6. **Run Edge Device Simulator (Terminal 4):**
   `powershell
   uv run python -m simulator.device_simulator --duration 60 --threshold 85 --disconnect-at 10 --disconnect-duration 10
   `

### Running Backend & E2E Tests
`powershell
uv run pytest -v
`

---

## 5. Demonstration Walkthrough

1. Open the dashboard at http://localhost:5173. Notice the LIVE (SSE) badge.
2. In Terminal 4, start the simulator. The dashboard immediately reflects the newly registered device device-sim-001 in the **Fleet Devices** panel.
3. As normal telemetry is produced, watch the **Live Ingestion Stream** terminal update live without page refreshes.
4. When a temperature reading exceeds 85.0°C, the worker immediately creates a **High Temperature Alert** incident under **Operational Incidents**.
5. Click on the incident row to open the **Incident Inspector Modal**, displaying the triggering event payload and measurement timestamp.
6. Click **Acknowledge** to take ownership of the ticket, then **Resolve** to close it.
7. During seconds 10–20 of the simulation, observe the simulator trigger an outage, buffer readings locally, reconnect at second 20, and replay the batch cleanly without losing telemetry or rolling back timestamps.

---

## 6. Production Deployment & Scaling Notes

- **API Layer:** Stateless FastAPI containers deployed behind an Application Load Balancer (ALB) or Nginx ingress. Multiple replicas can run concurrently.
- **Worker Scaling:** Background workers can scale horizontally; concurrent claiming is protected by PostgreSQL FOR UPDATE SKIP LOCKED.
- **Database:** PostgreSQL with read-replicas for read queries (GET /api/devices, /api/incidents) and partitioned events table by occurrence month (PARTITION BY RANGE (event_time)).
- **Broadcaster in Multi-Instance Deployments:** In a multi-replica API cluster, replace the in-memory broadcaster with PostgreSQL LISTEN / NOTIFY or Redis Pub/Sub so SSE clients connected to any server instance receive worker updates.
