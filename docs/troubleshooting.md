# Troubleshooting

> **Docs:** [Getting Started](getting-started.md) | [Configuration](configuration.md) | [API Reference](api-reference.md) | [Architecture](architecture.md) | [MQTT Topics](mqtt-topics.md) | [SNMP OIDs](snmp-oids.md) | [Multi-PDU](multi-pdu.md) | [Security](security.md) | [Troubleshooting](troubleshooting.md)

This guide is organized by symptom. Find the problem you are experiencing and follow the diagnostic steps.

> **Tip:** Every script logs its output to the `logs/` directory. If something went wrong during startup, check the most recent log file there. You can also run `./start --logs` to follow live container output.

---

## Dashboard Shows No Data

The web dashboard loads but all values are blank, zero, or showing "no data."

### Step 1: Check if the bridge is running

```bash
./start --status
```

Look for the `bridge` container. It should show `Up` and `healthy`. If it shows `unhealthy` or is not running, check the logs:

```bash
./start --logs
```

### Step 2: Check the health endpoint

```bash
curl http://localhost:8080/api/health
```

If the response shows `"status": "degraded"`, the `issues` array will tell you what is wrong:

- `"No data received yet"` -- The bridge has not successfully polled the PDU.
- `"Data is 45s stale"` -- The bridge polled successfully at some point but has stopped receiving data.
- `"MQTT disconnected"` -- The MQTT connection is down (but this does not prevent the dashboard from working since it uses the REST API).

### Step 3: Check SNMP connectivity

From the bridge host, test that SNMP can reach the PDU:

```bash
snmpget -v2c -c public 192.168.20.177 1.3.6.1.4.1.3808.1.1.3.1.1.0
```

Replace `192.168.20.177` with your PDU's IP and `public` with your read community string.

