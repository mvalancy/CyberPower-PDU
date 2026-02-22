"""Configuration from environment variables."""

import os


class Config:
    def __init__(self):
        self.pdu_host = os.environ.get("PDU_HOST", "192.168.20.177")
        self.pdu_snmp_port = int(os.environ.get("PDU_SNMP_PORT", "161"))
        self.pdu_community_read = os.environ.get("PDU_COMMUNITY_READ", "public")
        self.pdu_community_write = os.environ.get("PDU_COMMUNITY_WRITE", "private")
        self.device_id = os.environ.get("PDU_DEVICE_ID", "pdu44001")

        self.mqtt_broker = os.environ.get("MQTT_BROKER", "mosquitto")
        self.mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))

        self.poll_interval = float(os.environ.get("BRIDGE_POLL_INTERVAL", "1.0"))
        self.mock_mode = os.environ.get("BRIDGE_MOCK_MODE", "false").lower() == "true"
        self.log_level = os.environ.get("BRIDGE_LOG_LEVEL", "INFO")

        self.rules_file = os.environ.get("BRIDGE_RULES_FILE", "/data/rules.json")
        self.web_port = int(os.environ.get("BRIDGE_WEB_PORT", "8080"))

        self.history_db = os.environ.get("BRIDGE_HISTORY_DB", "/data/history.db")
        self.history_retention_days = int(os.environ.get("HISTORY_RETENTION_DAYS", "60"))
        self.house_monthly_kwh = float(os.environ.get("HOUSE_MONTHLY_KWH", "0"))
        self.outlet_names_file = os.environ.get("BRIDGE_OUTLET_NAMES_FILE", "/data/outlet_names.json")
