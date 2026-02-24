# CyberPower PDU Bridge

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab.svg?logo=python&logoColor=white)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ed.svg?logo=docker&logoColor=white)](docker-compose.yml)
[![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-660066.svg?logo=eclipsemosquitto&logoColor=white)](docs/mqtt-topics.md)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-Discovery-41bdf5.svg?logo=homeassistant&logoColor=white)](docs/configuration.md)
[![Tests: 942](https://img.shields.io/badge/Tests-942_passing-05ffa1.svg)](#)

A self-hosted SNMP + Serial to MQTT bridge for CyberPower PDUs — including ATS dual-source models — with a real-time web dashboard, device management, historical charts, automation rules, and Home Assistant integration.

<p align="center">
  <img src="docs/screenshots/PDU44001.png" alt="CyberPower PDU44001" width="600" />
</p>

## Quick Start

```bash
git clone https://github.com/mvalancy/CyberPower-PDU.git
cd CyberPower-PDU
./bootstrap          # Install Docker + tools (once, needs sudo)
./setup              # Create .env, pull images, build containers
nano .env            # Set PDU_HOST to your PDU's IP (or BRIDGE_MOCK_MODE=true)
./start              # Start the stack — open http://localhost:8080
```

## Screenshots

<table>
<tr>
<td><a href="docs/features.md#dashboard"><img src="docs/screenshots/dashboard.png" width="400" alt="Dashboard"/></a><br/><sub>Real-time dashboard with ATS monitoring</sub></td>
<td><a href="docs/features.md#authentication"><img src="docs/screenshots/login.png" width="400" alt="Login"/></a><br/><sub>Optional web authentication</sub></td>
</tr>
<tr>
<td><a href="docs/features.md#settings--configuration"><img src="docs/screenshots/settings-pdus.png" width="400" alt="Settings — PDUs"/></a><br/><sub>PDU configuration (SNMP, serial, transport)</sub></td>
<td><a href="docs/features.md#settings--configuration"><img src="docs/screenshots/settings-general.png" width="400" alt="Settings — General"/></a><br/><sub>Polling, MQTT, auth, backup & restore</sub></td>
</tr>
<tr>
<td><a href="docs/features.md#settings--configuration"><img src="docs/screenshots/settings-rename.png" width="400" alt="Settings — Rename"/></a><br/><sub>Device rename & source labels</sub></td>
<td><a href="docs/features.md#pdu-management"><img src="docs/screenshots/settings-manage.png" width="400" alt="Settings — Manage"/></a><br/><sub>Security, network, thresholds, ATS, notifications</sub></td>
</tr>
<tr>
<td><a href="docs/features.md#pdu-management"><img src="docs/screenshots/settings-logs.png" width="400" alt="Settings — Logs"/></a><br/><sub>Live bridge logs with filtering</sub></td>
<td><a href="docs/features.md#historical-data--charts"><img src="docs/screenshots/charts.png" width="400" alt="Charts"/></a><br/><sub>Historical power, voltage, current</sub></td>
</tr>
<tr>
<td><a href="docs/features.md#help"><img src="docs/screenshots/help.png" width="400" alt="Help"/></a><br/><sub>In-app help & troubleshooting</sub></td>
<td></td>
</tr>
</table>

> **[See all features with full-size screenshots →](docs/features.md)**

## CLI Tools

Every user-facing operation is wrapped in a script. No raw `docker` commands needed.

| Command | What it does |
|---------|-------------|
| `./bootstrap` | Install Docker, SNMP tools, Python deps, serial access (once) |
| `./setup` | Create `.env` from template, pull images, build containers |
| `./start` | Start the Docker stack and wait for healthy services |
| `./start --stop` | Stop all containers |
| `./start --restart` | Restart all containers |
| `./start --rebuild` | Rebuild from source, then restart |
| `./start --status` | Show container and systemd service status |
| `./start --logs` | Follow live container logs |
| `./start --mqtt-passwd USER` | Create/update an MQTT password for USER |
| `./start --db-size` | Show history database file size |
| `./start --db-compact` | VACUUM the database to reclaim disk space |
| `./start --check-time` | Show the bridge container's current date/time |
| `./start --install` | Install systemd service for auto-start on boot |
| `./start --uninstall` | Remove the systemd service |
| `./scan` | Discover CyberPower PDUs on the network via SNMP |
| `./wizard` | Interactive first-time setup (discover PDUs, write config) |
| `./test` | Run unit tests with branded HTML report |
| `./test --mock` | Full Docker integration test with simulated data |
| `./test --hardware` | Hardware validation suite (needs `PDU_HOST`) |
| `./test --e2e-mock` | Playwright browser E2E tests |

## First-Time Setup

1. Run `./bootstrap` to install system dependencies (Docker, SNMP tools, serial access)
2. Run `./setup` to create `.env` and build containers
3. Edit `.env` — set `PDU_HOST` to your PDU's IP, or set `BRIDGE_MOCK_MODE=true` to try without hardware
4. Run `./start` to launch the stack
5. Open **http://localhost:8080**
6. **Secure your system** — see [Security](docs/security.md) to lock down MQTT, change default PDU credentials, and enable dashboard auth

## Monitoring

| Service | Port | Description |
|---------|------|-------------|
| Bridge + Web UI | 8080 | Dashboard, REST API, automation engine |
| Mosquitto | 1883 / 9001 | MQTT broker (TCP + WebSocket) |
| InfluxDB | 8086 | Time-series database + UI (optional) |
| Telegraf | -- | MQTT-to-InfluxDB pipe (optional) |

```bash
./start --status          # Container health at a glance
curl localhost:8080/api/health   # JSON health check
```

## Architecture

```mermaid
graph LR
    PDU1["PDU #1<br/><small>SNMP + Serial</small>"]
    PDU2["PDU #2<br/><small>SNMP</small>"]
    Bridge["Python Bridge<br/><small>async poll loop</small>"]
    MQTT["Mosquitto<br/><small>:1883</small>"]
    WebUI["Web Dashboard<br/><small>:8080</small>"]
    SQLite["SQLite History<br/><small>60-day 1Hz</small>"]
    HA["Home Assistant"]
    Telegraf["Telegraf"]
    InfluxDB["InfluxDB<br/><small>:8086</small>"]

    PDU1 <-->|"SNMP GET/SET<br/>RS-232 Serial"| Bridge
    PDU2 <-->|"SNMP GET/SET"| Bridge
    Bridge --> MQTT
    Bridge --> WebUI
    Bridge --> SQLite
    MQTT <-->|"pub/sub"| HA
    MQTT --> Telegraf --> InfluxDB

    style PDU1 fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style PDU2 fill:#1a1a2e,stroke:#f59e0b,color:#e2e4e9
    style Bridge fill:#1a1a2e,stroke:#0ea5e9,color:#e2e4e9
    style MQTT fill:#1a1a2e,stroke:#10b981,color:#e2e4e9
    style WebUI fill:#1a1a2e,stroke:#8b5cf6,color:#e2e4e9
    style SQLite fill:#1a1a2e,stroke:#06b6d4,color:#e2e4e9
    style HA fill:#1a1a2e,stroke:#10b981,color:#e2e4e9
    style Telegraf fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
    style InfluxDB fill:#1a1a2e,stroke:#ec4899,color:#e2e4e9
```

## Documentation

| Guide | What's in it |
|-------|-------------|
| [Features](docs/features.md) | Visual walkthrough of every feature with screenshots |
| [Getting Started](docs/getting-started.md) | Prerequisites, setup, mock mode, features |
| [Configuration](docs/configuration.md) | All `.env` variables, `pdus.json`, automation rules |
| [API Reference](docs/api-reference.md) | REST API with request/response examples |
| [Architecture](docs/architecture.md) | System design, data flow, bridge internals |
| [MQTT Topics](docs/mqtt-topics.md) | Full topic hierarchy with payload formats |
| [SNMP OIDs](docs/snmp-oids.md) | CyberPower ePDU/ePDU2 MIB reference |
| [Multi-PDU](docs/multi-pdu.md) | Monitoring multiple PDUs from one bridge |
| [Security](docs/security.md) | Hardening SNMP, MQTT, InfluxDB, web UI |
| [Troubleshooting](docs/troubleshooting.md) | Symptom-based diagnostic guide |

## License

GPL-3.0 License -- Copyright (c) 2026 Matthew Valancy, Valpatel Software LLC

See [LICENSE](LICENSE) for the full text.
