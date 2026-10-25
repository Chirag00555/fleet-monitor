# Architecture Notes

> **Fleet Device Operations Platform** — Engineering Architecture Notes  
> Based on source code inspection of the implemented codebase.  
> All claims below are verified against actual code, not documentation or README.

---

## 1. System Overview

The platform is a real-time fleet monitoring system structured around five distinct runtime processes that communicate through PostgreSQL as the durable backbone and an in-memory broadcaster for live dashboard updates.

```
[Device Simulators] → HTTP → [FastAPI Backend + Worker] → PostgreSQL
                                         ↓ (in-process)
                              [In-Memory SSE Broadcaster]
                                         ↓
                              [React Dashboard via EventSource]
```

**Runtime processes and containers:**

| Service | Container | Role |
|---|---|---|
| `fleet_postgres` | postgres:16-alpine | Durable data store, work queue |
| `fleet_backend` | python:3.13-slim | FastAPI API server + embedded asyncio worker |
| `fleet_worker` | python:3.13-slim | Optional standalone worker (same image) |
| `fleet_sim_freezer/conveyor/generator` | python:3.13-slim | Device simulators |
| React Dashboard | Vite dev server (not containerised) | Operator UI |

The most significant architectural detail: the background worker is **not** a separate process in the normal deployment path. It runs as an `asyncio.create_task` inside the FastAPI lifespan (`app/main.py`), sharing the same Python event loop and the same in-memory `Broadcaster` instance. This means worker-originated SSE notifications (incident creation, silence alerts) reach connected browsers without any inter-process hop or message broker.

---

## 2. Key Decisions and Alternatives

### 2.1 Overall Architecture: Embedded Worker vs. Separate Worker Process

**Decision:** The background rule-evaluation worker runs as an `asyncio.create_task` launched in the FastAPI `lifespan` context (`app/main.py`). A standalone `fleet_worker` Docker service using `python -m app.worker.processor` also exists in `docker-compose.yml` but is a separate container that does **not** share the same broadcaster.

**Why it fits:** For a prototype, co-locating the worker with the API server eliminates the need for a message broker (Redis, RabbitMQ, Kafka) to bridge SSE notifications across processes. The broadcaster is an `asyncio.Queue`-per-subscriber structure that only works within a single event loop.

**Alternative considered:** Running the worker as a truly separate process and publishing notifications through a Redis pub/sub channel that the SSE endpoint subscribes to. This would scale horizontally and survive API restarts without losing notifications.

**Trade-off:** The current design means that if the API server crashes, the worker also stops. SSE notifications from the standalone `fleet_worker` container are silently dropped because that container's broadcaster has no SSE subscribers — it is an isolated process. The compose file includes the standalone `fleet_worker` service, but it should not be used alongside `fleet_backend` if live notifications are required.

**Status:** Implemented (integrated path). Standalone worker is implemented but does not deliver SSE.

---

### 2.2 Device Communication: HTTP POST with Custom Token Header

**Decision:** Devices send telemetry via HTTP POST to `/api/events` (single) or `/api/events/batch` (batch replay). Authentication uses a custom header `X-Device-Token`. The API also accepts `Authorization: Bearer <token>` as a fallback.

**Why it fits:** HTTP is universally supported on constrained edge devices; no persistent connection or broker is needed. The custom token header is simple to implement in a Python `httpx` client and easy to rotate.

**Alternative considered:** MQTT with TLS client certificates — the standard in production IoT fleets. MQTT provides guaranteed delivery semantics and lower overhead for high-frequency sensors.

**Trade-off:** HTTP is stateless; the server has no push channel back to the device. If a device needs remote commands or configuration, a separate mechanism would be required. HTTP also adds per-request overhead unsuitable for very high-frequency sensors (>10 Hz). Not an issue for this prototype at 2–4 second intervals.

**Status:** Implemented.

---

### 2.3 Event Schema: Client-Assigned UUIDs

**Decision:** Each event carries a client-generated `event_id` (UUIDv4 string), a device-assigned `event_time` (ISO 8601 with timezone), an `event_type` string, and a free-form JSON `payload`. The server also records `received_at` (server wall clock).

**Why it fits:** Client-assigned IDs enable idempotent re-delivery — the same event can be submitted multiple times after a network failure without being double-stored, because the server uses `UNIQUE(device_id, event_id)` to detect duplicates.

**Alternative considered:** Server-assigned IDs. Simpler server logic, but loses idempotency — a device cannot safely retry without risking duplicate processing.

