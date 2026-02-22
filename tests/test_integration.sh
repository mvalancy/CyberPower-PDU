#!/usr/bin/env bash
# Integration test: verifies the full stack with mock mode
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Integration Test (Mock Mode) ==="

export BRIDGE_MOCK_MODE=true

echo "Building and starting stack..."
docker compose up -d --build
sleep 8

echo ""
echo "Service status:"
docker compose ps

echo ""
echo "Checking MQTT topics..."
TOPICS=$(timeout 5 mosquitto_sub -h localhost -t 'pdu/#' -C 10 -v 2>/dev/null || true)
if [ -z "$TOPICS" ]; then
    echo "FAIL: No MQTT messages"
    docker compose logs bridge
    docker compose down
    exit 1
fi

echo "Sample topics:"
echo "$TOPICS" | head -10 | sed 's/^/  /'

echo ""
echo "Checking bridge online..."
STATUS=$(timeout 3 mosquitto_sub -h localhost -t 'pdu/+/bridge/status' -C 1 2>/dev/null || true)
echo "Bridge status: $STATUS"

echo ""
echo "Sending command: outlet 5 off"
mosquitto_pub -h localhost -t 'pdu/pdu44001/outlet/5/command' -m 'off'
sleep 2

RESP=$(timeout 3 mosquitto_sub -h localhost -t 'pdu/+/outlet/5/command/response' -C 1 2>/dev/null || true)
echo "Response: $RESP"

echo ""
echo "=== Integration test complete ==="
echo "Leaving stack running. Stop with: docker compose down"
