from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator

class RuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    rule_type: str = Field(..., description="temperature_threshold, event_type, or device_silence")
    device_id: Optional[str] = Field(None, description="Applies to specific device or all devices if null")
    is_enabled: bool = True
    severity: str = Field(default="warning", description="info, warning, or critical")

    # Temperature parameters
    comparator: Optional[str] = Field(None, description=">, >=, <, <=, == (default: >=)")
    threshold_value: Optional[float] = Field(None, description="Numerical threshold for temperature")
    payload_field: Optional[str] = Field("temperature", description="Key in event payload containing the reading")

    # Event type match parameter
    target_event_type: Optional[str] = Field(None, description="Event type to match, e.g. error, low_battery")

    # Silence duration parameter
    silence_duration_seconds: Optional[int] = Field(None, gt=0, description="Duration in seconds before device is considered silent")

    @model_validator(mode="after")
    def validate_rule_config(self):
        if self.rule_type == "temperature_threshold":
            if self.threshold_value is None:
                raise ValueError("threshold_value is required for temperature_threshold rules")
            if self.comparator is None:
                self.comparator = ">="
            elif self.comparator not in [">", ">=", "<", "<=", "=="]:
                raise ValueError(f"Invalid comparator '{self.comparator}'. Must be one of: '>', '>=', '<', '<=', '=='")
        elif self.rule_type == "event_type":
            if not self.target_event_type:
                raise ValueError("target_event_type is required for event_type rules")
        elif self.rule_type == "device_silence":
            if not self.silence_duration_seconds or self.silence_duration_seconds <= 0:
                raise ValueError("silence_duration_seconds must be a positive integer for device_silence rules")
        else:
            raise ValueError(f"Unknown rule_type '{self.rule_type}'. Supported: temperature_threshold, event_type, device_silence")
        return self


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_enabled: bool
    rule_type: str
    device_id: Optional[str]
    comparator: Optional[str]
    threshold_value: Optional[float]
    payload_field: Optional[str]
    target_event_type: Optional[str]
    silence_duration_seconds: Optional[int]
    severity: str
    created_at: datetime
    updated_at: datetime


class IncidentEventDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    event_type: str
    event_time: datetime
    received_at: datetime
    payload: Dict[str, Any]


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    device_id: str
    status: str
    severity: str
    title: str
    summary: str
    event_count: int
    opened_at: datetime
    last_event_at: datetime
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution_note: Optional[str]
    created_at: datetime
    updated_at: datetime


class IncidentDetailResponse(IncidentResponse):
    events: List[IncidentEventDetail] = []


class IncidentTransitionRequest(BaseModel):
    resolution_note: Optional[str] = None
