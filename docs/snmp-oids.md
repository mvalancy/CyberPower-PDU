# SNMP OID Reference

CyberPower ePDU MIB — base OID: `1.3.6.1.4.1.3808.1.1.3`

## Device Identity

| OID Suffix | Full OID | Description |
|-----------|----------|-------------|
| `.1.1.0` | `...3.1.1.0` | ePDUIdentName — device name |
| `.1.3.0` | `...3.1.3.0` | ePDUIdentDeviceNumOutlets |
| `.1.4.0` | `...3.1.4.0` | ePDUIdentDeviceNumPhases |

## Input

| OID Suffix | Full OID | Description | Unit |
|-----------|----------|-------------|------|
| `.5.7.0` | `...3.5.7.0` | Input voltage | Volts |
| `.5.8.0` | `...3.5.8.0` | Input frequency | Hz |

## Outlet Table

Index `{n}` = outlet number (1-based).

| OID Suffix | Description | Type |
|-----------|-------------|------|
| `.3.3.1.1.2.{n}` | Outlet name | String |
| `.3.3.1.1.4.{n}` | Outlet command | Integer: 1=on, 2=off, 3=reboot |
| `.3.5.1.1.4.{n}` | Outlet state | Integer: 1=on, 2=off |
| `.3.5.1.1.5.{n}` | Outlet current | Tenths of amps |
| `.3.5.1.1.6.{n}` | Outlet power | Watts |
| `.3.5.1.1.7.{n}` | Outlet energy | Tenths of kWh |

## Bank Table

Index `{idx}` = bank number (1-based). PDU44001 has 2 banks.

| OID Suffix | Description | Type |
|-----------|-------------|------|
| `.2.3.1.1.2.{idx}` | Bank current | Tenths of amps |
| `.2.3.1.1.3.{idx}` | Bank load state | 1=normal, 2=low, 3=nearOverload, 4=overload |
| `.2.3.1.1.6.{idx}` | Bank voltage | Tenths of volts |
| `.2.3.1.1.7.{idx}` | Bank active power | Watts |
| `.2.3.1.1.8.{idx}` | Bank apparent power | VA |
| `.2.3.1.1.9.{idx}` | Bank power factor | Hundredths (e.g. 95 = 0.95) |

## Discovery

Run a full walk to see all available OIDs:

```bash
snmpwalk -v2c -c public 192.168.20.177 1.3.6.1.4.1.3808.1.1.3
```

Or use the test script:

```bash
./test --snmpwalk
```
