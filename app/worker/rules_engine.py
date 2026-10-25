import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import Event, Rule, Incident, IncidentEvent, Device
from app.core.broadcaster import broadcaster

logger = logging.getLogger("rules_engine")

def evaluate_temperature_condition(actual: float, comparator: str, threshold: float) -> bool:
    if comparator == ">":
        return actual > threshold
    elif comparator == ">=":
        return actual >= threshold
    elif comparator == "<":
        return actual < threshold
    elif comparator == "<=":
        return actual <= threshold
    elif comparator == "==":
        return actual == threshold
    return False


async def evaluate_event_rules(session: AsyncSession, event: Event) -> List[Incident]:
    rules_query = await session.execute(
        select(Rule).where(
            and_(
                Rule.is_enabled == True,
                or_(Rule.device_id == None, Rule.device_id == event.device_id)
            )
        )
    )
    rules = rules_query.scalars().all()
    created_or_updated_incidents: List[Incident] = []
    now = datetime.now(timezone.utc)

    for rule in rules:
        matched = False
        match_summary = ""

        if rule.rule_type == "temperature_threshold":
            payload_field = rule.payload_field or "temperature"
            if payload_field not in event.payload:
                continue

            raw_val = event.payload[payload_field]
            try:
                reading = float(raw_val)
            except (ValueError, TypeError):
                continue

            comp = rule.comparator or ">="
            thresh = rule.threshold_value if rule.threshold_value is not None else 0.0
            if evaluate_temperature_condition(reading, comp, thresh):
                matched = True
                match_summary = (
                    f"Temperature reading {reading} satisfied rule condition '{comp} {thresh}' "
                    f"(device '{event.device_id}')"
                )

        elif rule.rule_type == "event_type":
            if event.event_type == rule.target_event_type:
                matched = True
                match_summary = (
                    f"Event type '{event.event_type}' matched configured target '{rule.target_event_type}' "
                    f"(device '{event.device_id}')"
                )

        if not matched:
            continue

        dedup_key = f"{rule.id}:{event.device_id}"
        existing_incident_query = await session.execute(
            select(Incident).where(
                and_(
                    Incident.dedup_key == dedup_key,
                    Incident.status.in_(["open", "acknowledged"])
                )
            )
        )
        existing_incident = existing_incident_query.scalar_one_or_none()

        action_type = "incident_updated"
        if existing_incident:
            existing_incident.event_count += 1
            existing_incident.last_event_at = event.event_time
            existing_incident.updated_at = now
            existing_incident.summary = match_summary
            incident = existing_incident
        else:
            action_type = "incident_created"
            incident = Incident(
                rule_id=rule.id,
                device_id=event.device_id,
                status="open",
                severity=rule.severity,
                title=f"{rule.name} triggered on {event.device_id}",
                summary=match_summary,
                dedup_key=dedup_key,
                event_count=1,
                opened_at=now,
                last_event_at=event.event_time,
            )
            session.add(incident)
            await session.flush()

        existing_assoc_query = await session.execute(
            select(IncidentEvent).where(
                and_(
                    IncidentEvent.incident_id == incident.id,
                    IncidentEvent.event_pk == event.id
                )
            )
        )
        if not existing_assoc_query.scalar_one_or_none():
            assoc = IncidentEvent(
                incident_id=incident.id,
                event_pk=event.id,
                created_at=now
            )
            session.add(assoc)

        created_or_updated_incidents.append(incident)

        # Broadcast incident update via SSE
        await broadcaster.broadcast(action_type, {
            "id": incident.id,
            "rule_id": incident.rule_id,
            "device_id": incident.device_id,
            "status": incident.status,
            "severity": incident.severity,
            "title": incident.title,
            "summary": incident.summary,
            "event_count": incident.event_count,
            "opened_at": incident.opened_at.isoformat(),
            "last_event_at": incident.last_event_at.isoformat(),
        })

    return created_or_updated_incidents


