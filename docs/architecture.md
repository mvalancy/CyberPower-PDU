# Architecture

## Overview

The system bridges SNMP from a CyberPower PDU44001 to MQTT, making all PDU data available to any MQTT-connected system (Home Assistant, Node-RED, Telegraf, etc.). A built-in web dashboard and SQLite history store provide self-contained monitoring without external dependencies.

## System Architecture

```mermaid
graph TB
    subgraph Hardware ["🔌 Hardware Layer"]
        PDU["CyberPower PDU44001<br/><small>10 outlets · 2 banks · ATS</small>"]
    end

    subgraph Docker ["🐳 Docker Compose Stack"]
        subgraph BridgeContainer ["Python Bridge Container"]
            SNMP["SNMP Client<br/><small>pysnmp-lextudio</small>"]
            Core["Bridge Core<br/><small>async poll loop</small>"]
            MQTTHandler["MQTT Handler<br/><small>paho-mqtt</small>"]
            WebServer["Web Server<br/><small>aiohttp :8080</small>"]
            History["History Store<br/><small>SQLite + WAL</small>"]
            Automation["Automation Engine<br/><small>rules + scheduling</small>"]
            Mock["Mock PDU<br/><small>simulated data</small>"]
        end

        Mosquitto["Mosquitto Broker<br/><small>:1883 MQTT · :9001 WS</small>"]
        Telegraf["Telegraf<br/><small>MQTT → InfluxDB</small>"]
        InfluxDB["InfluxDB 2.7<br/><small>:8086</small>"]
    end

    subgraph Consumers ["📱 External Consumers"]
        HA["Home Assistant"]
        NodeRED["Node-RED"]
        CLI["mosquitto_pub/sub"]
    end

    PDU <-->|"SNMP v2c<br/>GET/SET"| SNMP
    SNMP --> Core
    Core --> MQTTHandler
    Core --> History
    Core --> Automation
    Core --> WebServer
    Mock -.->|"dev mode"| Core
    MQTTHandler <-->|"MQTT"| Mosquitto
    Mosquitto -->|"subscribe"| Telegraf
    Telegraf -->|"write"| InfluxDB
    Mosquitto <-->|"pub/sub"| Consumers

    style Hardware fill:#1e293b,stroke:#f59e0b,color:#f8fafc
    style Docker fill:#0f172a,stroke:#475569,color:#f8fafc
    style BridgeContainer fill:#172033,stroke:#0ea5e9,color:#f8fafc
    style Consumers fill:#1e293b,stroke:#10b981,color:#f8fafc
    style PDU fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style SNMP fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style Core fill:#1a1a2e,stroke:#0ea5e9,color:#e2e4e9
    style MQTTHandler fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style WebServer fill:#1a1a2e,stroke:#8b5cf6,color:#e2e4e9
    style History fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style Automation fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style Mock fill:#1a1a2e,stroke:#64748b,color:#94a3b8,stroke-dasharray:5
    style Mosquitto fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style Telegraf fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style InfluxDB fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style HA fill:#1a1a2e,stroke:#10b981,color:#e2e4e9
    style NodeRED fill:#1a1a2e,stroke:#10b981,color:#e2e4e9
    style CLI fill:#1a1a2e,stroke:#10b981,color:#e2e4e9
```

## Data Flow

```mermaid
sequenceDiagram
    participant PDU as PDU44001
    participant Bridge as Python Bridge
    participant SQLite as SQLite DB
    participant MQTT as Mosquitto
    participant Web as Web Dashboard
    participant Client as MQTT Client

    loop Every 1 second
        Bridge->>PDU: SNMP GET (all OIDs)
        PDU-->>Bridge: Values (voltage, current, outlets, banks)
        Bridge->>Bridge: Apply metering floor corrections
        Bridge->>Bridge: Apply outlet name overrides
        Bridge->>MQTT: Publish to pdu/{device}/* topics
        Bridge->>SQLite: Buffer sample (1-min aggregation)
        Bridge->>Bridge: Evaluate automation rules
        Bridge->>Web: Update live state
    end

    Client->>MQTT: Publish to outlet/{n}/command
    MQTT->>Bridge: Command received
    Bridge->>PDU: SNMP SET (outlet command OID)
    PDU-->>Bridge: Success/failure
    Bridge->>MQTT: Publish command/response

    Note over SQLite: Minute rollover triggers<br/>flush of averaged samples

    loop Every hour
        Bridge->>SQLite: Generate weekly report (if needed)
        Bridge->>SQLite: Cleanup old data (>90 days)
    end
```

