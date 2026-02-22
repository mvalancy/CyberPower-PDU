# API Reference

> **Docs:** [Getting Started](getting-started.md) | [Configuration](configuration.md) | [API Reference](api-reference.md) | [Architecture](architecture.md) | [MQTT Topics](mqtt-topics.md) | [SNMP OIDs](snmp-oids.md) | [Multi-PDU](multi-pdu.md) | [Security](security.md) | [Troubleshooting](troubleshooting.md)

The bridge serves a REST API on port 8080 (configurable via `BRIDGE_WEB_PORT`). All endpoints return JSON unless otherwise noted. CORS is enabled for all origins.

---

## Multi-PDU Note

When multiple PDUs are registered, most endpoints accept an optional `?device_id=` query parameter to specify which PDU to target. If only one PDU is registered, the parameter is optional and the single PDU is auto-selected. If multiple PDUs are registered and no `device_id` is provided, the bridge defaults to the primary device or returns an error with a list of available devices.

```bash
# Single PDU (device_id optional)
curl http://localhost:8080/api/status

# Multi-PDU (specify which device)
curl http://localhost:8080/api/status?device_id=rack1-pdu
```

---

## Status Endpoints

### GET /api/status

Returns the current state of the PDU: device info, ATS status, all bank metrics, all outlet states, and a summary.

**Query parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `device_id` | No | Target PDU (required if multiple PDUs registered) |

**Response (200):**

```json
{
  "device": {
    "name": "CyberPower PDU44001",
    "id": "pdu44001",
    "outlet_count": 10,
    "phase_count": 1
  },
  "ats": {
    "preferred_source": 1,
    "preferred_label": "A",
    "current_source": 1,
    "current_label": "A",
    "auto_transfer": true,
    "transferred": false,
    "redundancy_ok": true,
    "source_a": {
      "voltage": 121.3,
      "frequency": 60.0,
      "voltage_status": "normal"
    },
    "source_b": {
      "voltage": 120.8,
      "frequency": 60.0,
      "voltage_status": "normal"
    }
  },
  "inputs": {
    "1": {
      "number": 1,
      "voltage": 121.3,
      "current": 1.2,
      "power": 145,
      "apparent_power": 150,
      "power_factor": 0.97,
      "load_state": "normal"
    }
  },
  "outlets": {
    "1": {
      "number": 1,
      "name": "File Server",
      "state": "on",
      "current": 0.8,
      "power": 95.0,
      "energy": 12.5
    }
  },
  "summary": {
    "total_power": 145.0,
    "input_voltage": 121.3,
    "input_frequency": 60.0,
    "active_outlets": 8,
    "total_outlets": 10
  },
  "identity": {
    "serial": "ABC123",
    "model": "PDU44001",
    "firmware_main": "01.03.01",
    "outlet_count": 10,
    "phase_count": 1
  },
  "mqtt": {
    "connected": true,
    "reconnect_count": 0,
    "broker": "127.0.0.1",
    "port": 1883
  },
  "data_age_seconds": 0.3,
  "ts": 1708531200.0
}
```

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400 | Multiple PDUs registered but no `device_id` provided |
| 503 | No data received from PDU yet |

---

### GET /api/health

Health check endpoint used by Docker HEALTHCHECK and external monitoring. Aggregates health across all registered PDUs.

**Response (200 = healthy, 503 = degraded):**

```json
{
  "status": "healthy",
  "issues": [],
  "pdu_count": 1,
  "subsystems": {
    "mqtt": {
      "connected": true,
      "reconnect_count": 0,
      "broker": "127.0.0.1",
      "port": 1883,
      "publish_errors": 0,
      "total_publishes": 4521
    },
    "history": {
      "db_path": "/data/history.db",
      "total_writes": 1500,
      "write_errors": 0,
      "retention_days": 60,
      "healthy": true
    }
  },
  "uptime_seconds": 3600.5
}
```

When degraded, the `issues` array contains descriptive strings:

```json
{
  "status": "degraded",
  "issues": [
    "[rack1-pdu] Data is 45s stale",
    "MQTT disconnected"
  ]
}
```

---

## PDU Management Endpoints

These endpoints manage the multi-PDU configuration. See [Multi-PDU](multi-pdu.md) for the full guide.

### GET /api/pdus

List all registered PDUs with their status.

**Response (200):**

```json
{
  "pdus": [
    {
      "device_id": "rack1-pdu",
      "config": {
        "device_id": "rack1-pdu",
        "host": "192.168.20.177",
        "snmp_port": 161,
        "label": "Main Rack PDU",
        "enabled": true
      },
      "identity": {
        "serial": "ABC123",
        "model": "PDU44001",
        "firmware_main": "01.03.01"
      },
      "status": "healthy",
      "data_age_seconds": 0.5,
      "has_data": true
    }
  ],
  "count": 1
}
```

Status values: `healthy`, `degraded` (data older than 30s), `no_data`, `unknown`.

---

### POST /api/pdus

Add a new PDU to the configuration.

**Request body:**

