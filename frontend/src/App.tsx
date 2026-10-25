import React, { useState, useEffect } from "react";
import type { Device, TelemetryEvent, Incident, IncidentDetail, OperatorUser } from "./types";

const API_BASE = "http://localhost:8000/api";

export const App: React.FC = () => {
  // Authentication state
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("fleet_token"));
  const [operator, setOperator] = useState<OperatorUser | null>(null);
  const [authLoading, setAuthLoading] = useState<boolean>(true);
  const [usernameInput, setUsernameInput] = useState<string>("admin");
  const [passwordInput, setPasswordInput] = useState<string>("admin123");
  const [authError, setAuthError] = useState<string>("");

  // Dashboard state
  const [devices, setDevices] = useState<Device[]>([]);
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<IncidentDetail | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<"connected" | "reconnecting" | "offline">("offline");
  const [resolutionNote, setResolutionNote] = useState<string>("");
  const [selectedDeviceFilter, setSelectedDeviceFilter] = useState<string>("all");

  // Verify stored token on initial load
  useEffect(() => {
    const verifyToken = async () => {
      if (!token) {
        setAuthLoading(false);
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const user: OperatorUser = await res.json();
          setOperator(user);
        } else {
          localStorage.removeItem("fleet_token");
          setToken(null);
          setOperator(null);
        }
      } catch (err) {
        console.error("Auth verification failed:", err);
      } finally {
        setAuthLoading(false);
      }
    };
    verifyToken();
  }, [token]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError("");
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: usernameInput, password: passwordInput }),
      });
      if (res.ok) {
        const data = await res.json();
        localStorage.setItem("fleet_token", data.access_token);
        setToken(data.access_token);
        setOperator(data.operator);
      } else {
        const errData = await res.json().catch(() => ({}));
        setAuthError(errData.detail || "Invalid operator credentials");
      }
    } catch (err) {
      setAuthError("Network error connecting to auth server");
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("fleet_token");
    setToken(null);
    setOperator(null);
  };

  const fetchAllState = async () => {
    try {
      const [devRes, incRes, evtRes] = await Promise.all([
        fetch(API_BASE + "/devices"),
        fetch(API_BASE + "/incidents"),
        fetch(API_BASE + "/events?limit=50")
      ]);

      if (devRes.ok) setDevices(await devRes.json());
      if (incRes.ok) setIncidents(await incRes.json());
      if (evtRes.ok) setEvents(await evtRes.json());
    } catch (err) {
      console.error("Failed to fetch platform state:", err);
    }
  };

  useEffect(() => {
    if (!token) return;

    fetchAllState();

    let eventSource: EventSource | null = null;
    let reconnectTimeout: any = null;

    const connectSSE = () => {
      setConnectionStatus("reconnecting");
      eventSource = new EventSource(API_BASE + "/events/stream");

      eventSource.onopen = () => {
        setConnectionStatus("connected");
        fetchAllState();
      };

      eventSource.addEventListener("event_received", (e) => {
        try {
          const newEvent: TelemetryEvent = JSON.parse(e.data);
          setEvents((prev) => [newEvent, ...prev.slice(0, 49)]);
          setDevices((prev) =>
            prev.map((d) =>
              d.id === newEvent.device_id
                ? { ...d, latest_report_time: newEvent.event_time, server_last_received_at: newEvent.received_at }
                : d
            )
          );
        } catch (err) {
          console.error("Failed to parse SSE event_received:", err);
        }
      });

      eventSource.addEventListener("incident_created", (e) => {
        try {
          const inc: Incident = JSON.parse(e.data);
          setIncidents((prev) => [inc, ...prev.filter((i) => i.id !== inc.id)]);
        } catch (err) {
          console.error("Failed to parse SSE incident_created:", err);
        }
      });

      eventSource.addEventListener("incident_updated", (e) => {
        try {
          const updated: Incident = JSON.parse(e.data);
          setIncidents((prev) =>
            prev.map((i) => (i.id === updated.id ? { ...i, ...updated } : i))
          );
        } catch (err) {
          console.error("Failed to parse SSE incident_updated:", err);
        }
      });

      eventSource.addEventListener("incident_acknowledged", (e) => {
        try {
          const data = JSON.parse(e.data);
          setIncidents((prev) =>
            prev.map((i) =>
              i.id === data.id ? { ...i, status: "acknowledged", acknowledged_at: data.acknowledged_at } : i
            )
          );
        } catch (err) {}
      });

      eventSource.addEventListener("incident_resolved", (e) => {
        try {
          const data = JSON.parse(e.data);
          setIncidents((prev) =>
            prev.map((i) =>
              i.id === data.id ? { ...i, status: "resolved", resolved_at: data.resolved_at, resolution_note: data.resolution_note } : i
            )
          );
        } catch (err) {}
      });

      eventSource.onerror = () => {
        setConnectionStatus("offline");
        if (eventSource) {
          eventSource.close();
        }
        reconnectTimeout = setTimeout(connectSSE, 3000);
      };
    };

    connectSSE();

    return () => {
      if (eventSource) eventSource.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, [token]);

  const handleInspectIncident = async (id: number) => {
    try {
      const res = await fetch(API_BASE + "/incidents/" + id);
      if (res.ok) {
        setSelectedIncident(await res.json());
      }
    } catch (err) {
      console.error("Failed to inspect incident:", err);
    }
  };

  const handleAcknowledge = async (id: number) => {
    try {
      const res = await fetch(API_BASE + "/incidents/" + id + "/acknowledge", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const updated = await res.json();
        setIncidents((prev) => prev.map((i) => (i.id === id ? updated : i)));
        if (selectedIncident && selectedIncident.id === id) {
          setSelectedIncident((prev) => (prev ? { ...prev, ...updated } : null));
        }
      }
    } catch (err) {
      console.error("Error acknowledging incident:", err);
    }
  };

  const handleResolve = async (id: number) => {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const res = await fetch(API_BASE + "/incidents/" + id + "/resolve", {
        method: "POST",
        headers,
        body: JSON.stringify({ resolution_note: resolutionNote || "Resolved by operator" })
      });
      if (res.ok) {
        const updated = await res.json();
        setIncidents((prev) => prev.map((i) => (i.id === id ? updated : i)));
        if (selectedIncident && selectedIncident.id === id) {
          setSelectedIncident((prev) => (prev ? { ...prev, ...updated } : null));
        }
        setResolutionNote("");
      }
    } catch (err) {
      console.error("Error resolving incident:", err);
    }
  };

  // If initial auth token check is in flight
  if (authLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh", background: "#0a0e17", color: "#8fa0b5", fontFamily: "sans-serif" }}>
        <div>Initializing Fleet Operations Console...</div>
      </div>
    );
  }

  // If unauthenticated, show Stage 1 Operator Sign-In card
  if (!token) {
    return (
      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "radial-gradient(ellipse at top, #141c2b 0%, #0a0e17 100%)",
        color: "#f0f4f8",
        fontFamily: "'Segoe UI', Roboto, sans-serif",
        padding: "20px"
      }}>
        <div style={{
          width: "100%",
          maxWidth: "420px",
          background: "rgba(18, 24, 38, 0.95)",
          border: "1px solid #26354d",
          borderRadius: "12px",
          padding: "32px",
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.6)",
          backdropFilter: "blur(12px)"
        }}>
          <div style={{ textAlign: "center", marginBottom: "28px" }}>
            <div style={{ fontSize: "36px", marginBottom: "8px" }}>🛰️</div>
            <h2 style={{ margin: "0 0 6px 0", fontSize: "20px", letterSpacing: "1px", color: "#00d2ff" }}>
              FLEET OPERATIONS
            </h2>
            <div style={{ fontSize: "12px", color: "#8fa0b5" }}>
              Operator Authentication (Stage 1)
            </div>
          </div>

          {authError && (
            <div style={{
              background: "rgba(239, 68, 68, 0.15)",
              border: "1px solid rgba(239, 68, 68, 0.4)",
              color: "#fca5a5",
              padding: "10px 14px",
              borderRadius: "6px",
              fontSize: "12px",
              marginBottom: "20px"
            }}>
              ⚠️ {authError}
            </div>
          )}

          <form onSubmit={handleLogin}>
            <div style={{ marginBottom: "16px" }}>
              <label style={{ display: "block", fontSize: "12px", color: "#8fa0b5", marginBottom: "6px", fontWeight: 500 }}>
                Operator Username
              </label>
              <input
                type="text"
                required
                value={usernameInput}
                onChange={(e) => setUsernameInput(e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  background: "#0d131f",
                  border: "1px solid #223249",
                  borderRadius: "6px",
                  color: "#fff",
                  fontSize: "14px",
                  outline: "none"
                }}
              />
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", fontSize: "12px", color: "#8fa0b5", marginBottom: "6px", fontWeight: 500 }}>
                Password
              </label>
              <input
                type="password"
                required
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  background: "#0d131f",
                  border: "1px solid #223249",
                  borderRadius: "6px",
                  color: "#fff",
                  fontSize: "14px",
                  outline: "none"
                }}
              />
            </div>

            <button
              type="submit"
              className="btn"
              style={{
                width: "100%",
                padding: "11px",
                fontSize: "14px",
                fontWeight: 600,
                letterSpacing: "0.5px",
                background: "linear-gradient(135deg, #0077b6 0%, #0096c7 100%)",
                border: "none",
                borderRadius: "6px",
                color: "#fff",
                cursor: "pointer"
              }}
            >
              Sign In to Console
            </button>
          </form>

          <div style={{
            marginTop: "24px",
            paddingTop: "16px",
            borderTop: "1px solid #1a273a",
            fontSize: "11px",
            color: "#6b7d95",
            textAlign: "center"
          }}>
            Default Seeded Operator: <code style={{ color: "#00d2ff" }}>admin</code> / <code style={{ color: "#00d2ff" }}>admin123</code>
          </div>
        </div>
      </div>
    );
  }

  const activeIncidents = incidents.filter((i) => i.status !== "resolved");
  const criticalIncidents = incidents.filter((i) => i.status !== "resolved" && i.severity === "critical");
  
  // Filter events based on selected device dropdown
  const filteredEvents = selectedDeviceFilter === "all" 
    ? events 
    : events.filter((e) => e.device_id === selectedDeviceFilter);

  return (
    <div className="dashboard-container">
      <header className="top-header">
        <div className="brand-section">
          <span style={{ fontSize: "20px" }}>🛰️</span>
          <div>
            <h1 className="brand-title">FLEET OPERATIONS CONSOLE</h1>
            <div style={{ fontSize: "11px", color: "#8fa0b5" }}>Real-time Edge Telemetry & Incident Intelligence</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          {operator && (
            <div style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              background: "#121826",
              border: "1px solid #26354d",
              padding: "5px 12px",
              borderRadius: "20px",
              fontSize: "12px"
            }}>
              <span style={{ color: "#10b981", fontSize: "10px" }}>●</span>
              <span style={{ color: "#f0f4f8", fontWeight: 600 }}>{operator.username}</span>
              <span style={{ color: "#00d2ff", fontSize: "10px", textTransform: "uppercase", background: "rgba(0,210,255,0.1)", padding: "2px 6px", borderRadius: "10px" }}>{operator.role}</span>
            </div>
          )}
          <div className={"connection-badge conn-" + connectionStatus}>
            <span className="pulse-dot"></span>
            {connectionStatus === "connected" ? "LIVE (SSE)" : connectionStatus === "reconnecting" ? "RECONNECTING..." : "OFFLINE"}
          </div>
          <button className="btn btn-secondary btn-sm" onClick={fetchAllState}>
            Refresh REST
          </button>
          <button
            className="btn btn-secondary btn-sm"
            style={{ color: "#f87171", borderColor: "#7f1d1d" }}
            onClick={handleLogout}
            title="Sign out of operator session"
          >
            Sign Out
          </button>
        </div>
      </header>

      <section className="stats-ribbon">
        <div className="stat-card">
          <div className="stat-label">Registered Devices</div>
          <div className="stat-value" style={{ color: "#00d2ff" }}>{devices.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Active Incidents</div>
          <div className="stat-value" style={{ color: activeIncidents.length > 0 ? "#f59e0b" : "#10b981" }}>
            {activeIncidents.length}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Critical Alerts</div>
          <div className="stat-value" style={{ color: criticalIncidents.length > 0 ? "#ef4444" : "#8fa0b5" }}>
            {criticalIncidents.length}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Telemetry Events (Cached)</div>
          <div className="stat-value" style={{ color: "#f0f4f8" }}>{events.length}</div>
        </div>
      </section>

      <main className="main-content">
        {/* Left Column: Operational Incidents */}
        <div className="panel">
          <div className="panel-header">
            <h2 className="panel-title">🚨 Operational Incidents ({incidents.length})</h2>
            <div style={{ display: "flex", gap: "8px" }}>
              <span className="badge badge-critical">{criticalIncidents.length} Critical</span>
              <span className="badge badge-warning">{activeIncidents.length - criticalIncidents.length} Warning</span>
            </div>
          </div>

          <div className="panel-body">
            {incidents.length === 0 ? (
              <div style={{ textAlign: "center", padding: "30px", color: "#8fa0b5" }}>
                No incidents reported. All systems nominal.
              </div>
            ) : (
              <div className="list-container">
                {incidents.map((inc) => (
                  <div key={inc.id} className="incident-row" style={{ cursor: "pointer" }} onClick={() => handleInspectIncident(inc.id)}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                        <span className={"badge badge-" + inc.severity}>{inc.severity}</span>
                        <span className={"badge badge-" + inc.status}>{inc.status}</span>
                        <span style={{ fontWeight: 600 }}>#{inc.id} {inc.title}</span>
                      </div>
                      <span style={{ fontSize: "11px", color: "#8fa0b5" }}>
                        {new Date(inc.last_event_at).toLocaleTimeString()}
                      </span>
                    </div>

                    <p style={{ fontSize: "12px", color: "#cbd5e1", marginBottom: "8px" }}>{inc.summary}</p>

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "11px", color: "#8fa0b5" }}>
                      <span>Device: <strong>{inc.device_id}</strong> (Events: {inc.event_count})</span>
                      <div style={{ display: "flex", gap: "6px" }} onClick={(e) => e.stopPropagation()}>
                        {inc.status === "open" && (
                          <button className="btn btn-sm" onClick={() => handleAcknowledge(inc.id)}>Acknowledge</button>
                        )}
                        {inc.status !== "resolved" && (
                          <button className="btn btn-success btn-sm" onClick={() => handleResolve(inc.id)}>Resolve</button>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Fleet Devices + Live Telemetry Stream */}
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <div className="panel" style={{ flex: "1" }}>
            <div className="panel-header">
              <h2 className="panel-title">📡 Fleet Devices ({devices.length})</h2>
            </div>
            <div className="panel-body" style={{ maxHeight: "200px" }}>
              {devices.length === 0 ? (
                <div style={{ color: "#8fa0b5", textAlign: "center", padding: "15px" }}>No devices registered.</div>
              ) : (
                <div className="list-container">
                  {devices.map((d) => (
                    <div 
                      key={d.id} 
                      className="device-row" 
                      style={{ 
                        display: "flex", 
                        justifyContent: "space-between", 
                        alignItems: "center",
                        cursor: "pointer",
                        borderLeft: selectedDeviceFilter === d.id ? "3px solid #00d2ff" : undefined
                      }}
                      onClick={() => setSelectedDeviceFilter(selectedDeviceFilter === d.id ? "all" : d.id)}
                      title="Click to filter live stream by this device"
                    >
                      <div>
                        <div style={{ fontWeight: 600 }}>{d.name} <span style={{ fontSize: "11px", color: "#8fa0b5" }}>({d.id})</span></div>
                        <div style={{ fontSize: "11px", color: "#8fa0b5" }}>{d.location || "Unknown Location"}</div>
                      </div>
                      <div style={{ textAlign: "right", fontSize: "11px" }}>
                        <div style={{ color: d.latest_report_time ? "#10b981" : "#f59e0b" }}>
                          {d.latest_report_time ? "Last: " + new Date(d.latest_report_time).toLocaleTimeString() : "Never reported"}
                        </div>
                        <div style={{ color: "#8fa0b5" }}>{d.is_enabled ? "Enabled" : "Disabled"}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="panel" style={{ flex: "1.2" }}>
            <div className="panel-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <h2 className="panel-title">⚡ Live Ingestion Stream (SSE)</h2>
                <span style={{ fontSize: "11px", color: "#8fa0b5" }}>({filteredEvents.length})</span>
              </div>
              
              {/* Device Stream Filter Dropdown */}
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <label style={{ fontSize: "11px", color: "#8fa0b5" }}>Filter:</label>
                <select
                  value={selectedDeviceFilter}
                  onChange={(e) => setSelectedDeviceFilter(e.target.value)}
                  style={{
                    background: "#0d1117",
                    color: "#00d2ff",
                    border: "1px solid #26354d",
                    borderRadius: "4px",
                    padding: "3px 8px",
                    fontSize: "11px",
                    cursor: "pointer"
                  }}
                >
                  <option value="all">All Devices (Fleet View)</option>
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.id} ({d.name})
                    </option>
                  ))}
                </select>
              </div>
            </div>
            
            <div className="panel-body" style={{ maxHeight: "250px" }}>
              {filteredEvents.length === 0 ? (
                <div style={{ color: "#8fa0b5", textAlign: "center", padding: "20px" }}>
                  {selectedDeviceFilter === "all" ? "Waiting for telemetry..." : "No telemetry received for this device yet."}
                </div>
              ) : (
                <div className="list-container">
                  {filteredEvents.slice(0, 15).map((evt) => (
                    <div key={evt.device_id + "-" + evt.event_id} className="event-row">
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px" }}>
                        <span>
                          <strong style={{ color: "#00d2ff" }}>{evt.device_id}</strong> / {evt.event_type}
                        </span>
                        <span style={{ color: "#8fa0b5", fontSize: "11px" }}>
                          {new Date(evt.event_time).toLocaleTimeString()}
                        </span>
                      </div>
                      <div style={{ fontSize: "11px", color: "#cbd5e1", marginTop: "4px" }}>
                        Payload: {JSON.stringify(evt.payload)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      {/* Incident Deep Inspection Modal */}
      {selectedIncident && (
        <div className="modal-overlay" onClick={() => setSelectedIncident(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <span className={"badge badge-" + selectedIncident.severity}>{selectedIncident.severity}</span>
                <span className={"badge badge-" + selectedIncident.status}>{selectedIncident.status}</span>
                <h3 style={{ fontSize: "16px" }}>Incident #{selectedIncident.id}</h3>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => setSelectedIncident(null)}>✕</button>
            </div>

            <div className="modal-body">
              <h4 style={{ marginBottom: "8px" }}>{selectedIncident.title}</h4>
              <p style={{ color: "#cbd5e1", marginBottom: "16px" }}>{selectedIncident.summary}</p>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", fontSize: "12px", marginBottom: "16px" }}>
                <div><strong>Device:</strong> {selectedIncident.device_id}</div>
                <div><strong>Opened At:</strong> {new Date(selectedIncident.opened_at).toLocaleString()}</div>
                <div><strong>Last Occurrence:</strong> {new Date(selectedIncident.last_event_at).toLocaleString()}</div>
                <div><strong>Event Count:</strong> {selectedIncident.event_count}</div>
                {selectedIncident.resolution_note && (
                  <div style={{ gridColumn: "span 2", color: "#10b981" }}>
                    <strong>Resolution:</strong> {selectedIncident.resolution_note}
                  </div>
                )}
              </div>

              <h4 style={{ fontSize: "13px", marginBottom: "8px" }}>Triggering & Associated Events ({selectedIncident.events ? selectedIncident.events.length : 0})</h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "180px", overflowY: "auto" }}>
                {selectedIncident.events && selectedIncident.events.length > 0 ? (
                  selectedIncident.events.map((e) => (
                    <div key={e.event_id} style={{ background: "#0d1117", padding: "8px", borderRadius: "4px", border: "1px solid #26354d", fontSize: "11px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", color: "#00d2ff" }}>
                        <span>{e.event_type} (ID: {e.event_id.slice(0, 8)}...)</span>
                        <span style={{ color: "#8fa0b5" }}>{new Date(e.event_time).toLocaleTimeString()}</span>
                      </div>
                      <pre style={{ marginTop: "4px", fontSize: "11px" }}>{JSON.stringify(e.payload, null, 2)}</pre>
                    </div>
                  ))
                ) : (
                  <div style={{ color: "#8fa0b5", fontSize: "12px" }}>No direct events attached (e.g. periodic silence rule).</div>
                )}
              </div>

              {selectedIncident.status !== "resolved" && (
                <div style={{ marginTop: "16px" }}>
                  <label style={{ fontSize: "12px", display: "block", marginBottom: "4px" }}>Resolution Note (Optional):</label>
                  <input
                    type="text"
                    style={{ width: "100%", padding: "8px", background: "#0d1117", border: "1px solid #26354d", color: "#fff", borderRadius: "4px" }}
                    placeholder="e.g. Unit recalibrated or power restored"
                    value={resolutionNote}
                    onChange={(e) => setResolutionNote(e.target.value)}
                  />
                </div>
              )}
            </div>

            <div className="modal-footer">
              {selectedIncident.status === "open" && (
                <button className="btn" onClick={() => handleAcknowledge(selectedIncident.id)}>Acknowledge</button>
              )}
              {selectedIncident.status !== "resolved" && (
                <button className="btn btn-success" onClick={() => handleResolve(selectedIncident.id)}>Resolve Incident</button>
              )}
              <button className="btn btn-secondary" onClick={() => setSelectedIncident(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
export default App;
