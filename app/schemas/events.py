from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict

class EventIngestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1, max_length=128, description="Stable, unique device-generated event ID")
    device_id: str = Field(..., min_length=1, max_length=64, description="ID of the registered reporting device")
    event_time: datetime = Field(..., description="Measurement or occurrence timestamp in ISO 8601 UTC format")
    event_type: str = Field(..., min_length=1, max_length=64, description="Type of the event, e.g. temperature, heartbeat, error")
    payload: Dict[str, Any] = Field(default_factory=dict, description="JSON object containing event telemetry or metadata")


class EventBatchIngestRequest(BaseModel):
    events: List[EventIngestItem] = Field(..., min_length=1, description="List of events to ingest in batch")


class IngestStatusDetail(BaseModel):
    event_id: str
    status: str # 'accepted' | 'duplicate'
    message: Optional[str] = None


class BatchIngestResponse(BaseModel):
    accepted: int
    duplicates: int
    total: int
    details: List[IngestStatusDetail]


class SingleIngestResponse(BaseModel):
    event_id: str
    status: str # 'accepted' | 'duplicate'
    message: str


class DeviceRegistrationRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    location: Optional[str] = Field(None, max_length=256)
    is_enabled: bool = True


class DeviceUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    location: Optional[str] = Field(None, max_length=256)
    is_enabled: Optional[bool] = None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    location: Optional[str]
    is_enabled: bool
    api_token: Optional[str] = None
    latest_report_time: Optional[datetime]
    server_last_received_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class PairingCodeGenerateRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    device_name: str = Field(..., min_length=1, max_length=128)
    location: Optional[str] = Field(None, max_length=256)
    expires_in_minutes: int = Field(default=15, ge=1, le=1440)


class PairingCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    device_id: str
    device_name: str
    location: Optional[str]
    expires_at: datetime
    created_at: datetime


class PairDeviceClaimRequest(BaseModel):
    pairing_code: str = Field(..., min_length=4, max_length=32)


class PairDeviceClaimResponse(BaseModel):
    device_id: str
    device_name: str
    api_token: str
    message: str = "Device successfully paired and registered"
