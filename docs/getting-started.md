# Getting Started

> **Docs:** [Getting Started](getting-started.md) | [Configuration](configuration.md) | [API Reference](api-reference.md) | [Architecture](architecture.md) | [MQTT Topics](mqtt-topics.md) | [SNMP OIDs](snmp-oids.md) | [Multi-PDU](multi-pdu.md) | [Security](security.md) | [Troubleshooting](troubleshooting.md)

This guide takes you from zero to a running dashboard in about five minutes. By the end, you will have the bridge polling your PDU (or a simulated one), publishing data to MQTT, and serving a live web dashboard.

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
./run
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
./run
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
docker compose logs -f bridge
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
| Start the stack | `./run` |
| Stop the stack | `docker compose down` |
| Rebuild after code changes | `docker compose up -d --build` |
| View bridge logs | `docker compose logs -f bridge` |
| Watch MQTT traffic | `mosquitto_sub -t 'pdu/#' -v` |
| Run tests | `pytest tests/ -v` |
| Run tests with mock data | `./test --mock` |
| Open dashboard | `http://localhost:8080` |