**Trade-off:** The client must generate stable UUIDs. The simulator uses `uuid.uuid4()` at generation time and holds them in its local buffer, so buffered-and-replayed events retain the same IDs. If a simulator crashes mid-buffer, the buffer is lost and events are permanently dropped (no on-disk buffer).

**Status:** Implemented. The simulator buffer is in-memory only — not persisted to disk.

---

### 2.4 Device Identity, Registration, and Pairing

**Decision:** Devices are pre-registered in the database by an operator via `POST /api/devices`. A separate pairing flow then issues a one-time 8-character code via `POST /api/devices/pairing/generate`. The edge device calls `POST /api/devices/pairing/claim` with the code to receive a `dev_`-prefixed API token, which is stored in `devices.api_token`. The simulator persists this token to `.device_<id>_creds.json` on a host-volume mount.

**Why it fits:** Separates device identity (operator-controlled) from device authentication (device-initiated), which is the correct pattern for zero-touch provisioning.

**Alternative considered:** Pre-provisioned tokens injected via Docker environment variables. Simpler but inflexible — each new device requires redeployment.

**Trade-off and limitations:**
- Pairing codes are stored and validated in the database but there is no periodic job to clean up expired unused codes. Expired codes are checked at claim time only.
- Token revocation deletes `devices.api_token` (via `DELETE /api/devices/{id}` or `PATCH`), which correctly blocks future ingestion. There is no token blacklist — if a token leaks, the only remedy is deletion or device revocation.
- Device registration (`POST /api/devices`) does **not** require operator JWT authentication in the current code. Any HTTP client can register a new device.

**Status:** Implemented and tested (`test_pairing_code_generation_and_claim` — 1 test passing).

---

### 2.5 Event Validation, Duplicate Handling, and Out-of-Order Timestamps

**Decision (validation):** FastAPI Pydantic schemas validate that `event_id`, `device_id`, `event_type`, and `event_time` are present and correctly typed before reaching the database. Missing or non-parseable fields return HTTP 422.

**Decision (duplicates):** The `UNIQUE(device_id, event_id)` database constraint is the canonical deduplication boundary. Before inserting, `ingest_service.py` runs a `SELECT` to check for an existing record and returns `status: "duplicate"` without inserting or creating a work item. The constraint also acts as a safety net if the pre-check races.

**Decision (out-of-order / delayed events):** `device.latest_report_time` is only advanced forward: `if event_time > device.latest_report_time: update`. Late-arriving events from a network replay are accepted, stored, and processed by the worker, but they do not roll back the device's apparent last-seen time. This prevents a 30-second-old buffered event from making an online device appear silent.

**Alternative considered:** A server-side sequence number per device. More robust against clock skew but requires the device to maintain a persistent counter.

**Trade-off:** The system trusts the device clock for `event_time`. A device with a drifted clock will produce events with incorrect timestamps. The `received_at` column (server wall clock) is always recorded accurately and could be used as a fallback.

**Status:** Implemented and tested (7 relevant tests pass).

---

### 2.6 Database: PostgreSQL with Async SQLAlchemy and Alembic

**Decision:** PostgreSQL 16 via `asyncpg` driver with SQLAlchemy 2.0 async ORM. Schema managed by 4 Alembic migration files (`0001–0004`). Connection pool: `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`.

**Why it fits:** PostgreSQL provides `FOR UPDATE SKIP LOCKED` for the work queue pattern, JSON column storage for flexible event payloads, and reliable ACID transactions for the atomic event+work-item insert.

**Alternative considered:** A managed queue service (SQS, RabbitMQ) for the work queue, paired with a simpler relational store. Reduces code complexity but introduces an external dependency and loses the ability to inspect queue state via SQL.

**Trade-off:** Running the queue in PostgreSQL means the database is both the operational store and the work queue, so heavy queue load competes with read queries. For a prototype with three devices this is irrelevant; at scale a separate queue would be preferable.

**Tests run against SQLite (in-memory):** `conftest.py` uses `sqlite+aiosqlite:///:memory:`. This means `FOR UPDATE SKIP LOCKED` is not executed in tests — the `try/except` in `claim_next_work_items` silently omits the lock clause on SQLite. Correctness of locking behavior is untested in the automated suite.

**Status:** Implemented. Migration chain fully applied in production via `alembic upgrade head` in the Docker command.

---

### 2.7 Background Work Queue, Retries, and Delivery Guarantees

