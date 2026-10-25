import json
import asyncio
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from app.db.session import get_db
from app.core.config import settings
from app.core.broadcaster import broadcaster
from app.models.entities import Device, Rule, Incident, IncidentEvent, Event
from app.schemas.events import (
    EventIngestItem,
    EventBatchIngestRequest,
    SingleIngestResponse,
    BatchIngestResponse,
    DeviceRegistrationRequest,
    DeviceResponse,
)
from app.schemas.rules_incidents import (
    RuleCreateRequest,
    RuleResponse,
    IncidentResponse,
    IncidentDetailResponse,
    IncidentTransitionRequest,
    IncidentEventDetail,
)
from app.api.ingest_service import ingest_events_batch

router = APIRouter(prefix="/api", tags=["fleet-ops"])

# ----------------- SERVER-SENT EVENTS (SSE) -----------------

@router.get("/events/stream", summary="Live Server-Sent Events stream for operations dashboard")
async def sse_event_stream(request: Request):
    """
    Server-Sent Events endpoint delivering real-time platform updates:
    - event_received: newly accepted device telemetry
    - incident_created: new active incident opened
    - incident_updated: existing active incident re-triggered (event attached)
    - incident_acknowledged / incident_resolved: state transitions
    """
    client_queue = broadcaster.subscribe()

    async def event_generator():
        try:
            yield f": connected to fleet-monitor live stream\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(client_queue.get(), timeout=1.0)
                    event_type = item["type"]
                    data_str = json.dumps(item["data"])
                    yield f"event: {event_type}\ndata: {data_str}\n\n"
                    # If this was a close_stream signal (e.g. from tests), exit cleanly
                    if event_type == "_close_stream":
                        break
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield f": heartbeat {datetime.now(timezone.utc).isoformat()}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            broadcaster.unsubscribe(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ----------------- EVENTS INGESTION -----------------

@router.post(
    "/events",
    response_model=SingleIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a single device event"
)
async def ingest_single_event(
    event: EventIngestItem,
    db: AsyncSession = Depends(get_db)
):
    try:
        accepted, duplicates, details = await ingest_events_batch(db, [event])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ingestion failed: {str(e)}")

    detail = details[0]
    return SingleIngestResponse(
        event_id=detail.event_id,
        status=detail.status,
        message=detail.message or ""
    )


@router.post(
    "/events/batch",
    response_model=BatchIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a batch of device events (for buffered replay)"
)
async def ingest_batch_events(
    request: EventBatchIngestRequest,
    db: AsyncSession = Depends(get_db)
):
    if len(request.events) > settings.MAX_EVENT_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch size {len(request.events)} exceeds maximum allowed limit of {settings.MAX_EVENT_BATCH_SIZE}"
        )

    try:
        accepted, duplicates, details = await ingest_events_batch(db, request.events)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Batch ingestion failed: {str(e)}")

    return BatchIngestResponse(
        accepted=accepted,
        duplicates=duplicates,
        total=len(request.events),
        details=details
    )


@router.get(
    "/events",
    summary="List recent telemetry events"
)
async def list_recent_events(
    limit: int = Query(50, ge=1, le=200),
    device_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Event).order_by(Event.received_at.desc()).limit(limit)
    if device_id:
        query = query.where(Event.device_id == device_id)
    result = await db.execute(query)
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "event_id": e.event_id,
            "device_id": e.device_id,
            "event_type": e.event_type,
            "event_time": e.event_time,
            "received_at": e.received_at,
            "payload": e.payload,
            "processing_status": e.processing_status,
        }
        for e in events
    ]


# ----------------- DEVICES -----------------

@router.post(
    "/devices",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new device"
)
async def register_device(
    payload: DeviceRegistrationRequest,
    db: AsyncSession = Depends(get_db)
):
    existing = await db.get(Device, payload.id)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Device '{payload.id}' is already registered.")

    device = Device(
        id=payload.id,
        name=payload.name,
        location=payload.location,
        is_enabled=payload.is_enabled,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


@router.get(
    "/devices",
    response_model=List[DeviceResponse],
    summary="List all registered devices"
)
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.name))
    return result.scalars().all()


# ----------------- RULES MANAGEMENT -----------------

