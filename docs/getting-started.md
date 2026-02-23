# Getting Started

> **Docs:** [Getting Started](getting-started.md) | [Configuration](configuration.md) | [API Reference](api-reference.md) | [Architecture](architecture.md) | [MQTT Topics](mqtt-topics.md) | [SNMP OIDs](snmp-oids.md) | [Multi-PDU](multi-pdu.md) | [Security](security.md) | [Troubleshooting](troubleshooting.md)

This guide takes you from zero to a running dashboard in about five minutes. By the end, you will have the bridge polling your PDU (or a simulated one), publishing data to MQTT, and serving a live web dashboard.

---

## Why Would I Want It?

- **Dual-source power monitoring** — See both inputs in real time: grid on A, battery/solar on B. Know instantly when the ATS transfers.
- **Protect equipment during transfers** — Automation rules shed non-critical loads before a transfer, preventing backfeed or overloading a battery inverter.
- **See everything at a glance** — Live dashboard: outlet states, power draw per bank, ATS status, per-source voltage/frequency.
- **Control outlets remotely** — On/off/reboot from the web UI, MQTT, or REST API.
- **Track power history** — 60 days of 1-second charts with CSV export.
- **Automate with rules** — "If voltage drops below 108V, turn off outlet 5." "When ATS switches to Source B, shed the lab." "At 10 PM, lights off."
- **Home Assistant integration** — MQTT auto-discovery creates switches and sensors automatically.
- **Multiple PDUs** — One bridge instance handles any number of CyberPower PDUs.
- **Full device management** — Change thresholds, ATS settings, network config, passwords, and notifications from the web UI (requires serial connection or mock mode).

---

## Features

### Real-Time Monitoring
- ATS dual-source monitoring with animated transfer switch diagram
- Per-bank voltage, current, power, apparent power, and power factor
- Per-outlet state, current, power, and cumulative energy (kWh)
- Environmental monitoring (temperature, humidity, contact closures)
- 1-second poll resolution

### Dual Transport: SNMP + Serial
- SNMP (network) for monitoring and basic outlet control
- RS-232 serial console for full PDU management
- Automatic failover between transports with health tracking
- Serial port auto-discovery for USB-to-serial adapters

### Outlet Control
- On/off/reboot via web dashboard, MQTT, or REST API
- Delayed on/off commands with cancel support
- Custom outlet naming with persistence across restarts

### Full PDU Management (via Serial)
- Load threshold configuration (overload, near-overload, low-load per bank)
- ATS configuration (preferred source, sensitivity, voltage limits, coldstart delay)
- Network configuration (IP, subnet, gateway, DHCP)
- Security: default credential detection, password change
- Notification configuration (SNMP traps, SMTP, email recipients, syslog)
- EnergyWise power saving configuration
- User account management
- Event log viewer

### Historical Data
- 60 days of 1Hz samples in SQLite (WAL mode)
- Auto-downsampling for fast chart rendering (1s to 30m resolution)
- CSV export for banks and outlets
- Weekly energy reports with per-outlet breakdown

### Automation Engine
- Voltage threshold rules (brownout protection)
- ATS source monitoring (backup power shedding)
- Time-of-day schedules with midnight wrapping
- Days-of-week filtering and one-shot rules
- Multi-outlet targeting (comma-separated or range syntax)
- Auto-restore when conditions clear
- Enable/disable toggle per rule

### Home Assistant Integration
- MQTT auto-discovery for switches, sensors, and binary sensors
- Per-device entities with model and firmware metadata
- Bridge online/offline status via LWT

### Multi-PDU Support
- Monitor any number of PDUs from a single bridge instance
- Per-device MQTT namespacing, automation rules, and outlet names
- Network scanner and interactive setup wizard (`./wizard`)
- REST API for runtime PDU management (add/remove/test)

### Health & Resilience
- Docker HEALTHCHECK integration
- DHCP resilience: if your PDU changes IP, the bridge auto-recovers via subnet scan or serial fallback
- Graduated state machine (HEALTHY > DEGRADED > RECOVERING > LOST)
- MQTT publish queue with reconnect drain
- SQLite auto-recovery after write errors

---

## Prerequisites

You need two things installed on the machine that will run the bridge:

| Requirement | Why | Check |
|-------------|-----|-------|
| **Git** | Clone the repository | `git --version` |
| **Docker** with Compose | Runs all services in containers | `docker compose version` |

