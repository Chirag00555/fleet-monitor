# Deployment Notes — Fleet Operations Platform

> **Scope:** Practical production guide and prototype constraints  
> **Repository Root:** `C:\Users\chira\OneDrive\Desktop\Appscrip-Assignment`

---

## 1. Deployment Topology

### Current Prototype
- **Frontend:** React 19 + TypeScript SPA built to static assets (`dist/`) via `npm run build` (or served via Vite dev server on port `5173`).
- **Backend API & Embedded Worker:** FastAPI running under Uvicorn (`app/main.py`) on port `8000`. The worker runs as an in-process `asyncio` task within the application lifespan, sharing an in-memory `Broadcaster` singleton for real-time Server-Sent Events (SSE).
- **Database:** PostgreSQL 16 container (`postgres:16-alpine`) on port `5432` (or `5433` via compose).
- **Configuration:** Managed via `.env` and `pydantic-settings` (`app/core/config.py`).

### Recommended Production Target
- **Frontend:** Static assets (`dist/`) deployed to an edge CDN (Cloudflare Pages, AWS CloudFront + S3, or Nginx).
- **API Instances:** Containerized FastAPI replicas behind an Application Load Balancer (ALB / Ingress) terminating TLS.
- **Worker Service:** Standalone worker containers running `python -m app.worker.processor`, decoupled from API pods.
- **Database:** Managed PostgreSQL (e.g., AWS RDS / GCP Cloud SQL) with automated point-in-time recovery and PgBouncer connection pooling.
- **Secrets:** Injected via secret stores (AWS Secrets Manager, Vault) instead of plain `.env` files.

---

## 2. Monitoring & Observability

### Health Checks (Implemented)
- **Liveness:** `GET /health` (`200 OK`) verifies FastAPI process responsiveness.
- **Readiness:** `GET /ready` (`200 OK`) verifies active database connectivity (`SELECT 1`). Gate traffic on this endpoint.

### Recommended Production Metrics & Alerts
- **API Performance:** Request rate, p95/p99 latency, and HTTP 5xx error rate (alert on 5xx > 1% over 5m).
- **Queue Backlog & Age:** Count of `work_items` with `status = 'pending'`, and maximum age (`now() - created_at`). Alert if backlog > 500 or oldest item > 60s.
- **Worker Failures:** Rate of work items reaching `status = 'failed'` (exhausted 5 retries).
- **Incident Volume:** Active incident counts grouped by severity and rule type.
- **Stale Devices:** Count of enabled devices where `now() - latest_report_time > silence_threshold`.
- **Structured Logs:** Centralized JSON logs tracking `event_id` and `device_id` across ingestion and worker stages.

---

## 3. Scaling & Bottlenecks

### API Instances & In-Memory SSE Constraint
- The API is stateless for ingestion and queries and scales horizontally.
- **Critical Prototype Constraint:** The SSE broadcaster uses an **in-memory queue per subscriber** (`app/services/broadcaster.py`). If multiple API instances run behind a load balancer, clients connected to Instance A will not receive events processed by Instance B.
- **Production Fix:** Introduce **Redis Pub/Sub** or PostgreSQL `LISTEN / NOTIFY` to bridge broadcasts across API replicas.

### Worker Scaling
- Background workers scale horizontally; concurrent claiming is protected by PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` on `work_items`.
- **Bottlenecks to Watch:**
  - Database connection pool exhaustion under high worker concurrency.
  - Table bloat in `work_items` without automated archival of completed tasks.
  - Row-lock contention on `devices` table when concurrent events update `latest_report_time`.

---

## 4. Reliability & Security

- **Database Migrations:** Run `uv run alembic upgrade head` as a pre-deployment step or CI/CD job prior to traffic switch.
- **Backups:** Daily snapshots plus continuous WAL archiving (RPO < 5 min).
- **TLS:** Terminate TLS 1.3 at the reverse proxy / ALB. The prototype operates over plaintext HTTP.
- **Device Authentication:** Devices authenticate with `X-Device-Token` header. Rotation policies should be added in production.
- **Operator Auth Limitation:** JWT is currently enforced only on `GET /api/auth/me`. In production, add `Depends(get_current_operator)` to all mutating REST routes (`/api/devices`, `/api/rules`, `/api/incidents`).
- **Ingestion Protection:** Protect `POST /api/events` with token-bucket rate limiting (e.g., `slowapi`) to prevent denial of service from malfunctioning devices.
