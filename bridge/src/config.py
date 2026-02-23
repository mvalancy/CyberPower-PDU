# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Configuration from environment variables with validation.

Settings can also be persisted to a JSON file via the web UI. Saved settings
override env-var defaults on startup. Env vars that are *explicitly* set by the
user (i.e., non-default) still take precedence.
"""

import json
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

        # Serial transport
        self.serial_port = os.environ.get("PDU_SERIAL_PORT", "")
        self.serial_baud = self._int("PDU_SERIAL_BAUD", "9600", 300, 115200)
        self.serial_username = os.environ.get("PDU_SERIAL_USERNAME", "cyber")
        self.serial_password = os.environ.get("PDU_SERIAL_PASSWORD", "cyber")
        self.transport_primary = os.environ.get("PDU_TRANSPORT", "snmp")

        # Web authentication (opt-in: set BRIDGE_WEB_PASSWORD to enable)
        self.web_username = os.environ.get("BRIDGE_WEB_USERNAME", "admin")
        self.web_password = os.environ.get("BRIDGE_WEB_PASSWORD", "")
        self.session_secret = os.environ.get("BRIDGE_SESSION_SECRET", "")
        self.session_timeout = self._int("BRIDGE_SESSION_TIMEOUT", "86400", 60, 604800)

        # Bridge settings persistence file
        self.settings_file = os.environ.get(
            "BRIDGE_SETTINGS_FILE", "/data/bridge_settings.json"
        )

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

    # ------------------------------------------------------------------
    # Settings persistence — save/load from JSON file
    # ------------------------------------------------------------------

    # Fields that can be saved/loaded from the settings file.
    SAVEABLE_FIELDS = {
        "mqtt_broker": str,
        "mqtt_port": int,
        "mqtt_username": str,
        "mqtt_password": str,
        "poll_interval": float,
        "log_level": str,
        "history_retention_days": int,
        "web_username": str,
        "web_password": str,
        "session_timeout": int,
        "snmp_timeout": float,
        "snmp_retries": int,
        "recovery_enabled": str,  # store as string to avoid bool("false") bug
    }

    def load_saved_settings(self, path: str):
        """Load persisted settings from JSON file, overriding current values.

        This allows the web UI to persist settings across restarts.
        """
        try:
            with open(path) as f:
                saved = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return  # No saved settings — use env/defaults

        for field, typ in self.SAVEABLE_FIELDS.items():
            if field in saved:
                try:
                    if field == "recovery_enabled":
                        # Parse bool from string/bool
                        val = saved[field]
                        self.recovery_enabled = val in (True, "true", "1")
                    else:
                        setattr(self, field, typ(saved[field]))
                except (ValueError, TypeError):
                    logger.warning("Invalid saved setting %s=%r, ignoring",
                                   field, saved[field])

        logger.info("Loaded saved settings from %s", path)

    def save_settings(self, path: str):
        """Persist current saveable settings to JSON file."""
        data = {}
        for field in self.SAVEABLE_FIELDS:
            val = getattr(self, field, None)
            # Convert bools to strings for fields stored as str
            if field == "recovery_enabled" and isinstance(val, bool):
                val = "true" if val else "false"
            data[field] = val
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("Saved settings to %s", path)
        except OSError as e:
            logger.error("Failed to save settings to %s: %s", path, e)

    @property
    def settings_dict(self) -> dict:
        """Return saveable settings as a dict (for GET /api/config)."""
        return {field: getattr(self, field, None)
                for field in self.SAVEABLE_FIELDS}

    def _log_config(self):
        logger.info(
            "Config: pdu=%s:%d mock=%s poll=%.1fs mqtt=%s:%d retention=%dd",
            self.pdu_host, self.pdu_snmp_port, self.mock_mode,
            self.poll_interval, self.mqtt_broker, self.mqtt_port,
            self.history_retention_days,
        )
