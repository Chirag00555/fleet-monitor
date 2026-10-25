export interface Device {
  id: string;
  name: string;
  location?: string | null;
  is_enabled: boolean;
  latest_report_time?: string | null;
  server_last_received_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TelemetryEvent {
  id: number;
  event_id: string;
  device_id: string;
  event_type: string;
  event_time: string;
  received_at: string;
  payload: Record<string, any>;
  processing_status: string;
}

export interface Incident {
  id: number;
  rule_id: number;
  device_id: string;
  status: 'open' | 'acknowledged' | 'resolved';
  severity: 'info' | 'warning' | 'critical';
  title: string;
  summary: string;
  event_count: number;
  opened_at: string;
  last_event_at: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  resolution_note?: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentDetail extends Incident {
  events: Array<{
    id: number;
    event_id: string;
    event_type: string;
    event_time: string;
    received_at: string;
    payload: Record<string, any>;
  }>;
}

export interface Rule {
  id: number;
  name: string;
  is_enabled: boolean;
  rule_type: string;
  device_id?: string | null;
  comparator?: string | null;
  threshold_value?: number | null;
  payload_field?: string | null;
  target_event_type?: string | null;
  silence_duration_seconds?: number | null;
  severity: string;
}
export interface OperatorUser {
  id: number;
  username: string;
  full_name?: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
}