**Decision:** Each accepted event gets a corresponding `WorkItem` row inserted atomically in the same transaction. The worker polls with `FOR UPDATE SKIP LOCKED`, processes items one at a time, and commits `status=completed`. Failures trigger exponential backoff (`2^attempts` seconds, capped at 300 s) up to `max_attempts=5`, after which the item is permanently marked `failed`.

**Stale-lock recovery:** Items stuck in `status=processing` with `locked_at < now - 5 minutes` are reclaimed on the next poll. This recovers from worker crashes or event loop hangs.

**Delivery guarantee — what it actually is:** At-least-once processing within the `max_attempts` budget. A work item is committed as `completed` only after rule evaluation succeeds. If the server crashes after event ingestion but before the work item is processed, the item will be picked up on the next worker startup. If the server crashes after rule evaluation but before the final `status=completed` commit, the item will be re-processed — this could create a duplicate incident if the database commit for the incident succeeded but the work-item commit did not. The rules engine uses `dedup_key` to prevent duplicate incidents on retry, so in practice rule outcomes are idempotent (`test_worker_retry_idempotency_for_incidents` verifies this). However, the system does **not** guarantee exactly-once event processing in the strict distributed-systems sense.

**Alternative considered:** Transactional outbox with a separate inbox table and a change-data-capture pipeline. Stronger guarantees but significantly more infrastructure.

**Status:** Implemented and tested.

---

### 2.8 Rule Evaluation

**Decision:** Two rule types are implemented:
- `temperature_threshold`: evaluates a named field in `event.payload` against a numeric threshold using one of five comparators (`>`, `>=`, `<`, `<=`, `==`). The field defaults to `"temperature"` but is configurable via `Rule.payload_field`.
- `event_type`: matches `event.event_type` against `Rule.target_event_type` exactly.

Rules can be scoped to a specific `device_id` or applied globally (`device_id IS NULL`). Disabled rules are filtered at query time (`WHERE is_enabled = TRUE`).

**Alternative considered:** A DSL or expression language (e.g., JSONPath conditions). More powerful but significantly more complex to implement and test safely.

**Trade-off:** Rules are simple — there is no compound logic (AND/OR of multiple conditions), no rate-based alerting (e.g., "temperature > 80 for 3 consecutive readings"), and no statistical rules. Adding new rule types requires a new branch in `rules_engine.py`.

**Status:** Implemented and tested.

---

### 2.9 Device Silence Detection

**Decision:** `run_device_silence_check()` is called on a configurable interval (`SILENCE_CHECK_INTERVAL_SECONDS`, default 10 s, from `app/core/config.py`). For each enabled `device_silence` rule, the function compares `device.latest_report_time` against `now - silence_duration_seconds`. If the threshold is exceeded, it opens or updates a silence incident.

**Never-reported devices:** If `device.latest_report_time IS NULL`, the function falls back to `device.created_at`. A newly registered device that has never reported will trigger a silence incident after `silence_duration_seconds` from its creation time. This is intentional — operators should know a device is not reporting.

**Reconnect resolution:** When any event is successfully processed for a device that has open silence incidents, `evaluate_device_reconnect()` resolves them with a note containing the recovery timestamp.

**Check interval vs. silence threshold:** These are independent settings. The check interval (10 s) controls how often the worker wakes to scan for silence; the rule's `silence_duration_seconds` controls the actual alerting threshold. With a 10 s check interval and a 30 s silence threshold, detection latency is at most 10 s. With very short thresholds (< 10 s), the check interval becomes the effective minimum detection latency.

**Alternative considered:** A database trigger or scheduled job (pg_cron). Avoids the polling loop but adds infrastructure complexity.

**Trade-off:** The polling approach means detection latency is bounded by `SILENCE_CHECK_INTERVAL_SECONDS`. For a 3-second reporting device with a 10-second silence threshold, detection could occur up to 10 seconds after the threshold is crossed.

**Status:** Implemented and tested.

---

### 2.10 Incident Lifecycle and Deduplication

**Decision:** Incidents use a `dedup_key` of `"{rule_id}:{device_id}"` with a `UNIQUE` database constraint. When the rules engine finds a rule match:
- If an open or acknowledged incident exists for that `dedup_key`, it updates `event_count` and `last_event_at` — it does **not** create a new incident.
- If no active incident exists (first occurrence, or after a previous resolution), a new incident is created.

Resolving an incident clears `dedup_key` to `NULL`, allowing future rule matches to open a new incident for the same rule+device pair.

Incidents carry associated events in the `incident_events` join table, so an operator can inspect exactly which telemetry readings triggered or extended the incident.

