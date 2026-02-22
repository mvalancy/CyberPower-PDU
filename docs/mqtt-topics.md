# MQTT Topic Reference

Device ID defaults to `pdu44001`. Outlet numbers are 1-indexed (1-24). Bank numbers are 1-indexed (1-2).

## Status Topics (published by bridge, retained, ~1Hz)

| Topic | Payload | Description |
|-------|---------|-------------|
| `pdu/{device}/status` | JSON | Full PDU summary with timestamp |
| `pdu/{device}/input/voltage` | float | Input voltage (volts) |
| `pdu/{device}/input/frequency` | float | Input frequency (Hz) |
| `pdu/{device}/outlet/{n}/state` | `on` or `off` | Outlet relay state |
| `pdu/{device}/outlet/{n}/name` | string | Outlet name from PDU config |
| `pdu/{device}/outlet/{n}/current` | float | Current draw (amps), if available |
| `pdu/{device}/outlet/{n}/power` | float | Active power (watts), if available |
| `pdu/{device}/outlet/{n}/energy` | float | Cumulative energy (kWh), if available |
| `pdu/{device}/bank/{n}/current` | float | Bank total current (amps) |
| `pdu/{device}/bank/{n}/voltage` | float | Bank voltage (volts) |
| `pdu/{device}/bank/{n}/power` | float | Bank active power (watts) |
| `pdu/{device}/bank/{n}/apparent_power` | float | Bank apparent power (VA) |
| `pdu/{device}/bank/{n}/power_factor` | float | Bank power factor (0-1) |
| `pdu/{device}/bank/{n}/load_state` | string | `normal`, `low`, `nearOverload`, or `overload` |

## Control Topics

| Topic | Payload | Description |
|-------|---------|-------------|
| `pdu/{device}/outlet/{n}/command` | `on`, `off`, or `reboot` | Send command to outlet |
| `pdu/{device}/outlet/{n}/command/response` | JSON | Command result |

### Command Response Format

```json
{
  "success": true,
  "command": "off",
  "outlet": 1,
  "error": null,
  "ts": 1708531200.0
}
```

## Bridge Meta

| Topic | Payload | Description |
|-------|---------|-------------|
| `pdu/{device}/bridge/status` | `online` or `offline` | Bridge LWT |

## Notes

- Per-outlet current/power/energy may not be available on the PDU44001 (Switched model, not Metered-by-Outlet). The bridge publishes only what the PDU reports.
- All status topics use retained messages — subscribers get the latest value immediately on connect.
- The `status` topic JSON includes a `timestamp` field (Unix epoch).
