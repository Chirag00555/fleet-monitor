from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db, async_session_factory
from app.api.endpoints import router as api_router
from app.core.config import settings
from app.core.auth import ensure_default_operator

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure default operator on startup
    async with async_session_factory() as session:
        await ensure_default_operator(session)
    # Start integrated background worker so incidents and silence checks share SSE broadcaster
    import asyncio
    from app.worker.processor import run_worker_loop
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_worker_loop(poll_interval=1.0, stop_event=stop_event))
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(worker_task, timeout=3.0)
        except Exception:
            pass

app = FastAPI(
    title="Fleet Monitor Operations Platform",
    description="Real-Time Operations Platform for Device Fleet Telemetry and Incidents",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for dashboard web app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.get("/health", tags=["system"])
async def health_check():
    """Liveness probe returning application process health."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
    }

@app.get("/ready", tags=["system"])
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness probe verifying active database connectivity."""
    try:
        await db.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "connected",
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(e)
            }
        )