**State machine:** `open` → `acknowledged` → `resolved`. Only `open` incidents can be acknowledged; both `open` and `acknowledged` can be resolved. Attempting an invalid transition returns HTTP 400.

**Alternative considered:** Per-event incidents with a separate grouping view. Simpler insert logic but poor operator UX — an alert firing every 3 seconds would generate hundreds of rows.

**Trade-off:** The deduplication means an operator sees one incident per active rule+device pair, with an escalating event count. If a rule fires, then goes quiet without resolution, then fires again after the silence clears a new incident opens — which is the correct behavior.

**Status:** Fully implemented and tested.

---

### 2.11 Dashboard / SSE Live Updates

**Decision:** The operator dashboard subscribes to `GET /api/events/stream` using the browser `EventSource` API. The server generates a `text/event-stream` response from a named-event async generator. Events are named (`event_received`, `incident_created`, etc.) so the frontend can use `addEventListener` selectively.

The broadcaster is a singleton `asyncio.Queue`-per-subscriber structure (`app/core/broadcaster.py`). Each queue is bounded to 100 messages; on overflow the oldest message is dropped and replaced with the newest.

**SSE reconnect behavior (frontend):** `eventSource.onerror` closes the connection and schedules `connectSSE()` after 3 seconds. On reconnect, `fetchAllState()` is called immediately via `eventSource.onopen` to reconcile any missed events during the disconnection window.

**SSE heartbeat:** The server emits `": heartbeat <timestamp>"` comment lines every 1 second while no events are pending (`asyncio.TimeoutError` on queue read). This keeps proxies and load balancers from closing idle connections.

**Cross-process limitation:** As stated in §2.1, only the embedded worker (running inside `fleet_backend`) shares the broadcaster. The standalone `fleet_worker` container has its own isolated broadcaster with no SSE subscribers. Its broadcasts are silently dropped.

**Alternative considered:** WebSockets for bidirectional communication. Unnecessary for a read-only dashboard push pattern; SSE is simpler and survives HTTP/1.1 proxies better.

**Status:** Implemented and tested (`test_sse_endpoint_delivery` passes).

---

### 2.12 External Notification Integration

**Decision:** No external notifications are implemented. There is no email, Slack, PagerDuty, or webhook integration.

**Status:** Not implemented. Not shown in the architecture diagram.

---

### 2.13 Local Development and Container Setup

**Decision:** `docker-compose.yml` defines the full stack. For local development without Docker, the backend runs with `uv run uvicorn app.main:app --reload` against a local PostgreSQL instance configured via `.env`. The React dashboard runs with `npx vite --host 0.0.0.0`.

Migrations are applied automatically: the backend container entrypoint runs `alembic upgrade head && uvicorn ...`. The `alembic/env.py` reads the same `settings.get_sync_database_url()` as the application.

**Operational visibility:** There is no metrics endpoint, no distributed tracing, and no health-check dashboard beyond the `/health` (liveness) and `/ready` (readiness: queries `SELECT 1`) endpoints. Logs are plain text to stdout, readable via `docker logs`.

**Status:** Implemented.

---

### 2.14 Testing Strategy

**Approach:** 27 automated pytest tests using `pytest-asyncio`. The test database is SQLite in-memory (`sqlite+aiosqlite:///:memory:`), set up fresh for each test via `autouse` fixture. HTTP layer is exercised via `httpx.AsyncClient` with `ASGITransport` (no network). Worker functions are imported and called directly.

**Coverage highlights:**
- Auth: login success/failure, JWT validation, 401 on missing token.
- Ingestion: single event, batch, duplicates, malformed payloads, unregistered devices, out-of-order timestamps.
- Rules: temperature threshold (all comparators), event-type matching, disabled rules, malformed fields.
- Incidents: deduplication, lifecycle state machine (open→ack→resolve), associated events, idempotent re-processing.
- Resilience: atomic rollback on commit failure, exponential backoff, silence detection and reconnect resolution.
- SSE: broadcaster dispatch, endpoint delivery, stream content-type.
- Simulator: UUID stability, payload schema, offline buffering.

**Known untested scenarios:**
- `FOR UPDATE SKIP LOCKED` locking behavior (SQLite does not support it; the code silently drops the clause via `try/except`).
- Concurrent worker processes claiming the same batch — no concurrency test exists.
- SSE client disconnect and broadcaster unsubscribe under load.
- Pairing code expiry enforcement (expiry is stored but only checked at claim time; no test validates this).
- The standalone `fleet_worker` container's SSE notification gap.
- Simulator crash recovery (buffer is in-memory; a crashed simulator loses all buffered events).
- Frontend behavior under SSE reconnect — no frontend tests at all.