```json
{
  "device_id": "rack2-pdu",
  "host": "192.168.20.178",
  "snmp_port": 161,
  "community_read": "public",
  "community_write": "private",
  "label": "Secondary Rack",
  "enabled": true,
  "num_banks": 2
}
```

**Response (201):**

```json
{
  "device_id": "rack2-pdu",
  "ok": true
}
```

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing `device_id` or `host` |
| 409 | PDU with that `device_id` already exists |

---

### PUT /api/pdus/{device_id}

Update an existing PDU's configuration.

**Request body:** Same fields as POST.

**Response (200):**

```json
{
  "device_id": "rack2-pdu",
  "ok": true
}
```

**Error: 404** if `device_id` not found.

---

### DELETE /api/pdus/{device_id}

Remove a PDU from the configuration.

**Response (200):**

```json
{
  "device_id": "rack2-pdu",
  "deleted": true
}
```

**Error: 404** if `device_id` not found.

---

### POST /api/pdus/discover

Trigger a network scan for CyberPower PDUs.

**Response (200):**

```json
{
  "discovered": [
    {
      "host": "192.168.20.177",
      "model": "PDU44001",
      "serial": "ABC123",
      "name": "CyberPower PDU"
    }
  ]
}
```

**Error: 503** if discovery callback is not configured.

---

## Bridge Configuration Endpoints

### GET /api/config

Get the current bridge configuration.

**Response (200):**

```json
{
  "poll_interval": 1.0,
  "port": 8080,
  "pdu_count": 1,
  "default_device_id": "pdu44001"
}
```

---

### PUT /api/config

Update bridge configuration at runtime (without restart).

**Request body:**

```json
{
  "poll_interval": 5.0
}
```

**Response (200):**

```json
{
  "updated": {
    "poll_interval": 5.0
  },
  "ok": true
}
```

**Constraints:** `poll_interval` must be >= 1.

---

## Device Endpoints

These endpoints write to the PDU hardware via SNMP SET.

### PUT /api/device/name

Set the device name on the PDU via SNMP.

**Query parameters:** `device_id` (optional, required for multi-PDU)

**Request body:**

```json
{
  "name": "Main Rack PDU"
}
```

**Response (200):**

```json
{
  "device_id": "pdu44001",
  "name": "Main Rack PDU",
  "ok": true
}
```

---

### PUT /api/device/location

Set the sysLocation field on the PDU via SNMP.

**Query parameters:** `device_id` (optional, required for multi-PDU)

**Request body:**

```json
{
  "location": "Server Room A, Rack 3"
}
```

**Response (200):**

```json
{
  "device_id": "pdu44001",
  "location": "Server Room A, Rack 3",
  "ok": true
}
```

---

## Outlet Endpoints

### POST /api/outlets/{n}/command

Send a command to an outlet.

**URL parameters:**

| Parameter | Description |
|-----------|-------------|
| `n` | Outlet number (1-based) |

**Query parameters:** `device_id` (optional, required for multi-PDU)

**Request body:**

```json
{
  "action": "off"
}
```

Valid actions: `on`, `off`, `reboot`

**Response (200):**

```json
{
  "outlet": 3,
  "action": "off",
  "device_id": "pdu44001",
  "ok": true
}
```

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400 | Invalid outlet number or action |
| 500 | SNMP SET failed |
| 503 | Command handler not available |

---

### PUT /api/outlets/{n}/name

Set a custom name for an outlet. The name persists across restarts.

**Request body:**

```json
{
  "name": "File Server"
}
```

To clear a custom name and revert to the PDU hardware name:

```json
{
  "name": ""
}
```

**Response (200):**

```json
{
  "outlet": 1,
  "name": "File Server",
  "ok": true
}
```

---

### GET /api/outlet-names

Get all custom outlet name overrides.

**Response (200):**

```json
{
  "1": "File Server",
  "3": "Network Switch",
  "5": "Lab Equipment"
}
```

---

## Automation Endpoints

### GET /api/rules

List all automation rules with their current state.

**Query parameters:** `device_id` (optional)

**Response (200):**

```json
[
  {
    "name": "low-voltage-shutoff",
    "input": 1,
    "condition": "voltage_below",
    "threshold": 108.0,
    "outlet": 5,
    "action": "off",
    "restore": true,
    "delay": 5,
    "state": {
      "triggered": false,
      "condition_since": null,
      "fired_at": null
    }
  }
]
```

---

### POST /api/rules

Create a new automation rule.

**Query parameters:** `device_id` (optional)

**Request body:**

```json
{
  "name": "brownout-protection",
  "input": 1,
  "condition": "voltage_below",
  "threshold": 108,
  "outlet": 5,
  "action": "off",
  "restore": true,
  "delay": 10
}
```

**Response (201):** The created rule object.

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400 | Invalid rule data (missing fields, bad condition, etc.) |
| 409 | Rule with that name already exists |

---

### PUT /api/rules/{name}

Update an existing rule. The rule name in the URL must match an existing rule.

