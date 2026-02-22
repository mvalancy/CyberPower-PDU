# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""Configuration from environment variables with validation."""

import logging
import os

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration is invalid."""


class Config:
    def __init__(self):
        self.pdu_host = os.environ.get("PDU_HOST", "192.168.20.177")
        self.pdu_snmp_port = self._int("PDU_SNMP_PORT", "161", 1, 65535)
        self.pdu_community_read = os.environ.get("PDU_COMMUNITY_READ", "public")
        self.pdu_community_write = os.environ.get("PDU_COMMUNITY_WRITE", "private")
        self.device_id = os.environ.get("PDU_DEVICE_ID", "pdu44001")

        self.mqtt_broker = os.environ.get("MQTT_BROKER", "mosquitto")
        self.mqtt_port = self._int("MQTT_PORT", "1883", 1, 65535)
        self.mqtt_username = os.environ.get("MQTT_USERNAME", "")
        self.mqtt_password = os.environ.get("MQTT_PASSWORD", "")

        self.poll_interval = self._float("BRIDGE_POLL_INTERVAL", "1.0", 0.1, 300)
        self.mock_mode = os.environ.get("BRIDGE_MOCK_MODE", "false").lower() in ("true", "1", "yes")
        self.log_level = os.environ.get("BRIDGE_LOG_LEVEL", "INFO")
        self.snmp_timeout = self._float("BRIDGE_SNMP_TIMEOUT", "2.0", 0.5, 30)
        self.snmp_retries = self._int("BRIDGE_SNMP_RETRIES", "1", 0, 5)

        self.rules_file = os.environ.get("BRIDGE_RULES_FILE", "/data/rules.json")
        self.web_port = self._int("BRIDGE_WEB_PORT", "8080", 1, 65535)

        self.history_db = os.environ.get("BRIDGE_HISTORY_DB", "/data/history.db")
        self.history_retention_days = self._int("HISTORY_RETENTION_DAYS", "60", 1, 365)
        self.house_monthly_kwh = self._float("HOUSE_MONTHLY_KWH", "0", 0, 100000)
        self.outlet_names_file = os.environ.get("BRIDGE_OUTLET_NAMES_FILE", "/data/outlet_names.json")

        # Multi-PDU config file
        self.pdus_file = os.environ.get("BRIDGE_PDUS_FILE", "/data/pdus.json")

        # DHCP recovery
        self.recovery_enabled = os.environ.get(
            "BRIDGE_RECOVERY_ENABLED", "true"
        ).lower() in ("true", "1", "yes")

        # Validate device_id has no MQTT-unsafe characters
        if any(c in self.device_id for c in "/#+ "):
            raise ConfigError(
                f"PDU_DEVICE_ID contains invalid characters: {self.device_id!r}"
            )

        self._log_config()

    @staticmethod
    def _int(env: str, default: str, min_val: int, max_val: int) -> int:
        raw = os.environ.get(env, default)
        try:
            val = int(raw)
        except (ValueError, TypeError):
            raise ConfigError(f"{env}={raw!r} is not a valid integer")
        if not (min_val <= val <= max_val):
            raise ConfigError(f"{env}={val} out of range [{min_val}, {max_val}]")
        return val

    @staticmethod
    def _float(env: str, default: str, min_val: float, max_val: float) -> float:
        raw = os.environ.get(env, default)
        try:
            val = float(raw)
        except (ValueError, TypeError):
            raise ConfigError(f"{env}={raw!r} is not a valid number")
        if not (min_val <= val <= max_val):
            raise ConfigError(f"{env}={val} out of range [{min_val}, {max_val}]")
        return val

    def _log_config(self):
        logger.info(
            "Config: pdu=%s:%d mock=%s poll=%.1fs mqtt=%s:%d retention=%dd",
            self.pdu_host, self.pdu_snmp_port, self.mock_mode,
            self.poll_interval, self.mqtt_broker, self.mqtt_port,
            self.history_retention_days,
        )
