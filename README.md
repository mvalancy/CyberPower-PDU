# CyberPower PDU44001 Monitoring & Control

SNMP-to-MQTT bridge for the CyberPower PDU44001 switched PDU, with InfluxDB time-series storage via Telegraf.

## Architecture

```
                    SNMP GET/SET
  PDU44001 <========================> Python Bridge
  192.168.20.177                          |
                                     MQTT pub/sub
                                          |
                                     Mosquitto Broker
                                      /        \
                              MQTT sub            MQTT sub/pub
                                /                      \
                          Telegraf                   Any MQTT client
                              |                    (HA, Node-RED, CLI)
                          InfluxDB
```

The Python bridge is the only component that speaks SNMP. Everything else communicates via MQTT.

## Quick Start

```bash
./setup    # Install dependencies, build containers
./run      # Start the stack
```

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable | Default | Description |
|----------|---------|-------------|
| `PDU_HOST` | `192.168.20.177` | PDU IP address |
| `PDU_COMMUNITY_READ` | `public` | SNMP read community |
| `PDU_COMMUNITY_WRITE` | `private` | SNMP write community |
| `PDU_DEVICE_ID` | `pdu44001` | Device identifier in MQTT topics |
| `BRIDGE_POLL_INTERVAL` | `1.0` | Seconds between polls |
| `BRIDGE_MOCK_MODE` | `false` | Use simulated PDU data |

## MQTT Topics

Status (published by bridge, retained):
```
pdu/pdu44001/status                   # JSON summary
pdu/pdu44001/input/voltage            # float
pdu/pdu44001/outlet/{n}/state         # "on"|"off"
pdu/pdu44001/outlet/{n}/name          # string
pdu/pdu44001/bank/{n}/current         # float (amps)
pdu/pdu44001/bank/{n}/voltage         # float (volts)
pdu/pdu44001/bank/{n}/power           # float (watts)
```

Control:
```
pdu/pdu44001/outlet/{n}/command           # "on"|"off"|"reboot"
pdu/pdu44001/outlet/{n}/command/response  # JSON result
```

See [docs/mqtt-topics.md](docs/mqtt-topics.md) for the complete topic reference.

## Testing

```bash
./test              # Test against real PDU
./test --mock       # Full stack with simulated data
./test --snmpwalk   # OID discovery walk
```

Unit tests:
```bash
pip install pytest pytest-asyncio
pytest tests/test_bridge.py -v
```

## Monitoring

```bash
# Watch all MQTT messages
mosquitto_sub -t 'pdu/#' -v

# Toggle an outlet
mosquitto_pub -t 'pdu/pdu44001/outlet/1/command' -m 'off'

# Bridge logs
docker compose logs -f bridge

# InfluxDB UI
open http://localhost:8086
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Mosquitto | 1883 (MQTT), 9001 (WS) | Message broker |
| InfluxDB | 8086 | Time-series database + UI |
| Telegraf | — | MQTT consumer to InfluxDB |
| Bridge | — | SNMP-to-MQTT bridge |

## Docs

- [Architecture](docs/architecture.md)
- [MQTT Topics](docs/mqtt-topics.md)
- [SNMP OIDs](docs/snmp-oids.md)
