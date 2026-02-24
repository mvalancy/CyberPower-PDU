# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Unit tests for PDUConfig system — single and multi-PDU configuration."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from src.pdu_config import PDUConfig, load_pdu_configs, next_device_id, save_pdu_configs


# ---------------------------------------------------------------------------
# Auto-numbering
# ---------------------------------------------------------------------------

def test_next_device_id_empty():
    assert next_device_id() == "pdu-01"

def test_next_device_id_skips_existing():
    assert next_device_id({"pdu-01"}) == "pdu-02"
    assert next_device_id({"pdu-01", "pdu-02"}) == "pdu-03"

def test_next_device_id_fills_gaps():
    assert next_device_id({"pdu-02"}) == "pdu-01"

def test_from_dict_auto_assigns_id():
    """from_dict with missing device_id uses fallback."""
    cfg = PDUConfig.from_dict({"host": "10.0.0.1"}, fallback_id="pdu-01")
    assert cfg.device_id == "pdu-01"

def test_from_dict_explicit_id_wins():
    cfg = PDUConfig.from_dict({"device_id": "my-pdu", "host": "10.0.0.1"}, fallback_id="pdu-01")
    assert cfg.device_id == "my-pdu"


# ---------------------------------------------------------------------------
# PDUConfig dataclass
# ---------------------------------------------------------------------------

def test_pdu_config_from_dict():
    """Create a PDUConfig from a dictionary."""
    d = {
        "device_id": "rack1-pdu",
        "host": "192.168.1.100",
        "snmp_port": 1161,
        "community_read": "mycommunity",
        "community_write": "mywrite",
        "label": "Rack 1 PDU",
        "enabled": True,
        "num_banks": 4,
    }
    cfg = PDUConfig.from_dict(d)
    assert cfg.device_id == "rack1-pdu"
    assert cfg.host == "192.168.1.100"
    assert cfg.snmp_port == 1161
    assert cfg.community_read == "mycommunity"
    assert cfg.community_write == "mywrite"
    assert cfg.label == "Rack 1 PDU"
    assert cfg.enabled is True
    assert cfg.num_banks == 4


def test_pdu_config_to_dict():
    """Roundtrip: create from dict, convert back to dict, compare."""
    original = {
        "device_id": "test-pdu",
        "host": "10.0.0.50",
        "snmp_port": 161,
        "community_read": "public",
        "community_write": "private",
        "label": "Test PDU",
        "enabled": True,
        "num_banks": 2,
    }
    cfg = PDUConfig.from_dict(original)
    result = cfg.to_dict()
    assert result == original


def test_pdu_config_validate_good():
    """A valid PDUConfig passes validation without error."""
    cfg = PDUConfig(
        device_id="pdu44001",
        host="192.168.1.10",
        snmp_port=161,
    )
    # Should not raise
    cfg.validate()


def test_pdu_config_validate_bad_chars():
    """device_id with MQTT-unsafe characters raises ValueError."""
    for bad_char in ["#", "+", "/", " "]:
        cfg = PDUConfig(
            device_id=f"bad{bad_char}id",
            host="192.168.1.10",
        )
        with pytest.raises(ValueError, match="invalid MQTT characters"):
            cfg.validate()


def test_pdu_config_validate_no_host():
    """Empty host raises ValueError."""
    cfg = PDUConfig(device_id="test", host="")
    with pytest.raises(ValueError, match="no host or serial_port configured"):
        cfg.validate()


def test_pdu_config_validate_bad_port():
    """Out-of-range SNMP port raises ValueError."""
    cfg = PDUConfig(device_id="test", host="10.0.0.1", snmp_port=0)
    with pytest.raises(ValueError, match="snmp_port out of range"):
        cfg.validate()

    cfg2 = PDUConfig(device_id="test", host="10.0.0.1", snmp_port=70000)
    with pytest.raises(ValueError, match="snmp_port out of range"):
        cfg2.validate()


# ---------------------------------------------------------------------------
# load_pdu_configs
# ---------------------------------------------------------------------------

def test_load_from_json_file():
    """Load configs from a pdus.json file."""
    data = {
        "pdus": [
            {"device_id": "pdu-a", "host": "10.0.0.1"},
            {"device_id": "pdu-b", "host": "10.0.0.2", "snmp_port": 1161},
        ]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = f.name

    try:
        configs = load_pdu_configs(pdus_file=tmp_path)
        assert len(configs) == 2
        assert configs[0].device_id == "pdu-a"
        assert configs[0].host == "10.0.0.1"
        assert configs[1].device_id == "pdu-b"
        assert configs[1].snmp_port == 1161
    finally:
        os.unlink(tmp_path)


def test_load_from_env_vars():
    """No pdus.json, env vars create a single PDU config."""
    # Use a non-existent file path so the JSON file branch is skipped
    configs = load_pdu_configs(
        pdus_file="/tmp/nonexistent_pdus_test_12345.json",
        env_host="192.168.1.50",
        env_port=161,
        env_community_read="public",
        env_community_write="private",
        env_device_id="env-pdu",
        mock_mode=False,
    )
    assert len(configs) == 1
    assert configs[0].device_id == "env-pdu"
    assert configs[0].host == "192.168.1.50"


def test_load_mock_mode():
    """Mock mode creates a mock PDU config when no file or env host."""
    configs = load_pdu_configs(
        pdus_file="/tmp/nonexistent_pdus_test_12345.json",
        env_host="",
        mock_mode=True,
    )
    assert len(configs) == 1
    assert configs[0].host == "127.0.0.1"
    assert configs[0].label == "Mock PDU"


def test_load_no_config_raises():
    """No file, no env host, no mock mode -> ValueError."""
    with pytest.raises(ValueError, match="No PDU configuration found"):
        load_pdu_configs(
            pdus_file="/tmp/nonexistent_pdus_test_12345.json",
            env_host="",
            mock_mode=False,
        )


# ---------------------------------------------------------------------------
# save_pdu_configs / roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip():
    """Save configs to a temp file, load them back, verify match."""
    configs = [
        PDUConfig(device_id="pdu-x", host="10.0.0.10", label="PDU X"),
        PDUConfig(device_id="pdu-y", host="10.0.0.20", label="PDU Y", num_banks=4),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "pdus.json")
        save_pdu_configs(configs, pdus_file=path)

        loaded = load_pdu_configs(pdus_file=path)
        assert len(loaded) == 2
        assert loaded[0].device_id == "pdu-x"
        assert loaded[0].host == "10.0.0.10"
        assert loaded[0].label == "PDU X"
        assert loaded[1].device_id == "pdu-y"
        assert loaded[1].num_banks == 4


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_backward_compat_single_pdu():
    """Traditional .env vars still work when no pdus.json exists.

    This mimics the upgrade scenario: a user has PDU_HOST in their .env
    but has not created a pdus.json file yet.
    """
    configs = load_pdu_configs(
        pdus_file="/tmp/nonexistent_pdus_test_12345.json",
        env_host="192.168.20.177",
        env_port=161,
        env_community_read="public",
        env_community_write="private",
        env_device_id="pdu44001",
        mock_mode=False,
    )
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.device_id == "pdu44001"
    assert cfg.host == "192.168.20.177"
    assert cfg.snmp_port == 161
    assert cfg.community_read == "public"
    assert cfg.community_write == "private"
