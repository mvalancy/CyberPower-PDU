# Configuration

> **Docs:** [Getting Started](getting-started.md) | [Configuration](configuration.md) | [API Reference](api-reference.md) | [Architecture](architecture.md) | [MQTT Topics](mqtt-topics.md) | [SNMP OIDs](snmp-oids.md) | [Multi-PDU](multi-pdu.md) | [Security](security.md) | [Troubleshooting](troubleshooting.md) | [Features](features.md)

All bridge configuration is done through environment variables in the `.env` file at the project root. Copy `.env.example` to `.env` and edit it. After changing any setting, rebuild and restart the bridge:

```bash
./start --rebuild
```

---

## PDU Connection Settings

These settings tell the bridge how to reach your PDU over SNMP. In single-PDU mode, these are the primary connection settings. In multi-PDU mode, they are overridden by `pdus.json` (see [Multi-PDU](#multi-pdu-pdusjson-format) below).

| Variable | Default | Description |
|----------|---------|-------------|
| `PDU_HOST` | `192.168.20.177` | IP address or hostname of your PDU |
| `PDU_SNMP_PORT` | `161` | SNMP port (almost always 161) |
| `PDU_COMMUNITY_READ` | `public` | SNMP v2c community string for read (GET) operations |
| `PDU_COMMUNITY_WRITE` | `private` | SNMP v2c community string for write (SET) operations -- used for outlet control |
| `PDU_DEVICE_ID` | *(empty)* | Unique identifier used in MQTT topics (`pdu/{device_id}/...`) and the web UI. Auto-assigned as `pdu-01`, `pdu-02`, etc. if left empty. Must not contain `/ # + ` or spaces. |

**Example:**

```ini
PDU_HOST=192.168.1.50
PDU_SNMP_PORT=161
PDU_COMMUNITY_READ=public
PDU_COMMUNITY_WRITE=private
PDU_DEVICE_ID=rack1-pdu
```

---

## Serial Transport Settings

These settings enable RS-232 serial console access for full PDU management (thresholds, ATS, network, security, notifications). Serial is optional -- SNMP works for monitoring and outlet control without it.

| Variable | Default | Description |
|----------|---------|-------------|
| `PDU_SERIAL_PORT` | *(empty)* | Serial device path (e.g., `/dev/ttyUSB0`). Leave empty to disable serial. |
| `PDU_SERIAL_BAUD` | `9600` | Serial baud rate |
| `PDU_SERIAL_USERNAME` | `cyber` | Serial console login username (CyberPower factory default is `cyber`) |
| `PDU_SERIAL_PASSWORD` | `cyber` | Serial console login password (CyberPower factory default is `cyber`) |
| `PDU_TRANSPORT` | `snmp` | Primary transport: `snmp`, `serial`, or `both` |

**Example:**

```ini
PDU_SERIAL_PORT=/dev/ttyUSB0
PDU_SERIAL_BAUD=9600
PDU_SERIAL_USERNAME=cyber
PDU_SERIAL_PASSWORD=mypassword
```

When both SNMP and serial are configured, the bridge uses SNMP as the primary transport for monitoring (faster) and serial for management operations. If one transport fails, the bridge automatically falls back to the other.

---

## MQTT Settings

These control how the bridge connects to the MQTT broker (Mosquitto).

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | `127.0.0.1` | Hostname or IP of the MQTT broker. The Docker Compose file overrides this to `127.0.0.1` because the bridge uses `network_mode: host`. The `.env` value is only used when running the bridge outside Docker. |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | *(empty)* | Username for MQTT authentication. Leave empty for anonymous access. |
| `MQTT_PASSWORD` | *(empty)* | Password for MQTT authentication. Leave empty for anonymous access. |

**Anonymous access (default):**

```ini
MQTT_BROKER=mosquitto
MQTT_PORT=1883
```

**Authenticated access:**

```ini
MQTT_BROKER=mosquitto
MQTT_PORT=1883
MQTT_USERNAME=pdu-bridge
MQTT_PASSWORD=your-secure-password
```

When you set `MQTT_USERNAME` and `MQTT_PASSWORD`, the bridge will authenticate with the broker on connect. You also need to configure your Mosquitto broker to require authentication -- see [Security](security.md) for instructions.

---

## Bridge Operation Settings

These control how the bridge itself behaves.

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `BRIDGE_POLL_INTERVAL` | `1.0` | 0.1 - 300 | Seconds between SNMP poll cycles. `1.0` means the bridge queries the PDU once per second. |
| `BRIDGE_MOCK_MODE` | `false` | `true`/`false` | Use simulated PDU data instead of real SNMP. Useful for development and demos. |
| `BRIDGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Python logging level. Set to `DEBUG` for troubleshooting. |
| `BRIDGE_SNMP_TIMEOUT` | `2.0` | 0.5 - 30 | Seconds to wait for each SNMP response before timing out. |
| `BRIDGE_SNMP_RETRIES` | `1` | 0 - 5 | Number of times to retry a failed SNMP request. |
| `BRIDGE_WEB_PORT` | `8080` | 1 - 65535 | Port for the web dashboard and REST API. |
| `BRIDGE_RULES_FILE` | `/data/rules.json` | file path | Path to the automation rules JSON file (inside the container). |
| `BRIDGE_PDUS_FILE` | `/data/pdus.json` | file path | Path to the multi-PDU configuration file (inside the container). |
| `BRIDGE_OUTLET_NAMES_FILE` | `/data/outlet_names.json` | file path | Path to custom outlet name overrides (inside the container). |
| `BRIDGE_WEB_PASSWORD` | *(empty)* | string | Set to enable web UI authentication. When set, all API endpoints require a session token. |
| `BRIDGE_WEB_USERNAME` | `admin` | string | Username for web UI login (only used when `BRIDGE_WEB_PASSWORD` is set). |
| `BRIDGE_SESSION_SECRET` | *(auto-generated)* | string | Secret key for session tokens. Auto-generated if empty. Set explicitly for consistent sessions across restarts. |
| `BRIDGE_SESSION_TIMEOUT` | `86400` | 60 - 604800 | Session token lifetime in seconds (default: 24 hours). |
| `BRIDGE_RECOVERY_ENABLED` | `true` | `true`/`false` | Enable DHCP resilience -- auto-recover via subnet scan when the PDU changes IP. |
| `BRIDGE_SETTINGS_FILE` | `/data/bridge_settings.json` | file path | Path to persisted runtime settings (inside the container). |

---

## History and Reports Settings

These control the SQLite history database and PDF energy reports.

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `HISTORY_RETENTION_DAYS` | `60` | 1 - 365 | How many days of 1Hz history to keep. Older data is automatically deleted hourly. |
| `HOUSE_MONTHLY_KWH` | `0` | 0 - 100,000 | Your household's monthly electricity usage in kWh. Set to `0` to disable. |
| `BRIDGE_HISTORY_DB` | `/data/history.db` | file path | Path to the SQLite database file (inside the container). |
| `BRIDGE_REPORTS_ENABLED` | `true` | `true` / `false` | Enable or disable automatic PDF report generation. |
| `BRIDGE_REPORTS_DIR` | `/data/reports` | directory path | Where generated PDF reports are stored (inside the container). |
| `BRIDGE_REPORTS_PATH` | `./reports` | host path | Host directory bind-mounted to `/data/reports` in Docker. Reports are directly accessible here for backup or file sharing. |

**Storage estimate:** At 1Hz with 2 banks and 10 outlets, the database grows by roughly 50-100 MB per month. With the default 60-day retention, expect the database to stabilize around 100-200 MB. PDF reports are ~30-50 KB each and are never automatically deleted.

---

## InfluxDB Settings

These configure the optional InfluxDB time-series database. InfluxDB is not required -- the bridge stores 60 days of history in SQLite by default. InfluxDB is useful for longer retention, advanced queries, or Grafana dashboards.

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_URL` | `http://influxdb:8086` | InfluxDB URL |
| `INFLUXDB_ORG` | `cyber-pdu` | InfluxDB organization |
| `INFLUXDB_BUCKET` | `pdu` | InfluxDB bucket name |
| `INFLUXDB_ADMIN_USER` | `admin` | Initial admin username (set during first run) |
| `INFLUXDB_ADMIN_PASSWORD` | `changeme123` | Initial admin password -- **change this in production** |
| `INFLUXDB_ADMIN_TOKEN` | `cyber-pdu-admin-token` | API token for Telegraf writes -- **change this in production** |

---

## Telegraf Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAF_MQTT_BROKER` | `tcp://mosquitto:1883` | MQTT broker URL for Telegraf's MQTT consumer input |

---

## Multi-PDU: pdus.json Format

For monitoring multiple PDUs from a single bridge instance, create a `pdus.json` file. This file takes priority over the single-PDU environment variables (`PDU_HOST`, etc.).

The file is stored at the path specified by `BRIDGE_PDUS_FILE` (default: `/data/pdus.json`). Since the bridge container mounts the `bridge-data` Docker volume at `/data`, you can either:

1. Use the `./wizard` script to generate the file interactively.
2. Use the REST API (`POST /api/pdus`) to add PDUs at runtime.
3. Create it manually inside the container's data volume.

### pdus.json structure

```json
{
  "pdus": [
    {
      "device_id": "rack1-pdu",
      "host": "192.168.20.177",
      "snmp_port": 161,
      "community_read": "public",
      "community_write": "private",
      "label": "Main Rack PDU",
      "enabled": true,
      "num_banks": 2
    },
    {
      "device_id": "rack2-pdu",
      "host": "192.168.20.178",
      "snmp_port": 161,
      "community_read": "public",
      "community_write": "private",
      "label": "Secondary Rack PDU",
      "enabled": true,
      "num_banks": 2
    }
  ]
}
```

### Field reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `device_id` | Yes | -- | Unique identifier. Used in MQTT topics and API queries. Must not contain `/ # + ` or spaces. |
| `host` | Yes | -- | IP address or hostname of the PDU |
| `snmp_port` | No | `161` | SNMP port |
| `community_read` | No | `public` | SNMP read community string |
| `community_write` | No | `private` | SNMP write community string |
| `label` | No | `""` | Human-friendly display name |
| `enabled` | No | `true` | Set to `false` to skip this PDU without removing it from the config |
| `num_banks` | No | `2` | Default bank count (auto-detected from SNMP at startup if available) |
| `serial_port` | No | `""` | Serial device path (e.g., `/dev/ttyUSB0`) for management access |
| `serial_baud` | No | `9600` | Serial baud rate |
| `serial_username` | No | `"admin"` | Serial console login username |
| `serial_password` | No | `"cyber"` | Serial console login password |
| `transport` | No | `"snmp"` | Primary transport: `"snmp"`, `"serial"`, or `"both"` |

See [Multi-PDU](multi-pdu.md) for the full setup guide.

---

## Automation Rules Reference

Automation rules let the bridge automatically control outlets based on voltage thresholds, ATS source status, or time-of-day schedules. Rules are stored in `rules.json` (path configured by `BRIDGE_RULES_FILE`).

You can manage rules through:
- The web dashboard's Automation panel
- The REST API (`GET/POST /api/rules`, `PUT/DELETE /api/rules/{name}`)
- Direct editing of the JSON file

### Rule structure

```json
{
  "name": "low-voltage-shutoff",
  "input": 1,
  "condition": "voltage_below",
  "threshold": 108,
  "outlet": 5,
  "action": "off",
  "restore": true,
  "delay": 5,
  "enabled": true,
  "days_of_week": [0, 1, 2, 3, 4],
  "schedule_type": "continuous"
}
```

### Rule fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | -- | Unique name for the rule |
| `input` | No | `0` | Which input source to monitor: `1` = Source A, `2` = Source B. Ignored for time-based conditions. |
| `condition` | Yes | -- | Trigger condition (see table below) |
| `threshold` | Yes | -- | Condition threshold: voltage in volts (float), time as `"HH:MM"`, or time range as `"HH:MM-HH:MM"` |
| `outlet` | Yes | -- | Outlet number (1-based), comma-separated list (`"1,3,5"`), or range (`"1-4"`) |
| `action` | Yes | -- | Action when triggered: `"on"` or `"off"` |
| `restore` | No | `true` | Reverse the action when the condition clears |
| `delay` | No | `5` | Seconds the condition must hold before the rule fires |
| `enabled` | No | `true` | Set to `false` to disable without deleting |
| `days_of_week` | No | `null` | Array of day numbers (0=Mon, 6=Sun). `null` = every day. |
| `schedule_type` | No | `"continuous"` | `"continuous"` (re-arms) or `"oneshot"` (auto-disables after firing once) |

### Available conditions

| Condition | Threshold Format | Description |
|-----------|-----------------|-------------|
| `voltage_below` | Float (volts) | Triggers when source voltage drops below threshold |
| `voltage_above` | Float (volts) | Triggers when source voltage rises above threshold |
| `ats_source_is` | Integer (`1` or `2`) | Triggers when the active ATS source matches (1=A, 2=B) |
| `ats_preferred_lost` | *(ignored)* | Triggers when the ATS has transferred away from the preferred source |
| `time_after` | `"HH:MM"` | Triggers after the specified time of day |
| `time_before` | `"HH:MM"` | Triggers before the specified time of day |
| `time_between` | `"HH:MM-HH:MM"` | Triggers during the time range. Supports midnight wrapping (e.g., `"22:00-06:00"`) |

### Example rules

**Turn off non-essential equipment during a brownout:**

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

**Night-mode: turn off lab lights after hours:**

```json
{
  "name": "night-mode-lights",
  "input": 0,
  "condition": "time_between",
  "threshold": "22:00-06:00",
  "outlet": 8,
  "action": "off",
  "restore": true,
  "delay": 0
}
```

**Alert when ATS switches to backup power:**

```json
{
  "name": "backup-power-shed",
  "input": 0,
  "condition": "ats_preferred_lost",
  "threshold": 0,
  "outlet": 10,
  "action": "off",
  "restore": true,
  "delay": 5
}
```

---

## Home Assistant Setup

The bridge automatically publishes [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) configuration to Home Assistant when it starts. No manual configuration is needed if your Home Assistant instance is connected to the same MQTT broker.

### What gets auto-discovered

| Entity Type | Count | Description |
|------------|-------|-------------|
| **Switches** | 1 per outlet | On/off control for each outlet |
| **Sensors** | 6 per bank | Voltage, current, power, apparent power, power factor, load state |
| **Sensors** | 2 (input) | Input voltage, input frequency |
| **Binary sensor** | 1 | Bridge online/offline status |

### Requirements

1. Home Assistant must have the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured and connected to the same Mosquitto broker.
2. MQTT Discovery must be enabled (it is enabled by default in Home Assistant).

### How it works

On startup, the bridge publishes retained configuration messages to `homeassistant/switch/...`, `homeassistant/sensor/...`, and `homeassistant/binary_sensor/...`. Home Assistant picks these up automatically and creates the corresponding entities.

The device appears in Home Assistant under the name `CyberPower {DEVICE_ID}` with the model detected from the PDU's SNMP identity.

---

## Outlet Naming

You can assign custom names to outlets through the web dashboard or the REST API. Custom names override the names stored on the PDU hardware and persist across restarts.

### Via the web dashboard

Click the pencil icon next to any outlet name in the dashboard to rename it.

### Via the REST API

```bash
# Set a custom name
curl -X PUT http://localhost:8080/api/outlets/1/name \
  -H 'Content-Type: application/json' \
  -d '{"name": "File Server"}'

# Clear a custom name (reverts to PDU hardware name)
curl -X PUT http://localhost:8080/api/outlets/1/name \
  -H 'Content-Type: application/json' \
  -d '{"name": ""}'

# List all custom names
curl http://localhost:8080/api/outlet-names
```

Custom names are saved to a JSON file at the path specified by `BRIDGE_OUTLET_NAMES_FILE` (default: `/data/outlet_names.json`). In multi-PDU mode, each PDU gets its own outlet names file (`/data/outlet_names_{device_id}.json`).
