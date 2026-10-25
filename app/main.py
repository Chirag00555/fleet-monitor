from fastapi import FastAPI, Depends, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.api.endpoints import router as api_router
from app.core.config import settings

app = FastAPI(
    title="Fleet Monitor Operations Platform",
    description="Real-Time Operations Platform for Device Fleet Telemetry and Incidents",
    version="0.1.0",
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