**Status:** 27/27 tests pass. Test database differs from production (SQLite vs. PostgreSQL).

---

## 3. Reliability and Data-Consistency Guarantees

| Guarantee | What the code actually provides |
|---|---|
| **Event ingestion atomicity** | `Event` and `WorkItem` are inserted in a single `session.commit()`. If the commit fails, neither is persisted (verified by `test_atomic_transaction_rollback_on_failure`). |
| **Duplicate event safety** | `UNIQUE(device_id, event_id)` prevents double-storage. Pre-insert SELECT check returns `"duplicate"` without creating a work item. |
| **At-least-once processing** | Work items survive backend restarts. Stale locks are reclaimed after 5 minutes. Items are retried up to 5 times with exponential backoff. |
| **Not exactly-once processing** | A crash between incident-insert commit and work-item-complete commit causes the item to be re-processed. `dedup_key` prevents duplicate incidents on retry, but SSE `incident_created` could be re-broadcast. |
| **Incident deduplication** | Guaranteed by `UNIQUE(dedup_key)` database constraint plus upsert logic. |
| **Monotonic device timestamp** | `latest_report_time` only advances. Late-arriving replayed events do not cause false silence detection. |
| **SSE delivery** | Best-effort in-memory delivery. If the browser is disconnected, events during the gap are missed. `fetchAllState()` on reconnect reconciles incidents and devices but not the telemetry event feed. |
| **SSE back-pressure** | Per-client queue bounded at 100 messages. Overflow drops oldest message. A very slow browser client can lose events. |

---

## 4. Security and Prototype Limitations

### Implemented
- **Device authentication:** Devices must supply `X-Device-Token` matching `devices.api_token`. Requests without a token or with an incorrect token return HTTP 401. Devices without an `api_token` (pre-pairing) are accepted without a token — by design during the initial registration window.
- **Operator authentication:** Operators log in via `POST /api/auth/login` with username/password (bcrypt-hashed). A HS256 JWT (24-hour expiry) is returned and validated on `GET /api/auth/me`.
- **CORS:** `allow_origins=["*"]` — permissive for development. All origins accepted.

### Not Implemented (prototype limitations)
- **JWT not enforced on operator CRUD routes.** `GET/POST /api/devices`, `POST /api/rules`, `GET/POST /api/incidents`, and all acknowledge/resolve endpoints accept requests without a JWT. Only `GET /api/auth/me` enforces operator authentication. An unauthenticated client can register devices, create rules, and resolve incidents.
- **No HTTPS / TLS.** All traffic is plaintext HTTP. Device tokens and JWTs are transmitted unencrypted.
- **No rate limiting.** A device can flood `/api/events` with unlimited requests, growing the `work_items` table without bound.
- **Weak AUTH_SECRET_KEY default.** `auth.py` falls back to a hardcoded string if `AUTH_SECRET_KEY` is not set. Acceptable for development, unacceptable for any exposed environment.
- **No token expiry for device tokens.** Once issued, a device API token is valid indefinitely until the device record is deleted or the token is explicitly revoked.
- **Pairing code expiry not enforced asynchronously.** Expired codes are rejected at claim time only; no cleanup job removes them from the database.

---

## 5. Known Limitations and Future Improvements

| Area | Current limitation | Improvement path |
|---|---|---|
| SSE cross-process | Worker in separate process cannot deliver SSE | Add Redis pub/sub as broadcast transport |
| Rule management | No `PATCH` or `DELETE` for rules | Add rule CRUD endpoints |
| JWT enforcement | Operator CRUD unprotected | Add `Depends(get_current_operator)` to device/rule/incident mutating endpoints |
| Rate limiting | None | Add `slowapi` or a reverse-proxy layer |
| Rule expressiveness | No compound conditions, no rate-based rules | DSL or RETE engine |
| Simulator resilience | In-memory buffer lost on crash | Persist buffer to local SQLite file |
| Frontend persistence | JWT in `localStorage` cleared on browser clear; no refresh token | Add refresh token or session cookie |
| Metrics and tracing | No Prometheus or OpenTelemetry | Add `/metrics` endpoint, structured JSON logs |
| Test database parity | SQLite does not test `SKIP LOCKED` | Run a subset of tests against PostgreSQL in CI |
| Notification delivery | No external notifications | Webhook or email integration via rule action |