## Bridge Internals

```mermaid
graph LR
    subgraph Poll ["Poll Loop (1Hz)"]
        direction TB
        P1["SNMP GET all OIDs"]
        P2["Parse into PDUData"]
        P3["Apply name overrides"]
        P4["Publish to MQTT"]
        P5["Record to history"]
        P6["Evaluate rules"]
        P7["Update web server"]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7
    end

    subgraph History ["History Store"]
        direction TB
        H1["In-memory buffer<br/><small>per bank/outlet</small>"]
        H2["Minute rollover<br/><small>average + flush</small>"]
        H3["SQLite tables<br/><small>bank_samples<br/>outlet_samples</small>"]
        H4["Auto-downsampling<br/><small>1m/5m/15m/1h</small>"]
        H1 --> H2 --> H3
        H3 --> H4
    end

    subgraph Rules ["Automation Engine"]
        direction TB
        R1["Load rules.json"]
        R2["Check conditions<br/><small>voltage · time-of-day</small>"]
        R3["Execute actions<br/><small>outlet on/off</small>"]
        R4["Auto-restore<br/><small>when condition clears</small>"]
        R1 --> R2 --> R3 --> R4
    end

    Poll --> History
    Poll --> Rules

    style Poll fill:#0d1117,stroke:#0ea5e9,color:#e2e4e9
    style History fill:#0d1117,stroke:#06b6d4,color:#e2e4e9
    style Rules fill:#0d1117,stroke:#ec4899,color:#e2e4e9
    style P1 fill:#1a1a2e,stroke:#0ea5e9,color:#e2e4e9
    style P2 fill:#1a1a2e,stroke:#0ea5e9,color:#e2e4e9
    style P3 fill:#1a1a2e,stroke:#0ea5e9,color:#e2e4e9
    style P4 fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style P5 fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style P6 fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style P7 fill:#1a1a2e,stroke:#8b5cf6,color:#e2e4e9
    style H1 fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style H2 fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style H3 fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style H4 fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style R1 fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style R2 fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style R3 fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style R4 fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
```

## Components

### Python Bridge (`bridge/`)
- Polls PDU via SNMP GET at 1Hz
- Publishes all readings to MQTT with retained messages
- Subscribes to command topics, executes SNMP SET
- Supports mock mode for development/testing
- Single async event loop using `pysnmp-lextudio` and `paho-mqtt`
- Built-in web dashboard via `aiohttp` on port 8080
- SQLite history with 1-minute aggregation (WAL mode)
- Automation engine with voltage and time-of-day rules

### Mosquitto
- Eclipse Mosquitto 2 with anonymous access
- MQTT on port 1883, WebSocket on 9001
- Retained messages for latest state

### Telegraf
- Uses `inputs.mqtt_consumer` (not SNMP plugin)
- Subscribes to `pdu/#` topics
- Parses topic structure into tags (device, type, index, metric)
- Writes to InfluxDB v2

### InfluxDB
- InfluxDB 2.7 for time-series storage
- Auto-provisioned org/bucket/token via env vars
- Web UI at port 8086

## History Storage

The bridge stores minute-averaged samples in a local SQLite database (WAL mode for concurrent reads). This provides self-contained history without requiring InfluxDB.

```mermaid
erDiagram
    bank_samples {
        integer ts "Unix timestamp (minute)"
        integer bank "Bank number (1-2)"
        real voltage "Averaged voltage"
        real current "Averaged current"
        real power "Averaged active power"
        real apparent "Averaged apparent power"
        real pf "Averaged power factor"
    }

    outlet_samples {
        integer ts "Unix timestamp (minute)"
        integer outlet "Outlet number (1-10)"
        text state "Last known state"
        real current "Averaged current"
        real power "Averaged power"
        real energy "Last kWh reading"
    }

    energy_reports {
        integer id "Auto-increment PK"
        text week_start "Monday date"
        text week_end "Sunday date"
        text created_at "ISO timestamp"
        text data "JSON report blob"
    }
```

### Downsampling Strategy

| Query Range | Sample Interval | Max Points |
|-------------|----------------|------------|
| < 6 hours | 1 minute | 360 |
| < 24 hours | 5 minutes | 288 |
| < 7 days | 15 minutes | 672 |
| 30+ days | 1 hour | 720 |

## Mock Mode

Setting `BRIDGE_MOCK_MODE=true` replaces SNMP with a simulated PDU that generates realistic data (voltage drift, per-bank metering, outlet states). Used for development and CI testing.