**Query parameters:** `device_id` (optional)

**Request body:** Same fields as POST (the `name` field in the body is ignored; the URL name is used).

**Response (200):** The updated rule object.

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400 | Invalid rule data |
| 404 | Rule not found |

---

### DELETE /api/rules/{name}

Delete an automation rule.

**Query parameters:** `device_id` (optional)

**Response (200):**

```json
{
  "deleted": "brownout-protection"
}
```

**Error: 404** if rule not found.

---

### GET /api/events

Get the automation event log (most recent first, up to 100 events).

**Query parameters:** `device_id` (optional)

**Response (200):**

```json
[
  {
    "rule": "low-voltage-shutoff",
    "type": "triggered",
    "details": "Input 1 voltage_below 108 -> outlet 5 off",
    "ts": 1708531200.0
  },
  {
    "rule": "low-voltage-shutoff",
    "type": "restored",
    "details": "Input 1 recovered -> outlet 5 on",
    "ts": 1708531260.0
  }
]
```

Event types: `triggered`, `restored`, `created`, `updated`, `deleted`.

---

## History Endpoints

All history endpoints support time range filtering and automatic downsampling.

### Time range parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `range` | Preset time range | `1h`, `6h`, `24h`, `7d`, `30d` |
| `start` | Unix timestamp (start) | `1708444800` |
| `end` | Unix timestamp (end) | `1708531200` |

If both `start` and `end` are provided, they take precedence over `range`. The default range is `1h`.

### Downsampling

The bridge automatically downsamples data based on the time range to keep responses fast:

| Range | Sample Interval | Max Points |
|-------|----------------|------------|
| Up to 1 hour | 1 second (raw) | 3,600 |
| Up to 6 hours | 10 seconds | 2,160 |
| Up to 24 hours | 1 minute | 1,440 |
| Up to 7 days | 5 minutes | 2,016 |
| Up to 30 days | 15 minutes | 2,880 |
| 60 days | 30 minutes | 2,880 |

---

### GET /api/history/banks

Query bank history data (voltage, current, power, apparent power, power factor).

**Query parameters:** `range` or `start`/`end`, `device_id` (optional)

**Response (200):**

```json
[
  {
    "bucket": 1708531200,
    "bank": 1,
    "voltage": 121.3,
    "current": 1.2,
    "power": 145.0,
    "apparent": 150.0,
    "pf": 0.97
  }
]
```

---

### GET /api/history/outlets

Query outlet history data (current, power, energy).

**Query parameters:** `range` or `start`/`end`, `device_id` (optional)

**Response (200):**

```json
[
  {
    "bucket": 1708531200,
    "outlet": 1,
    "current": 0.8,
    "power": 95.0,
    "energy": 12.5
  }
]
```

---

### GET /api/history/banks.csv

Same data as `/api/history/banks`, returned as a CSV file download.

**Headers:** `Content-Disposition: attachment; filename="bank_history.csv"`

**Columns:** `bucket, bank, voltage, current, power, apparent, pf`

---

### GET /api/history/outlets.csv

Same data as `/api/history/outlets`, returned as a CSV file download.

**Headers:** `Content-Disposition: attachment; filename="outlet_history.csv"`

**Columns:** `bucket, outlet, current, power, energy`

---

## Report Endpoints

The bridge generates weekly energy reports automatically (one per Monday-to-Sunday week). Reports include total kWh, per-outlet breakdown, daily breakdown, peak/average power, and household comparison (if configured).

### GET /api/reports

List all available reports.

**Query parameters:** `device_id` (optional)

**Response (200):**

```json
[
  {
    "id": 3,
    "week_start": "2026-02-10",
    "week_end": "2026-02-17",
    "created_at": "2026-02-17T01:00:00.000000",
    "device_id": ""
  }
]
```

---

### GET /api/reports/latest

Get the most recent report with full data.

**Query parameters:** `device_id` (optional)

**Response (200):**

```json
{
  "id": 3,
  "week_start": "2026-02-10",
  "week_end": "2026-02-17",
  "created_at": "2026-02-17T01:00:00.000000",
  "device_id": "",
  "data": {
    "week_start": "2026-02-10",
    "week_end": "2026-02-17",
    "total_kwh": 24.567,
    "peak_power_w": 285.0,
    "avg_power_w": 146.2,
    "per_outlet": {
      "1": {"kwh": 8.123, "avg_power": 48.3, "peak_power": 95.0},
      "2": {"kwh": 5.432, "avg_power": 32.3, "peak_power": 60.0}
    },
    "daily": {
      "2026-02-10": {"kwh": 3.456, "avg_power": 144.0, "peak_power": 280.0}
    },
    "house_pct": 2.8,
    "sample_count": 604800
  }
}
```

**Error: 404** if no reports exist yet.

---

### GET /api/reports/{id}

Get a specific report by ID.

**Response (200):** Same format as `/api/reports/latest`.

**Error: 404** if report not found.

---

## Static Content

### GET /

Serves the web dashboard (`bridge/static/index.html`).
