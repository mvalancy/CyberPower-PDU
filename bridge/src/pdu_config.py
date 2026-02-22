# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
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
    host: str                           # IP address or hostname
    snmp_port: int = 161
    community_read: str = "public"
    community_write: str = "private"
    label: str = ""                     # Human-friendly name
    enabled: bool = True
    num_banks: int = 2                  # Default; auto-detected at startup
    serial: str = ""                    # Persisted on first identity discovery
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
        if self.serial:
            d["serial"] = self.serial
        if self.recovery_subnet:
            d["recovery_subnet"] = self.recovery_subnet
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PDUConfig":
        return cls(
            device_id=d["device_id"],
            host=d["host"],
            snmp_port=int(d.get("snmp_port", 161)),
            community_read=d.get("community_read", "public"),
            community_write=d.get("community_write", "private"),
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
        if not self.host:
            raise ValueError(f"PDU {self.device_id!r} has no host configured")
        if not (1 <= self.snmp_port <= 65535):
            raise ValueError(
                f"PDU {self.device_id!r} snmp_port out of range: {self.snmp_port}"
            )


def load_pdu_configs(pdus_file: str = DEFAULT_PDUS_FILE,
                     env_host: str = "",
                     env_port: int = 161,
                     env_community_read: str = "public",
                     env_community_write: str = "private",
                     env_device_id: str = "pdu44001",
                     mock_mode: bool = False) -> list[PDUConfig]:
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

    if env_host:
        pdu = PDUConfig(
            device_id=env_device_id,
            host=env_host,
            snmp_port=env_port,
            community_read=env_community_read,
            community_write=env_community_write,
        )
        pdu.validate()
        logger.info("Using single PDU from env vars: %s at %s:%d",
                     pdu.device_id, pdu.host, pdu.snmp_port)
        return [pdu]

    raise ValueError(
        "No PDU configuration found. Either:\n"
        "  1. Create a pdus.json file (use ./wizard)\n"
        "  2. Set PDU_HOST in .env\n"
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
