from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    BigInteger,
    Integer,
    Float,
    ForeignKey,
    UniqueConstraint,
    Index,
    JSON,
    Text,
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def utcnow():
    return datetime.now(timezone.utc)

class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

class Device(Base):
    __tablename__ = "devices"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    location = Column(String(256), nullable=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    latest_report_time = Column(UTCDateTime, nullable=True, index=True)
    server_last_received_at = Column(UTCDateTime, nullable=True)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    events = relationship("Event", back_populates="device", cascade="all, delete-orphan")
    incidents = relationship("Incident", back_populates="device", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False, index=True)
    event_time = Column(UTCDateTime, nullable=False, index=True)
    received_at = Column(UTCDateTime, default=utcnow, nullable=False)
    payload = Column(JSON, nullable=False)
    processing_status = Column(String(32), default="pending", nullable=False) # pending, processed, failed

    device = relationship("Device", back_populates="events")
    work_items = relationship("WorkItem", back_populates="event", cascade="all, delete-orphan")
    incident_associations = relationship("IncidentEvent", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("device_id", "event_id", name="uq_device_event_id"),
        Index("idx_device_event_time", "device_id", "event_time"),
    )


class WorkItem(Base):
    __tablename__ = "work_items"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_pk = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), default="pending", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    available_at = Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    locked_at = Column(UTCDateTime, nullable=True)
    locked_by = Column(String(128), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    event = relationship("Event", back_populates="work_items")

    __table_args__ = (
        Index("idx_work_items_claim", "status", "available_at"),
    )


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False, index=True)
    rule_type = Column(String(64), nullable=False, index=True) # temperature_threshold, event_type, device_silence
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True) # None = all devices
    
    # Typed configuration columns for safety, performance & clean validation
    comparator = Column(String(16), nullable=True) # '>', '>=', '<', '<=', '==' (default for temperature is '>=')
    threshold_value = Column(Float, nullable=True) # e.g. 80.0
    payload_field = Column(String(64), default="temperature", nullable=True) # JSON field in payload
    target_event_type = Column(String(64), nullable=True) # for event_type match
    silence_duration_seconds = Column(Integer, nullable=True) # for device_silence rule
    severity = Column(String(32), default="warning", nullable=False) # info, warning, critical

    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    incidents = relationship("Incident", back_populates="rule", cascade="all, delete-orphan")


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Status: 'open', 'acknowledged', 'resolved'
    status = Column(String(32), default="open", nullable=False, index=True)
    severity = Column(String(32), default="warning", nullable=False)
    title = Column(String(256), nullable=False)
    summary = Column(Text, nullable=False)
    
    # Active incident deduplication key:
    # Set to f"{rule_id}:{device_id}" while status is 'open' or 'acknowledged'.
    # Cleared to None upon resolution so future occurrences can create a new incident.
    dedup_key = Column(String(128), nullable=True, unique=True, index=True)

    event_count = Column(Integer, default=1, nullable=False)
    opened_at = Column(UTCDateTime, default=utcnow, nullable=False)
    last_event_at = Column(UTCDateTime, default=utcnow, nullable=False)
    acknowledged_at = Column(UTCDateTime, nullable=True)
    resolved_at = Column(UTCDateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    rule = relationship("Rule", back_populates="incidents")
    device = relationship("Device", back_populates="incidents")
    incident_events = relationship("IncidentEvent", back_populates="incident", cascade="all, delete-orphan")


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True)
    event_pk = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)

    incident = relationship("Incident", back_populates="incident_events")
    event = relationship("Event", back_populates="incident_associations")

    __table_args__ = (
        Index("idx_incident_events_event", "event_pk"),
    )