@router.post(
    "/rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an alert rule"
)
async def create_rule(
    payload: RuleCreateRequest,
    db: AsyncSession = Depends(get_db)
):
    if payload.device_id:
        dev = await db.get(Device, payload.device_id)
        if not dev:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Device '{payload.device_id}' not found."
            )

    rule = Rule(
        name=payload.name,
        is_enabled=payload.is_enabled,
        rule_type=payload.rule_type,
        device_id=payload.device_id,
        comparator=payload.comparator,
        threshold_value=payload.threshold_value,
        payload_field=payload.payload_field,
        target_event_type=payload.target_event_type,
        silence_duration_seconds=payload.silence_duration_seconds,
        severity=payload.severity,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get(
    "/rules",
    response_model=List[RuleResponse],
    summary="List all rules"
)
async def list_rules(
    is_enabled: Optional[bool] = None,
    rule_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Rule).order_by(Rule.created_at.desc())
    if is_enabled is not None:
        query = query.where(Rule.is_enabled == is_enabled)
    if rule_type is not None:
        query = query.where(Rule.rule_type == rule_type)

    result = await db.execute(query)
    return result.scalars().all()


# ----------------- INCIDENTS MANAGEMENT -----------------

@router.get(
    "/incidents",
    response_model=List[IncidentResponse],
    summary="List incidents with filters"
)
async def list_incidents(
    status: Optional[str] = Query(None, description="Comma-separated status values, e.g. open,acknowledged"),
    device_id: Optional[str] = None,
    rule_id: Optional[int] = None,
    severity: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Incident).order_by(Incident.opened_at.desc())
    if status:
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        query = query.where(Incident.status.in_(status_list))
    if device_id:
        query = query.where(Incident.device_id == device_id)
    if rule_id:
        query = query.where(Incident.rule_id == rule_id)
    if severity:
        query = query.where(Incident.severity == severity)

    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Get incident details with associated events"
)
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(Incident)
        .options(
            selectinload(Incident.incident_events).selectinload(IncidentEvent.event)
        )
        .where(Incident.id == incident_id)
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident #{incident_id} not found."
        )

    events_detail = []
    for assoc in incident.incident_events:
        if assoc.event:
            events_detail.append(IncidentEventDetail(
                id=assoc.event.id,
                event_id=assoc.event.event_id,
                event_type=assoc.event.event_type,
                event_time=assoc.event.event_time,
                received_at=assoc.event.received_at,
                payload=assoc.event.payload
            ))

    resp_dict = {c.name: getattr(incident, c.name) for c in Incident.__table__.columns}
    resp_dict["events"] = events_detail
    return IncidentDetailResponse(**resp_dict)


@router.post(
    "/incidents/{incident_id}/acknowledge",
    response_model=IncidentResponse,
    summary="Acknowledge an open incident"
)
async def acknowledge_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db)
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident #{incident_id} not found.")

    if incident.status != "open":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot acknowledge incident in '{incident.status}' state. Only 'open' incidents can be acknowledged."
        )

    now = datetime.now(timezone.utc)
    incident.status = "acknowledged"
    incident.acknowledged_at = now
    incident.updated_at = now
    await db.commit()
    await db.refresh(incident)

    await broadcaster.broadcast("incident_acknowledged", {
        "id": incident.id,
        "device_id": incident.device_id,
        "status": "acknowledged",
        "acknowledged_at": incident.acknowledged_at.isoformat()
    })
    return incident


@router.post(
    "/incidents/{incident_id}/resolve",
    response_model=IncidentResponse,
    summary="Resolve an open or acknowledged incident"
)
async def resolve_incident(
    incident_id: int,
    payload: IncidentTransitionRequest = IncidentTransitionRequest(),
    db: AsyncSession = Depends(get_db)
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident #{incident_id} not found.")

    if incident.status == "resolved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incident is already resolved."
        )

    now = datetime.now(timezone.utc)
    incident.status = "resolved"
    incident.resolved_at = now
    incident.updated_at = now
    incident.dedup_key = None
    if payload.resolution_note:
        incident.resolution_note = payload.resolution_note

    await db.commit()
    await db.refresh(incident)

    await broadcaster.broadcast("incident_resolved", {
        "id": incident.id,
        "device_id": incident.device_id,
        "status": "resolved",
        "resolution_note": incident.resolution_note,
        "resolved_at": incident.resolved_at.isoformat()
    })
    return incident
# ----------------- OPERATOR AUTHENTICATION -----------------

from app.schemas.auth import LoginRequest, TokenResponse, OperatorResponse
from app.core.auth import (
    verify_password,
    create_access_token,
    get_current_operator,
    ensure_default_operator,
)
from app.models.entities import Operator

@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["auth"],
    summary="Operator login returning access token"
)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Make sure default admin exists in dev
    await ensure_default_operator(db)

    result = await db.execute(select(Operator).where(Operator.username == credentials.username))
    operator = result.scalar_one_or_none()

    if not operator or not verify_password(credentials.password, operator.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not operator.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator account is inactive"
        )

    access_token = create_access_token(data={"sub": operator.username, "role": operator.role})
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        operator=OperatorResponse.model_validate(operator)
    )

@router.get(
    "/auth/me",
    response_model=OperatorResponse,
    tags=["auth"],
    summary="Get current authenticated operator profile"
)
async def get_me(current_operator: Operator = Depends(get_current_operator)):
    return OperatorResponse.model_validate(current_operator)