**If this fails:**
- Verify the PDU IP address is correct and reachable (`ping 192.168.20.177`)
- Verify the SNMP community string matches the PDU's configuration
- Verify SNMP is enabled on the PDU (check the PDU's web management interface)
- Verify no firewall is blocking UDP port 161

### Step 4: Check bridge logs for SNMP errors

```bash
./start --logs
```

Look for:
- `SNMP: GET ... No SNMP response received before timeout` -- PDU is unreachable or slow
- `SNMP: GET ... requestTimedOut` -- Increase `BRIDGE_SNMP_TIMEOUT` in `.env`
- `SNMP: PDU unreachable for N consecutive failures` -- Persistent connectivity problem

### Step 5: Try mock mode

If you cannot reach the PDU, verify the rest of the stack works with simulated data:

```bash
# In .env
BRIDGE_MOCK_MODE=true
```

Then rebuild and restart:

```bash
./start --rebuild
```

If the dashboard works in mock mode, the problem is SNMP connectivity to your PDU.

---

## MQTT Not Flowing

The bridge appears to be running, but no messages appear on MQTT topics.

### Step 1: Check if Mosquitto is running

```bash
./start --status
```

The Mosquitto container should show `healthy`.

### Step 2: Subscribe and listen

```bash
mosquitto_sub -t 'pdu/#' -v
```

If you see no messages after 5 seconds, the bridge is not publishing.

### Step 3: Check bridge MQTT connection

```bash
curl http://localhost:8080/api/health
```

Look at `subsystems.mqtt.connected`. If `false`:

- Verify `MQTT_BROKER` in `.env` is correct. When using `network_mode: host`, it should be `127.0.0.1` (not `mosquitto`, since Docker DNS does not work in host networking mode).
- Verify the MQTT port matches (`MQTT_PORT=1883`).
- If MQTT authentication is enabled, verify `MQTT_USERNAME` and `MQTT_PASSWORD` match the Mosquitto password file.

### Step 4: Check for publish errors

```bash
curl http://localhost:8080/api/health | python3 -m json.tool
```

Look at `subsystems.mqtt.publish_errors`. A non-zero value indicates the bridge is trying to publish but failing. Check the logs:

```bash
./start --logs
```

### Step 5: Test the broker directly

```bash
# In one terminal, subscribe
mosquitto_sub -t 'test/topic' -v

# In another terminal, publish
mosquitto_pub -t 'test/topic' -m 'hello'
```

If the test message does not arrive, Mosquitto itself has a problem.

---

## SNMP Timeouts

The bridge logs show frequent SNMP timeout errors and data updates are delayed or missing.

### Symptoms

```
WARNING SNMP: GET 1.3.6.1.4.1.3808.1.1.3.1.1.0: No SNMP response received before timeout
ERROR   SNMP: PDU unreachable for 10 consecutive failures
```

### Causes and fixes

**Network latency or congestion:**

Increase the SNMP timeout and reduce poll frequency:

```ini
BRIDGE_SNMP_TIMEOUT=5.0     # Default: 2.0
BRIDGE_SNMP_RETRIES=2        # Default: 1
BRIDGE_POLL_INTERVAL=5.0     # Default: 1.0
```

**PDU is overloaded:**

If the PDU has many SNMP clients querying it simultaneously, it may not respond in time. The bridge queries OIDs in parallel batches of 10 -- this is efficient but can overwhelm some PDU firmware. Increasing the timeout usually resolves this.

**Firewall blocking UDP:**

SNMP uses UDP port 161. Verify no firewall between the bridge host and the PDU is blocking UDP:

```bash
# Test UDP connectivity
nc -uzv 192.168.20.177 161
```

**PDU restarted:**

After a PDU reboot, SNMP may take 30-60 seconds to become available. The bridge handles this gracefully -- it logs a warning when it detects a reboot (via sysUptime decreasing) and continues polling.

---

## History Database Growing Too Large

The SQLite database (`history.db`) is consuming too much disk space.

### Check current size

```bash
./start --db-size
```

### Understand the growth rate

At 1Hz with 2 banks and 10 outlets, the database grows by approximately:
- 2-4 MB per day
- 50-100 MB per month
- 100-200 MB at 60-day retention

### Reduce retention

Lower the retention period in `.env`:

```ini
HISTORY_RETENTION_DAYS=30    # Default: 60
```

The hourly cleanup task will delete data older than this threshold.

### Force an immediate cleanup

Restart the bridge to trigger an immediate cleanup cycle, or wait for the next hourly cleanup:

```bash
./start --restart
```

### Reduce poll frequency

If you do not need 1-second resolution, increase the poll interval:

```ini
BRIDGE_POLL_INTERVAL=5.0    # Store one sample every 5 seconds instead of every 1 second
```

This reduces database growth by 5x.

### Compact the database

SQLite does not automatically shrink the file after deleting rows. To reclaim disk space:

```bash
./start --db-compact
```

---

## Automation Rules Not Firing

You have created automation rules but they do not trigger when expected.

### Step 1: Check rule status

```bash
curl http://localhost:8080/api/rules | python3 -m json.tool
```

Look at each rule's `state`:
- `"triggered": false, "condition_since": null` -- Condition has never been met.
- `"triggered": false, "condition_since": 1708531200.0` -- Condition is currently true but the delay period has not elapsed yet.
- `"triggered": true, "fired_at": 1708531200.0` -- Rule has fired and is waiting for the condition to clear before restoring.

### Step 2: Check the event log

```bash
curl http://localhost:8080/api/events | python3 -m json.tool
```

This shows the last 100 automation events. Look for `triggered`, `restored`, or error events.

### Step 3: Verify the condition

For **voltage-based rules**, the bridge checks the per-input SOURCE voltage (from the ePDU2 MIB), not the load bank voltage. This is important for ATS PDUs where the bank voltage always reads ~120V regardless of input conditions.

Check the current source voltages:

```bash
curl http://localhost:8080/api/status | python3 -c "
import json, sys
data = json.load(sys.stdin)
ats = data.get('ats', {})
print(f'Source A: {ats[\"source_a\"][\"voltage\"]}V')
print(f'Source B: {ats[\"source_b\"][\"voltage\"]}V')
"
```

For **time-based rules**, the bridge uses the container's local time. Verify the container's timezone is correct:

```bash
./start --check-time
```

### Step 4: Check the delay

Rules have a `delay` field (default: 5 seconds). The condition must be continuously true for the entire delay period before the rule fires. If the condition flickers (true/false/true), the delay timer resets.

### Step 5: Check for command errors in logs

```bash
./start --logs
```

Look for `command failed` messages. If the rule triggers but the SNMP SET command fails, the rule's condition timer resets and it retries on the next cycle.

### Step 6: Test with a simple rule

Create a rule that should trigger immediately to verify the automation engine is working:

```bash
# Create a rule that fires if voltage is above 100V (should always be true)
curl -X POST http://localhost:8080/api/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "test-rule",
    "input": 1,
    "condition": "voltage_above",
    "threshold": 100,
    "outlet": 1,
    "action": "off",
    "restore": true,
    "delay": 0
  }'
```

**Important:** Delete the test rule after testing to avoid unintended outlet changes:

```bash
curl -X DELETE http://localhost:8080/api/rules/test-rule
```

---

## Container Fails to Start

The bridge container exits immediately after starting.

### Check the logs

```bash
./start --logs
```

Common errors:

**Configuration error:**
```
Configuration error: PDU_DEVICE_ID contains invalid characters: "my pdu"
```
Fix: Device ID must not contain `/`, `#`, `+`, or spaces.

**No PDU configuration:**
```
ValueError: No PDU configuration found.
```
Fix: Either set `PDU_HOST` in `.env`, create a `pdus.json` file, or enable `BRIDGE_MOCK_MODE=true`.

**Port already in use:**
```
OSError: [Errno 98] Address already in use
```
Fix: Another service is using port 8080. Change `BRIDGE_WEB_PORT` in `.env` or stop the conflicting service.

---

## Home Assistant Not Discovering Entities

The bridge is running but Home Assistant does not show the PDU device.

### Check MQTT Discovery messages

```bash
mosquitto_sub -t 'homeassistant/#' -v
```

You should see retained configuration messages for switches and sensors. If not, the bridge may not have published discovery yet (it publishes once at startup).

### Force republish

Restart the bridge to trigger a fresh discovery publish:

```bash
./start --restart
```

### Verify Home Assistant MQTT setup

1. In Home Assistant, go to Settings > Devices & Services > MQTT.
2. Verify it is connected to the same Mosquitto broker.
3. Verify MQTT Discovery is enabled (it is by default).
4. Check the MQTT integration logs for errors.

### Check the topic prefix

Home Assistant expects discovery messages under `homeassistant/`. The bridge publishes to `homeassistant/switch/`, `homeassistant/sensor/`, and `homeassistant/binary_sensor/`. If your Home Assistant uses a different discovery prefix, this may not match.

---

## Management Tab Shows "Requires Serial"

The Manage tab in Settings shows "Requires serial transport" for some features.

### Why this happens

PDU management features (thresholds, ATS config, network settings, security, notifications) require either a serial transport connection or mock mode. SNMP alone cannot access these settings on CyberPower PDUs.

### Solution

1. **Connect via serial** -- Attach a USB-to-serial cable to the PDU's RS-232 port and configure serial settings:

```ini
PDU_SERIAL_PORT=/dev/ttyUSB0
PDU_SERIAL_USERNAME=admin
PDU_SERIAL_PASSWORD=cyber
```

2. **Use mock mode for testing** -- Set `BRIDGE_MOCK_MODE=true` to enable a simulated PDU with full management support.

3. **Check serial permissions** -- The Docker container needs access to the serial device. Add it to `docker-compose.yml`:

```yaml
bridge:
  devices:
    - /dev/ttyUSB0:/dev/ttyUSB0
```

---

## Serial Connection Not Working

The bridge cannot connect to the PDU via serial console.

### Step 1: Verify the serial device exists

```bash
ls -la /dev/ttyUSB*
```

If no devices appear, check that the USB-to-serial adapter is connected and its driver is loaded.

### Step 2: Check permissions

```bash
# Run ./bootstrap to set up serial port access, or manually:
sudo usermod -aG dialout $USER
```

### Step 3: Test the connection manually

```bash
# Install screen for manual serial testing
sudo apt install screen
screen /dev/ttyUSB0 9600
```

Type `sys show` and press Space. You should see a login prompt. The CyberPower PDU44001 uses Space (not Enter) as the submit key.

### Step 4: Check bridge logs

```bash
./start --logs
```

Look for serial connection errors, authentication failures, or timeout messages.
