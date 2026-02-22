# Architecture

## Overview

The system bridges SNMP from a CyberPower PDU44001 to MQTT, making all PDU data available to any MQTT-connected system (Home Assistant, Node-RED, Telegraf, etc.).

## Components

### Python Bridge (`bridge/`)
- Polls PDU via SNMP GET at 1Hz
- Publishes all readings to MQTT with retained messages
- Subscribes to command topics, executes SNMP SET
- Supports mock mode for development/testing
- Single async event loop using `pysnmp-lextudio` and `paho-mqtt`

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

## Data Flow

1. Bridge polls PDU via SNMP every second
2. Bridge publishes values to individual MQTT topics (retained)
3. Telegraf consumes MQTT topics, tags by device/outlet/bank
4. Telegraf writes to InfluxDB
5. Commands arrive on MQTT, bridge executes SNMP SET, publishes response

## Mock Mode

Setting `BRIDGE_MOCK_MODE=true` replaces SNMP with a simulated PDU that generates realistic data (voltage drift, per-bank metering, outlet states). Used for development and CI testing.
