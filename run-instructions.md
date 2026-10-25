# Run Instructions — Fleet Device Operations Platform

> **Target Audience:** Evaluating engineers and reviewers.  
> **Repository Root:** `C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment`  
> **Platform / Shell:** Windows 10/11 (PowerShell) or cross-platform POSIX bash.

---

## Table of Contents

1. [Prerequisites & Verification](#1-prerequisites--verification)
2. [First-Time Setup](#2-first-time-setup)
3. [Starting the Application](#3-starting-the-application)
   - [Option A: Local Hybrid Run (Recommended)](#option-a-local-hybrid-run-recommended)
   - [Option B: Full Docker Compose Run](#option-b-full-docker-compose-run)
4. [Running the Device Simulator](#4-running-the-device-simulator)
   - [Single Simulator Execution](#single-simulator-execution)
   - [Multi-Device Container Fleet (3 Devices)](#multi-device-container-fleet-3-devices)
   - [New Device Pairing Flow](#new-device-pairing-flow)
5. [Evaluation Walkthrough](#5-evaluation-walkthrough)
   - [Live Telemetry & Dashboard Feed](#step-1-live-telemetry--dashboard-feed)
   - [Threshold Alert & Incident Lifecycle](#step-2-threshold-alert--incident-lifecycle)
   - [Fast Device Silence Verification (< 30 Seconds)](#step-3-fast-device-silence-verification--30-seconds)
   - [Offline Buffering & Burst Replay](#step-4-offline-buffering--burst-replay)
6. [Tests and Build Verification](#6-tests-and-build-verification)
7. [Troubleshooting Guide](#7-troubleshooting-guide)
8. [Shutdown and Cleanup](#8-shutdown-and-cleanup)
9. [Known Prototype Limitations](#9-known-prototype-limitations)

---

## 1. Prerequisites & Verification

Ensure the following runtimes and tools are installed before proceeding:

| Dependency | Minimum Version | Tested / Verified Version | Role | Verification Command |
|---|---|---|---|---|
| **Python** | `>= 3.11` | `3.13.12` | Backend & Simulator runtime | `uv run python --version` |
| **uv** | `>= 0.5.0` | `0.10.0` | Python package and virtualenv manager | `uv --version` |
| **Node.js** | `>= 18.0.0` | `22.17.1` | Frontend JavaScript runtime | `node --version` |
| **npm** | `>= 9.0.0` | `11.12.1` | Frontend package manager | `npm --version` |
| **Docker Desktop** | `>= 24.0.0` | `29.9.2` | PostgreSQL container & edge simulators | `docker --version` |

### Verifying Docker Desktop Status
Docker Desktop must be running if you use containerized PostgreSQL or the multi-device simulator fleet.
```powershell
docker info
```
* **If running:** Returns container stats and server version.
* **If not running:** Start "Docker Desktop" from the Windows Start menu and wait until the status bar indicator turns green.
* **If Docker is unavailable:** You can point `.env` to any existing PostgreSQL instance or run backend tests using SQLite (`uv run pytest`), which requires no Docker daemon.

---

## 2. First-Time Setup

### Step 2.1 — Open Project Root
Open a PowerShell terminal in the repository root:
```powershell
cd C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment
```

### Step 2.2 — Configure Environment File
Copy the example environment configuration:
```powershell
Copy-Item .env.example .env
```
Default `.env` configuration:
```ini
POSTGRES_SERVER=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=fleet_monitor
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fleet_monitor
DATABASE_SYNC_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/fleet_monitor
ENVIRONMENT=development
LOG_LEVEL=INFO
MAX_EVENT_BATCH_SIZE=200
```
> [!NOTE]
> If you start PostgreSQL via `docker compose up -d db` (which maps port `5433:5432` to avoid host conflicts), update `POSTGRES_PORT=5433` and the corresponding port numbers in `DATABASE_URL` and `DATABASE_SYNC_URL` in `.env`.

### Step 2.3 — Install Backend Dependencies
Use `uv` to automatically create the `.venv` and install locked dependencies from `uv.lock`:
```powershell
uv sync
```

### Step 2.4 — Install Frontend Dependencies
```powershell
cd frontend
npm install
cd ..
```

### Step 2.5 — Start PostgreSQL Database
Run PostgreSQL 16 using Docker:
```powershell
docker run --name local_postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=fleet_monitor -p 5432:5432 -d postgres:16-alpine
```
*(If a container named `local_postgres` already exists, start it with `docker start local_postgres`)*.

### Step 2.6 — Apply Database Migrations
Run Alembic to apply all migrations up to head (`0004_device_pairing`):
```powershell
uv run alembic upgrade head
```
Verify the current schema version:
```powershell
uv run alembic current
# Expected output: 0004_device_pairing (head)
```

---

## 3. Starting the Application

### Option A: Local Hybrid Run (Recommended)
This workflow offers the fastest reload cycles and full real-time Server-Sent Events (SSE) integration.

#### Terminal 1: Backend API + In-Process Worker
Run FastAPI with auto-reload:
```powershell
# Working Directory: C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
> [!IMPORTANT]
> The backend `lifespan` in `app/main.py` automatically initializes the background worker loop as an `asyncio` task within the same process. This allows rule evaluation, silence alerts, and event ingestion to share the in-memory `Broadcaster` singleton and stream live SSE events to connected browsers.

#### Terminal 2 (Optional): Standalone Worker Process
A standalone worker can be executed separately:
```powershell
# Working Directory: C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment
uv run python -m app.worker.processor
```
> [!WARNING]
> If run in a separate terminal process, the standalone worker maintains its own isolated `Broadcaster` instance. Events and incidents processed by the standalone worker will be persisted to PostgreSQL, but live SSE push updates will not reach browser clients connected to the API process. For full live UI updates, rely on the embedded worker in Terminal 1.

#### Terminal 3: Frontend Operations Dashboard
```powershell
# Working Directory: C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment\frontend
npm run dev
```

#### Application Endpoints & Verification URLs

| Service / Interface | Local URL | Description |
|---|---|---|
| **Operations Dashboard** | `http://localhost:5173` | React 19 + TypeScript operator console |
| **API Documentation (Swagger)** | `http://localhost:8000/docs` | Interactive OpenAPI documentation |
| **API Documentation (ReDoc)** | `http://localhost:8000/redoc` | Alternative ReDoc specification |
| **Process Liveness Probe** | `http://localhost:8000/health` | Returns `{"status":"healthy","environment":"development"}` |
| **Database Readiness Probe** | `http://localhost:8000/ready` | Returns `{"status":"ready","database":"connected"}` |
| **Live SSE Stream** | `http://localhost:8000/api/events/stream` | Continuous event-stream endpoint |

---

### Option B: Full Docker Compose Run
To launch the entire stack inside Docker containers:
```powershell
docker compose up -d
```
This builds and starts:
- `fleet_postgres` (mapped to host port `5433`)
- `fleet_backend` (mapped to host port `8001`)
- `fleet_worker` (standalone processor)
- `fleet_sim_freezer`, `fleet_sim_conveyor`, `fleet_sim_generator`

*To stop the compose stack:*
```powershell
docker compose stop
```

---

## 4. Running the Device Simulator

The simulator (`simulator/device_simulator.py`) generates realistic industrial telemetry, models threshold spikes, simulates network outages with local buffering, and handles device authentication/pairing.

### Single Simulator Execution (Pre-paired Device)
The repository includes credentials for pre-registered demonstration devices (`device-01-freezer`, `device-02-conveyor`, `device-03-generator`).

Run a 30-second simulation with `device-01-freezer`:
```powershell
uv run python -m simulator.device_simulator --device-id device-01-freezer --duration 30 --interval 2 --threshold 80
```
**Expected Terminal Output:**
```text
[INFO] [SIMULATOR]: Loaded persistent API token for device-01-freezer from .\.device_device-01-freezer_creds.json
[INFO] [SIMULATOR]: Starting simulation loop for 30s (interval=2.0s)...
[INFO] [SIMULATOR]: HTTP Request: POST http://localhost:8000/api/events "HTTP/1.1 202 Accepted"
[INFO] [SIMULATOR]: Sent event 8b2b3ea6-77b1-402a-b5d4-c44bd5d4dac4 (temp: 69.88) -> status: accepted
```

---

### Multi-Device Container Fleet (3 Devices)
The `docker-compose.yml` configures 3 distinct industrial edge devices:

| Container Name | Device ID | Display Name | Location | Reporting Rate | Spike Threshold |
|---|---|---|---|---|---|
| `fleet_sim_freezer` | `device-01-freezer` | Cryo-Freezer Unit 1 | Pharma Lab A | 3.0s | `80.0°C` |
| `fleet_sim_conveyor` | `device-02-conveyor` | Assembly Conveyor Motor | Factory Floor Bay 3 | 2.5s | `85.0°C` |
| `fleet_sim_generator` | `device-03-generator` | Backup Diesel Generator | Substation Outdoor | 4.0s | `90.0°C` |

#### Commands to Manage Fleet Simulators

* **Start all 3 devices:**
  ```powershell
  docker compose up -d simulator-freezer simulator-conveyor simulator-generator
  ```
* **Check running devices:**
  ```powershell
  docker ps --filter "name=fleet_sim"
  ```
* **View live logs for a specific device:**
  ```powershell
  docker logs -f fleet_sim_freezer
  ```
* **Stop all 3 devices at once:**
  ```powershell
  docker stop fleet_sim_freezer fleet_sim_conveyor fleet_sim_generator
  ```

---

### New Device Pairing Flow
When running a simulator for a completely new, unregistered device:

1. **Attempting to run without pairing:**
   ```powershell
   uv run python -m simulator.device_simulator --device-id device-new-sensor
   ```
   The simulator will abort with:
   `Device 'device-new-sensor' is NOT paired. Please generate a Pairing Code in the Operator Console.`

2. **Generate a Pairing Code via API or UI:**
   In PowerShell:
   ```powershell
   Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/api/devices/pairing/generate' `
     -ContentType 'application/json' `
     -Body '{"device_id":"device-new-sensor","device_name":"Calibration Unit 4","location":"Cleanroom B"}'
   ```
   *(Or click **+ Pair Device** in the dashboard UI at `http://localhost:5173`).*

3. **Start the Simulator with the Pairing Code:**
   ```powershell
   uv run python -m simulator.device_simulator --device-id device-new-sensor --pairing-code <8_CHAR_CODE>
   ```
   The simulator automatically claims the pairing code, receives its permanent `api_token`, writes `.device_device-new-sensor_creds.json` locally, and begins transmitting telemetry.

---

## 5. Evaluation Walkthrough

Follow this step-by-step 5-minute walkthrough to evaluate all key capabilities.

### Step 1: Live Telemetry & Dashboard Feed
1. Open `http://localhost:5173` in your browser.
2. Log in using default operator credentials:
   * **Username:** `admin`
   * **Password:** `admin123`
3. Notice the **LIVE (SSE)** green indicator in the header.
4. Start the 3 fleet devices:
   ```powershell
   docker compose up -d simulator-freezer simulator-conveyor simulator-generator
   ```
5. Observe the **Fleet Devices** matrix update to show 3 `ONLINE` devices with real-time "last seen X s ago" counters, and the **Live Telemetry Event Feed** updating without page reloads.

### Step 2: Threshold Alert & Incident Lifecycle
1. Watch the telemetry feed. Within 15–30 seconds, a device will generate a reading exceeding 75.0°C or 85.0°C.
2. An incident card immediately appears under **Operational Incidents**:
   * Displays device name, trigger temperature, rule name, and timestamp.
   * Status is marked as `OPEN`.
3. Click on the incident row to open the **Incident Inspector Modal**. Review the triggering event payload and measurement timestamp.
4. Click **Acknowledge**:
   * Status transitions to `ACKNOWLEDGED`.
5. Click **Resolve**:
   * Enter a resolution note (e.g. *"Re-calibrated coolant pressure"*).
   * Status transitions to `RESOLVED` and moves to the Resolved filter tab.

### Step 3: Fast Device Silence Verification (< 30 Seconds)
The system includes an active silence rule (`silence_duration_seconds: 20` seconds) and the worker evaluates device silence every 10 seconds (`SILENCE_CHECK_INTERVAL_SECONDS = 10.0`).

1. Stop one of the running devices:
   ```powershell
   docker stop fleet_sim_freezer
   ```
2. In the dashboard, watch the `device-01-freezer` card.
3. Within 20–30 seconds:
   * The device status badge changes from green `ONLINE` to amber/red `SILENT / OFFLINE`.
   * A new **Device Offline Silence Warning** incident is opened automatically.
4. Now restart the device container:
   ```powershell
   docker start fleet_sim_freezer
   ```
5. As soon as the first telemetry packet arrives:
   * The device badge turns green `ONLINE`.
   * The silence incident is **automatically resolved** with the note: *"Auto-resolved upon device telemetry recovery at &lt;timestamp&gt;"*.

### Step 4: Offline Buffering & Burst Replay
Verify edge resilience and monotonic timestamp integrity:
1. Run a dedicated simulator instance configured with a simulated network drop:
   ```powershell
   uv run python -m simulator.device_simulator --device-id device-01-freezer --duration 25 --interval 2 --disconnect-at 6 --disconnect-duration 8
   ```
2. Observe the terminal logs:
   * **Seconds 0–6:** Normal transmission (`202 Accepted`).
   * **Seconds 6–14:** Outage simulated. Events are buffered locally:  
     `[BUFFERED] Network offline. Buffered 1 events (total in queue: 4)`.
   * **Second 14:** Network recovers. Buffered events are flushed in a single batch:  
     `Flushing 4 buffered events to /api/events/batch... Batch replay accepted: 4 events`.
3. In the UI, notice all buffered readings appear in the event stream with their original edge measurement timestamps preserved.

---

## 6. Tests and Build Verification

### Automated Backend Tests
Run the comprehensive test suite (27 tests covering auth, ingestion, rules, silence engine, idempotency, and SSE):
```powershell
uv run pytest -v --tb=short
```
* **Verified Result:** ✅ **27 passed in 3.10s** (0 failed, 0 warnings).

### Frontend Build
Verify TypeScript compilation and Vite production bundling:
```powershell
cd frontend
npm run build
```
* **Verified Result:** ✅ `tsc -b && vite build` built in 507ms; generates `dist/index.html` and assets.

### Frontend Linter
```powershell
cd frontend
npm run lint
```
* **Verified Result:** ✅ 0 errors (12 warnings for unused variables/lint hooks).

---

## 7. Troubleshooting Guide

| Issue | Root Cause | Diagnosis & Recovery |
|---|---|---|
| **Docker daemon unavailable** | Docker Desktop is not running or starting up. | Run `docker info`. If it fails, start Docker Desktop from the Start menu. If Docker is unavailable, run backend tests via `uv run pytest` (uses in-memory SQLite). |
| **Port 5432 already occupied** | A local PostgreSQL service is already bound to port 5432. | Check listener: `netstat -ano \| findstr :5432`. Either stop the conflicting container (`docker stop local_postgres`) or use port 5433 via `docker compose up -d db` and update `.env` to `POSTGRES_PORT=5433`. |
| **Port 8000 already occupied** | A previous `uvicorn` instance is still running in the background. | In PowerShell: `Get-Process python \| Stop-Process -Force` or find PID: `netstat -ano \| findstr :8000` then `Stop-Process -Id <PID>`. |
| **Missing database tables (`relation "devices" does not exist`)** | Alembic migrations have not been applied to the database. | Run `uv run alembic upgrade head`. Verify with `uv run alembic current`. |
| **Simulator 401 Unauthorized (`Invalid device API token`)** | The device credentials file is missing or contains an invalid token. | Delete the invalid credentials file (`Remove-Item .\.device_<device_id>_creds.json`), generate a new pairing code in the UI, and run with `--pairing-code <CODE>`. |
| **SSE shows "RECONNECTING..." badge** | Backend API on port 8000 is not reachable from the browser. | Verify backend terminal is active and probe `http://localhost:8000/health`. Check browser developer console for CORS or network errors. |
| **Incidents not appearing in real time** | Running worker in separate process without shared broadcaster. | Ensure Terminal 1 is running `uvicorn app.main:app`. Do not rely solely on the standalone worker in Terminal 2 for live SSE. |

---

## 8. Shutdown and Cleanup

### Graceful Process Shutdown
* **FastAPI Backend (Terminal 1):** Press `Ctrl+C` to trigger graceful lifespan shutdown.
* **Frontend Dev Server (Terminal 3):** Press `Ctrl+C`.
* **Fleet Device Simulators:**
  ```powershell
  docker stop fleet_sim_freezer fleet_sim_conveyor fleet_sim_generator
  ```
* **PostgreSQL Container:**
  ```powershell
  docker stop local_postgres
  ```

### Docker Compose Shutdown
If you started the application via `docker compose up`:
```powershell
docker compose stop
```

> [!CAUTION]
> Do **NOT** run `docker compose down -v` unless you intend to destroy all persisted PostgreSQL database records, incidents, and devices. Running `docker compose down` (without `-v`) safely stops and removes containers while preserving the `postgres_data` volume.

---

## 9. Known Prototype Limitations

For complete technical rationale, see [`architecture-notes.md`](file:///C:/Users/chira/OneDrive/Desktop/Appscrip-Assignment/architecture-notes.md):

1. **In-Memory SSE Broadcaster:** Live SSE notifications require the background worker to run embedded within the FastAPI process (`app/main.py`). The standalone worker container (`fleet_worker`) does not share memory with the API server and cannot publish to SSE subscribers without a multi-instance pub/sub broker like Redis.
2. **Operator Authorization Scope:** Operator JWT login is verified on `GET /api/auth/me`, but mutating REST endpoints (`/api/devices`, `/api/rules`, `/api/incidents`) do not currently enforce JWT authorization guards.
3. **Rule REST APIs:** Only `POST /api/rules` (create) and `GET /api/rules` (list) are implemented. Individual `GET`, `PATCH`, and `DELETE` endpoints for rules are not implemented.
4. **Simulator Buffer Storage:** Offline buffering in `device_simulator.py` is stored in process memory. If the simulator process is forcefully terminated while disconnected, buffered events are lost.
5. **External Notifications:** No third-party dispatch integrations (email, SMS, Slack, PagerDuty) are implemented; all alerts are consumed via the Operations Console.