async def evaluate_device_reconnect(session: AsyncSession, device: Device, event_time: datetime):
    now = datetime.now(timezone.utc)
    silence_incidents_query = await session.execute(
        select(Incident)
        .join(Rule, Incident.rule_id == Rule.id)
        .where(
            and_(
                Incident.device_id == device.id,
                Rule.rule_type == "device_silence",
                Incident.status.in_(["open", "acknowledged"])
            )
        )
    )
    active_silence_incidents = silence_incidents_query.scalars().all()
    for inc in active_silence_incidents:
        inc.status = "resolved"
        inc.resolved_at = now
        inc.dedup_key = None
        inc.resolution_note = (
            f"Auto-resolved upon device telemetry recovery at {event_time.isoformat()}."
        )
        await broadcaster.broadcast("incident_resolved", {
            "id": inc.id,
            "device_id": inc.device_id,
            "status": "resolved",
            "resolution_note": inc.resolution_note,
            "resolved_at": inc.resolved_at.isoformat()
        })

    await broadcaster.broadcast("device_status", {
        "device_id": device.id,
        "status": "online",
        "is_online": True,
        "last_seen": event_time.isoformat(),
    })


async def run_device_silence_check(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    rules_query = await session.execute(
        select(Rule).where(
            and_(Rule.is_enabled == True, Rule.rule_type == "device_silence")
        )
    )
    silence_rules = rules_query.scalars().all()
    if not silence_rules:
        return 0

    incidents_count = 0
    for rule in silence_rules:
        duration = rule.silence_duration_seconds or 60
        dev_query = select(Device).where(Device.is_enabled == True)
        if rule.device_id:
            dev_query = dev_query.where(Device.id == rule.device_id)

        devices = (await session.execute(dev_query)).scalars().all()

        for device in devices:
            baseline_time = device.latest_report_time or device.created_at
            if baseline_time is None:
                continue

            if baseline_time.tzinfo is None:
                baseline_time = baseline_time.replace(tzinfo=timezone.utc)

            elapsed_seconds = (now - baseline_time).total_seconds()
            if elapsed_seconds > duration:
                dedup_key = f"{rule.id}:{device.id}"

                existing_inc_query = await session.execute(
                    select(Incident).where(
                        and_(
                            Incident.dedup_key == dedup_key,
                            Incident.status.in_(["open", "acknowledged"])
                        )
                    )
                )
                existing_inc = existing_inc_query.scalar_one_or_none()

                if existing_inc:
                    existing_inc.last_event_at = now
                    existing_inc.summary = (
                        f"Device '{device.id}' has been silent for {int(elapsed_seconds)}s "
                        f"(threshold: {duration}s). Last report: {baseline_time.isoformat()}."
                    )
                    incidents_count += 1
                    await broadcaster.broadcast("incident_updated", {
                        "id": existing_inc.id,
                        "rule_id": existing_inc.rule_id,
                        "device_id": existing_inc.device_id,
                        "status": existing_inc.status,
                        "severity": existing_inc.severity,
                        "title": existing_inc.title,
                        "summary": existing_inc.summary,
                        "event_count": existing_inc.event_count,
                        "opened_at": existing_inc.opened_at.isoformat(),
                        "last_event_at": existing_inc.last_event_at.isoformat(),
                    })
                else:
                    new_inc = Incident(
                        rule_id=rule.id,
                        device_id=device.id,
                        status="open",
                        severity=rule.severity,
                        title=f"Device {device.id} is not reporting (Silent)",
                        summary=(
                            f"Device '{device.id}' has been silent for {int(elapsed_seconds)}s "
                            f"(threshold: {duration}s). Last report: {baseline_time.isoformat()}."
                        ),
                        dedup_key=dedup_key,
                        event_count=1,
                        opened_at=now,
                        last_event_at=now,
                    )
                    session.add(new_inc)
                    await session.flush()
                    incidents_count += 1
                    await broadcaster.broadcast("incident_created", {
                        "id": new_inc.id,
                        "rule_id": new_inc.rule_id,
                        "device_id": new_inc.device_id,
                        "status": "open",
                        "severity": new_inc.severity,
                        "title": new_inc.title,
                        "summary": new_inc.summary,
                        "event_count": 1,
                        "opened_at": new_inc.opened_at.isoformat(),
                        "last_event_at": new_inc.last_event_at.isoformat(),
                    })

                await broadcaster.broadcast("device_status", {
                    "device_id": device.id,
                    "status": "offline",
                    "is_online": False,
                    "silence_seconds": int(elapsed_seconds),
                    "last_seen": baseline_time.isoformat(),
                })

    await session.commit()
    return incidents_count
