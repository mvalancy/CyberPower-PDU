# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""PDU configuration — single or multi-PDU from JSON file or env vars."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PDUS_FILE = "/data/pdus.json"


@dataclass
class PDUConfig:
    """Configuration for a single PDU device."""
    device_id: str                      # MQTT topic key, e.g., "rack1-pdu"
    host: str = ""                      # IP address or hostname (empty = no SNMP)
    snmp_port: int = 161
    community_read: str = "public"
    community_write: str = "private"
    serial_port: str = ""               # Serial port path, e.g., "/dev/ttyUSB3"
    serial_baud: int = 9600
    serial_username: str = "cyber"
    serial_password: str = "cyber"
    transport: str = "snmp"             # Primary transport: "snmp" or "serial"
    label: str = ""                     # Human-friendly name
    enabled: bool = True
    num_banks: int = 2                  # Default; auto-detected at startup
    serial: str = ""                    # Hardware serial number (persisted on first discovery)
    recovery_subnet: str = ""           # Override auto-detected /24 for DHCP recovery

    def to_dict(self) -> dict:
        d = {
            "device_id": self.device_id,
            "host": self.host,
            "snmp_port": self.snmp_port,
            "community_read": self.community_read,
            "community_write": self.community_write,
            "label": self.label,
            "enabled": self.enabled,
            "num_banks": self.num_banks,
        }
        if self.serial_port:
            d["serial_port"] = self.serial_port
            d["serial_baud"] = self.serial_baud
            d["serial_username"] = self.serial_username
            if self.serial_password:
                d["serial_password"] = self.serial_password
        if self.transport != "snmp":
            d["transport"] = self.transport
        if self.serial:
            d["serial"] = self.serial
        if self.recovery_subnet:
            d["recovery_subnet"] = self.recovery_subnet
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PDUConfig":
        return cls(
            device_id=d["device_id"],
            host=d.get("host", ""),
            snmp_port=int(d.get("snmp_port", 161)),
            community_read=d.get("community_read", "public"),
            community_write=d.get("community_write", "private"),
            serial_port=d.get("serial_port", ""),
            serial_baud=int(d.get("serial_baud", 9600)),
            serial_username=d.get("serial_username", "cyber"),
            serial_password=d.get("serial_password", "cyber"),
            transport=d.get("transport", "snmp"),
            label=d.get("label", ""),
            enabled=d.get("enabled", True),
            num_banks=int(d.get("num_banks", 2)),
            serial=d.get("serial", ""),
            recovery_subnet=d.get("recovery_subnet", ""),
        )

    def validate(self):
        if any(c in self.device_id for c in "/#+ "):
            raise ValueError(
                f"device_id contains invalid MQTT characters: {self.device_id!r}"
            )
        if not self.host and not self.serial_port:
            raise ValueError(
                f"PDU {self.device_id!r} has no host or serial_port configured"
            )
        if self.host and not (1 <= self.snmp_port <= 65535):
            raise ValueError(
                f"PDU {self.device_id!r} snmp_port out of range: {self.snmp_port}"
            )
        if self.transport not in ("snmp", "serial"):
            raise ValueError(
                f"PDU {self.device_id!r} transport must be 'snmp' or 'serial', got {self.transport!r}"
            )


def load_pdu_configs(pdus_file: str = DEFAULT_PDUS_FILE,
                     env_host: str = "",
                     env_port: int = 161,
                     env_community_read: str = "public",
                     env_community_write: str = "private",
                     env_device_id: str = "pdu44001",
                     mock_mode: bool = False,
                     env_serial_port: str = "",
                     env_serial_baud: int = 9600,
                     env_serial_username: str = "cyber",
                     env_serial_password: str = "cyber",
                     env_transport: str = "snmp") -> list[PDUConfig]:
    """Load PDU configs with backward compatibility.

    Priority:
    1. pdus.json file if it exists
    2. Environment variables (single PDU — existing .env works unchanged)
    3. Mock mode generates a mock config
    """
    path = Path(pdus_file)

    if path.exists():
        try:
            data = json.loads(path.read_text())
            pdus = []
            for d in data.get("pdus", []):
                pdu = PDUConfig.from_dict(d)
                pdu.validate()
                pdus.append(pdu)
            if pdus:
                logger.info("Loaded %d PDU(s) from %s", len(pdus), path)
                return pdus
            logger.warning("pdus.json exists but has no PDUs, falling back to env vars")
        except Exception:
            logger.exception("Failed to load %s, falling back to env vars", path)

    if mock_mode:
        logger.info("Mock mode — using simulated PDU config")
        return [PDUConfig(
            device_id=env_device_id,
            host="127.0.0.1",
            label="Mock PDU",
        )]

    if env_host or env_serial_port:
        pdu = PDUConfig(
            device_id=env_device_id,
            host=env_host,
            snmp_port=env_port,
            community_read=env_community_read,
            community_write=env_community_write,
            serial_port=env_serial_port,
            serial_baud=env_serial_baud,
            serial_username=env_serial_username,
            serial_password=env_serial_password,
            transport=env_transport,
        )
        pdu.validate()
        transport_desc = []
        if env_host:
            transport_desc.append(f"SNMP {env_host}:{env_port}")
        if env_serial_port:
            transport_desc.append(f"Serial {env_serial_port}")
        logger.info("Using single PDU from env vars: %s via %s",
                     pdu.device_id, " + ".join(transport_desc))
        return [pdu]

    raise ValueError(
        "No PDU configuration found. Either:\n"
        "  1. Create a pdus.json file (use ./wizard)\n"
        "  2. Set PDU_HOST and/or PDU_SERIAL_PORT in .env\n"
        "  3. Enable BRIDGE_MOCK_MODE=true for testing"
    )


def save_pdu_configs(pdus: list[PDUConfig], pdus_file: str = DEFAULT_PDUS_FILE):
    """Save PDU configs to JSON file atomically."""
    path = Path(pdus_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"pdus": [p.to_dict() for p in pdus]}, indent=2)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(data)
        tmp.rename(path)
        logger.info("Saved %d PDU config(s) to %s", len(pdus), path)
    except Exception:
        logger.exception("Failed to save PDU configs")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
