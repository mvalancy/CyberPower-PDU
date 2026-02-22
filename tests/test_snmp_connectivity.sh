#!/usr/bin/env bash
# Quick SNMP connectivity check against real PDU
set -euo pipefail

cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

HOST="${PDU_HOST:-192.168.20.177}"
COMMUNITY="${PDU_COMMUNITY_READ:-public}"
BASE="1.3.6.1.4.1.3808.1.1.3"

echo "Testing SNMP connectivity to $HOST..."

echo ""
echo "Device name:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.1.1.0"

echo ""
echo "Outlet count:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.1.3.0"

echo ""
echo "Input voltage:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.5.7.0"

echo ""
echo "Input frequency:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.5.8.0"

echo ""
echo "Bank 1 current:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.2.3.1.1.2.1"

echo ""
echo "Outlet 1 state:"
snmpget -v2c -c "$COMMUNITY" "$HOST" "${BASE}.3.5.1.1.4.1"

echo ""
echo "All checks passed."