If you do not have Docker installed, the `./bootstrap` script can install it for you (see below).

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/mvalancy/CyberPower-PDU.git
cd CyberPower-PDU
```

---

## Step 2: Install System Dependencies (Optional)

If you already have Docker and Docker Compose installed, skip this step. Otherwise, run the bootstrap script:

```bash
./bootstrap
```

This installs Docker, mosquitto-clients (for debugging MQTT), SNMP tools, and Python test dependencies. It requires `sudo` and is designed for Debian/Ubuntu systems.

After bootstrap completes, **log out and back in** (or run `newgrp docker`) so your user can access Docker without sudo.

---

## Step 3: Set Up the Stack

```bash
./setup
```

This script does three things:

1. Checks that Docker and Docker Compose are available.
2. Creates a `.env` file from the template (`.env.example`) if one does not already exist.
3. Pulls container images and builds the bridge container.

---

## Step 4: Configure Your PDU

Open the `.env` file in your editor and set your PDU's IP address:

```bash
nano .env
```

The most important setting is `PDU_HOST`. Change it to your PDU's IP address:

```ini
PDU_HOST=192.168.20.177    # <-- your PDU's IP address
```

If your PDU uses non-default SNMP community strings, update those too:

```ini
PDU_COMMUNITY_READ=public   # SNMP read community
PDU_COMMUNITY_WRITE=private  # SNMP write community
```

See [Configuration](configuration.md) for the full list of settings.

---

## Step 5: Start the Stack

```bash
./start
```

This starts four Docker containers:

| Container | What It Does |
|-----------|-------------|
| **mosquitto** | MQTT message broker (ports 1883, 9001) |
| **influxdb** | Time-series database for long-term storage (port 8086) |
| **telegraf** | Pipes MQTT data into InfluxDB |
| **bridge** | The SNMP-to-MQTT bridge + web dashboard (port 8080) |

The script waits for each service to become healthy before finishing.

---

## Step 6: Open the Dashboard

Open your browser and go to:

```
http://localhost:8080
```

You should see the live dashboard with:

- **ATS panel** showing dual power sources and transfer switch status
- **Outlet tiles** with on/off controls and power readings
- **Historical charts** for power, voltage, and current
- **Automation rules** panel for creating voltage-based and time-based rules

---

## Try Without a PDU (Mock Mode)

If you do not have a physical PDU available, you can run the entire stack with simulated data. This is useful for exploring the dashboard, testing automation rules, or developing integrations.

Edit your `.env` file and set:

```ini
BRIDGE_MOCK_MODE=true
```

Then run:

```bash
./setup
./start
```

The bridge generates realistic simulated data: voltage drift around 120V, varying current per bank, random outlet state changes, and ATS source switching. Everything works the same as with a real PDU -- MQTT topics are published, history is recorded, automation rules fire, and the dashboard updates in real time.

To switch back to a real PDU later, change `BRIDGE_MOCK_MODE` back to `false` and set `PDU_HOST` to your PDU's address.

---

## Verifying Everything Works

Once the stack is running, you can verify each subsystem:

### Check MQTT messages

```bash
# Watch all PDU topics (values update every second)
mosquitto_sub -t 'pdu/#' -v
```

You should see a stream of messages like:

```
pdu/pdu44001/input/voltage 120.4
pdu/pdu44001/bank/1/power 150
pdu/pdu44001/outlet/1/state on
```

### Check bridge logs

```bash
./start --logs
```

Look for lines like:

```
INFO  [pdu44001] Poll #1: voltage=120.4V, 10 outlets, 2 banks (45ms)
```

### Check the health endpoint

```bash
curl http://localhost:8080/api/health
```

A healthy response looks like:

```json
{
  "status": "healthy",
  "issues": [],
  "pdu_count": 1,
  "uptime_seconds": 42.5
}
```

### Send a test command

```bash
# Turn outlet 1 off (use with caution on a real PDU!)
mosquitto_pub -t 'pdu/pdu44001/outlet/1/command' -m 'off'
```

---

## What Happens Next

Now that you have the bridge running, here are some things to explore:

- **[Configuration](configuration.md)** -- Fine-tune poll intervals, retention periods, and MQTT settings.
- **[API Reference](api-reference.md)** -- Build integrations using the REST API.
- **[Multi-PDU](multi-pdu.md)** -- Monitor multiple PDUs from a single bridge instance.
- **[Security](security.md)** -- Harden SNMP, MQTT, and InfluxDB for production use.
- **[Architecture](architecture.md)** -- Understand how data flows from PDU hardware to your dashboard.

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start the stack | `./start` |
| Stop the stack | `./start --stop` |
| Restart the stack | `./start --restart` |
| Rebuild after code changes | `./start --rebuild` |
| Check service status | `./start --status` |
| View live logs | `./start --logs` |
| Enable auto-start on boot | `./start --install` |
| Watch MQTT traffic | `mosquitto_sub -t 'pdu/#' -v` |
| Run unit tests | `./test` |
| Run browser E2E tests | `./test --e2e-mock` |
| Run hardware validation | `PDU_HOST=x.x.x.x ./test --hardware` |
| Run mock integration test | `./test --mock` |
| Open dashboard | `http://localhost:8080` |
