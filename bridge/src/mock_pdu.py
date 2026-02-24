# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Simulated PDU for testing without real hardware.

Generates realistic data for any CyberPower PDU model.
Outlet count and bank count are configurable for testing
different product family members.
"""

import logging
import math
import random
import time

from .pdu_model import (
    BANK_LOAD_STATE_MAP,
    OUTLET_CMD_OFF,
    OUTLET_CMD_ON,
    OUTLET_CMD_REBOOT,
    OUTLET_STATE_MAP,
    OUTLET_STATE_OFF,
    OUTLET_STATE_ON,
    BankData,
    DeviceIdentity,
    EnvironmentalData,
    OutletData,
    PDUData,
    SourceData,
)

logger = logging.getLogger(__name__)


class MockPDU:
    """Simulates a CyberPower PDU with realistic-looking data.

    Configurable outlet/bank counts to simulate different models in the
    CyberPower product family (PDU44001, PDU30SWEV17FNET, etc.).
    """

    def __init__(self, num_outlets: int = 10, num_banks: int = 2,
                 model: str = "PDU44001", device_name: str = "",
                 has_envirosensor: bool = False):
        self._num_outlets = num_outlets
        self._num_banks = num_banks
        self._outlet_states: dict[int, int] = {}
        self._outlet_names: dict[int, str] = {}
        self._start_time = time.time()

        for n in range(1, num_outlets + 1):
            self._outlet_states[n] = OUTLET_STATE_ON
            self._outlet_names[n] = f"Outlet {n}"

        self._reboot_until: dict[int, float] = {}
        self._failed_banks: set[int] = set()
        self._active_input = 1
        self._last_source_transfer = time.time()
        self._source_transfer_interval = 1800  # ~30 minutes between transfers
        self._cumulative_outlet_energy: dict[int, float] = {n: 0.0 for n in range(1, num_outlets + 1)}
        self._last_energy_update = time.time()
        self._has_envirosensor = has_envirosensor
        self._voltage_sensitivity = "Normal"
        self._coldstart_delay = 0
        self._coldstart_state = "allon"

        # Management state (for e2e testing without real hardware)
        self._thresholds = {"overload": 80, "nearover": 70, "lowload": 10}
        self._bank_thresholds: dict[int, dict] = {}
        self._outlet_config: dict[int, dict] = {}
        for n in range(1, num_outlets + 1):
            self._outlet_config[n] = {
                "name": f"Outlet {n}", "on_delay": 0, "off_delay": 0, "reboot_duration": 10,
            }
        self._network = {
            "ip": "192.168.1.100", "subnet": "255.255.255.0",
            "gateway": "192.168.1.1", "dhcp": False, "mac": "00:11:22:33:44:55",
        }
        self._preferred_source = "A"
        self._auto_transfer = True
        self._ats_sensitivity = "normal"
        self._transfer_voltage = {"upper": 138, "lower": 96}
        self._users = {
            "admin": {"password": "cyber", "access": "admin"},
            "device": {"password": "cyber", "access": "viewer"},
        }
        self._traps: list[dict] = [
            {"ip": "0.0.0.0", "community": "public", "severity": "warning", "enabled": False}
            for _ in range(4)
        ]
        self._smtp = {"server": "", "port": 25, "from_addr": "", "auth_user": ""}
        self._email: list[dict] = [{"to": "", "enabled": False} for _ in range(4)]
        self._syslog: list[dict] = [
            {"ip": "0.0.0.0", "facility": "user", "severity": "warning", "enabled": False}
            for _ in range(4)
        ]
        self._energywise = {"domain": "", "port": 43440, "secret": "", "enabled": False}
        self._eventlog: list[dict] = [
            {"index": 1, "date": "02/23/2026", "time": "10:00:00", "event": "System Started"},
        ]

        # Mock identity
        self._identity = DeviceIdentity(
            serial=f"MOCK{random.randint(100000, 999999):06d}",
            serial_numeric=f"{random.randint(100000, 999999)}",
            model=model,
            name=device_name or f"CyberPower {model} (Mock)",
            firmware_main="1.2",
            firmware_secondary="1.3.4",
            hardware_rev=12,
            max_current=12.0,
            outlet_count=num_outlets,
            phase_count=1,
            sys_description=f"CyberPower {model} Switched ATS PDU",
            sys_uptime=0,
            sys_contact="admin@example.com",
            sys_name=f"mock-{model.lower()}",
            sys_location="Rack 1, Row A",
        )

    @property
    def identity(self) -> DeviceIdentity:
        return self._identity

    @property
    def num_outlets(self) -> int:
        return self._num_outlets

    @property
    def num_banks(self) -> int:
        return self._num_banks

    async def poll(self) -> PDUData:
        """Return a snapshot of the simulated PDU state."""
        now = time.time()
        elapsed = now - self._start_time

        # Update uptime
        self._identity.sys_uptime = int(elapsed * 100)

        # Handle rebooting outlets
        for n, until in list(self._reboot_until.items()):
            if now >= until:
                self._outlet_states[n] = OUTLET_STATE_ON
                del self._reboot_until[n]
                logger.info("Mock: outlet %d reboot complete, now ON", n)

        # Simulate slow voltage drift (utility mains)
        base_voltage = 120.0 + 2.0 * math.sin(elapsed / 60.0)
        frequency = 60.0 + 0.02 * math.sin(elapsed / 30.0)

        # Per-bank voltage override for failure simulation
        bank_voltages = {}
        for idx in range(1, self._num_banks + 1):
            if idx in self._failed_banks:
                bank_voltages[idx] = 0.0
            else:
                bank_voltages[idx] = base_voltage + random.uniform(-0.3, 0.3)

        # ATS logic: if the active input fails, transfer to the other
        if self._active_input in self._failed_banks:
            for other in range(1, self._num_banks + 1):
                if other != self._active_input and other not in self._failed_banks:
                    self._active_input = other
                    logger.info("Mock: ATS transferred to input %d", other)
                    self._last_source_transfer = now
                    break

        # Simulate periodic ATS source transfers (every ~30 min, with some randomness)
        if (self._num_banks >= 2
                and not self._failed_banks
                and now - self._last_source_transfer >= self._source_transfer_interval):
            old_input = self._active_input
            self._active_input = 2 if self._active_input == 1 else 1
            self._last_source_transfer = now
            # Randomize next transfer interval (20-40 min)
            self._source_transfer_interval = random.uniform(1200, 2400)
            logger.info("Mock: Periodic ATS transfer %d -> %d", old_input, self._active_input)

        # Time delta for energy accumulation
        dt = now - self._last_energy_update
        self._last_energy_update = now

        # Outlets with realistic per-outlet power data
        outlets: dict[int, OutletData] = {}
        on_count = 0
        for n in range(1, self._num_outlets + 1):
            state_int = self._outlet_states[n]
            state_str = OUTLET_STATE_MAP.get(state_int, "unknown")

            bank_assignment = ((n - 1) % self._num_banks) + 1

            if state_int == OUTLET_STATE_ON:
                on_count += 1
                # Simulate realistic per-outlet load with some variation
                base_load = 0.1 + (n * 0.15)  # Different base load per outlet
                outlet_current = base_load + random.uniform(-0.02, 0.02)
                outlet_power = round(outlet_current * base_voltage, 1)
                # Accumulate energy (kWh)
                self._cumulative_outlet_energy[n] += outlet_power * dt / 3600.0 / 1000.0
                outlet_energy = round(self._cumulative_outlet_energy[n], 3)
            else:
                outlet_current = 0.0
                outlet_power = 0.0
                outlet_energy = round(self._cumulative_outlet_energy.get(n, 0.0), 3)

            outlets[n] = OutletData(
                number=n,
                name=self._outlet_names[n],
                state=state_str,
                current=round(outlet_current, 2),
                power=outlet_power,
                energy=outlet_energy,
                bank_assignment=bank_assignment,
                max_load=12.0,
            )

        # Bank-level metering
        total_current = on_count * 0.003 + random.uniform(0, 0.01)

        banks: dict[int, BankData] = {}
        for idx in range(1, self._num_banks + 1):
            voltage = bank_voltages[idx]
            is_active = (idx == self._active_input)

            if is_active and voltage > 10:
                current = round(total_current, 2)
                power = round(current * voltage, 1)
                apparent = round(current * voltage, 1)
                pf = 0.98 if current > 0.01 else 1.0
                load_state = "normal"
            else:
                current = 0.0
                power = 0.0
                apparent = 0.0
                pf = 1.0
                load_state = "normal" if voltage > 10 else "low"

            banks[idx] = BankData(
                number=idx,
                current=current,
                voltage=round(voltage, 1),
                power=power,
                apparent_power=apparent,
                power_factor=pf,
                load_state=load_state,
                energy=round(elapsed * power / 3600.0 / 1000.0, 3) if power > 0 else None,
                last_update=time.strftime("%m/%d/%Y %H:%M:%S") if power > 0 else "",
            )

        # Per-input source data
        source_a = SourceData(
            voltage=round(bank_voltages.get(1, 0.0), 1),
            frequency=round(frequency, 1) if 1 not in self._failed_banks else 0.0,
            voltage_status="underVoltage" if 1 in self._failed_banks else "normal",
        )
        source_b = None
        if self._num_banks >= 2:
            source_b = SourceData(
                voltage=round(bank_voltages.get(2, 0.0), 1),
                frequency=round(frequency, 1) if 2 not in self._failed_banks else 0.0,
                voltage_status="underVoltage" if 2 in self._failed_banks else "normal",
            )
        both_ok = not any(idx in self._failed_banks for idx in range(1, self._num_banks + 1))

        # Environment (optional)
        environment = None
        if self._has_envirosensor:
            environment = EnvironmentalData(
                temperature=round(22.0 + 2.0 * math.sin(elapsed / 120.0), 1),
                temperature_unit="C",
                humidity=round(45.0 + 10.0 * math.sin(elapsed / 180.0), 1),
                contacts={1: False, 2: False, 3: True, 4: False},
                sensor_present=True,
            )

        return PDUData(
            device_name=self._identity.name,
            outlet_count=self._num_outlets,
            phase_count=1,
            input_voltage=round(bank_voltages.get(self._active_input, 0.0), 1),
            input_frequency=round(frequency, 1),
            outlets=outlets,
            banks=banks,
            ats_preferred_source=1,
            ats_current_source=self._active_input,
            ats_auto_transfer=True,
            source_a=source_a,
            source_b=source_b,
            redundancy_ok=both_ok,
            voltage_sensitivity=self._voltage_sensitivity,
            coldstart_delay=self._coldstart_delay,
            coldstart_state=self._coldstart_state,
            environment=environment,
            identity=self._identity,
        )

    def simulate_input_failure(self, bank: int):
        """Simulate a power failure on the given bank."""
        if 1 <= bank <= self._num_banks:
            self._failed_banks.add(bank)
            logger.info("Mock: simulated power FAILURE on bank %d", bank)

    def simulate_input_restore(self, bank: int):
        """Restore power on the given bank."""
        self._failed_banks.discard(bank)
        logger.info("Mock: simulated power RESTORE on bank %d", bank)

    async def connect(self) -> None:
        """Mock connect — no-op."""
        pass

    async def command_outlet(self, outlet: int, command) -> bool:
        """Execute a command on an outlet. Returns True on success.

        Accepts both int (OUTLET_CMD_*) and str ('on', 'off', 'reboot')
        for PDUTransport compatibility.
        """
        if outlet < 1 or outlet > self._num_outlets:
            logger.error("Mock: invalid outlet %d", outlet)
            return False

        # Accept string commands (PDUTransport interface)
        if isinstance(command, str):
            str_map = {"on": OUTLET_CMD_ON, "off": OUTLET_CMD_OFF, "reboot": OUTLET_CMD_REBOOT}
            command = str_map.get(command.lower(), command)

        if command == OUTLET_CMD_ON:
            self._outlet_states[outlet] = OUTLET_STATE_ON
            logger.info("Mock: outlet %d -> ON", outlet)
        elif command == OUTLET_CMD_OFF:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            logger.info("Mock: outlet %d -> OFF", outlet)
        elif command == OUTLET_CMD_REBOOT:
            self._outlet_states[outlet] = OUTLET_STATE_OFF
            self._reboot_until[outlet] = time.time() + 5.0
            logger.info("Mock: outlet %d -> REBOOT (off for 5s)", outlet)
        else:
            logger.error("Mock: unknown command %d for outlet %d", command, outlet)
            return False

        return True

    async def get_identity(self) -> DeviceIdentity:
        """Return mock device identity."""
        return self._identity

    async def discover_num_banks(self) -> int:
        """Return mock bank count."""
        return self._num_banks

    async def query_startup_data(self, outlet_count: int) -> tuple[dict, dict]:
        """Return mock startup data (bank assignments, max loads)."""
        assignments = {}
        max_loads = {}
        for n in range(1, outlet_count + 1):
            assignments[n] = ((n - 1) % self._num_banks) + 1
            max_loads[n] = 12.0
        return assignments, max_loads

    async def set_device_field(self, field: str, value: str) -> bool:
        """Mock set field — always succeeds."""
        if field in ("device_name", "sys_name"):
            self._identity.name = value
        elif field == "sys_location":
            self._identity.sys_location = value
        return True

    def get_health(self) -> dict:
        """Return mock health."""
        return {
            "transport": "mock",
            "connected": True,
            "consecutive_failures": 0,
            "reachable": True,
        }

    @property
    def consecutive_failures(self) -> int:
        return 0

    def reset_health(self) -> None:
        pass

    # -- Management methods (mirror SerialTransport API for e2e testing) ------

    async def check_default_credentials(self) -> bool:
        """Return True if admin password is still 'cyber' (factory default)."""
        return self._users.get("admin", {}).get("password") == "cyber"

    async def change_password(self, account_type: str, new_password: str) -> bool:
        """Change a user account password."""
        if account_type == "admin" and "admin" in self._users:
            self._users["admin"]["password"] = new_password
            return True
        if account_type == "viewer" and "device" in self._users:
            self._users["device"]["password"] = new_password
            return True
        return False

    async def get_network_config(self) -> dict:
        # Match parse_netcfg_show format: {ip, subnet, gateway, dhcp_enabled, mac_address}
        return {
            "ip": self._network["ip"],
            "subnet": self._network["subnet"],
            "gateway": self._network["gateway"],
            "dhcp_enabled": self._network.get("dhcp", False),
            "mac_address": self._network.get("mac", "00:11:22:33:44:55"),
        }

    async def set_network_config(self, ip: str | None = None,
                                 subnet: str | None = None,
                                 gateway: str | None = None,
                                 dhcp: bool | None = None) -> bool:
        if ip is not None:
            self._network["ip"] = ip
        if subnet is not None:
            self._network["subnet"] = subnet
        if gateway is not None:
            self._network["gateway"] = gateway
        if dhcp is not None:
            self._network["dhcp"] = dhcp
        return True

    async def get_device_thresholds(self) -> dict:
        # Match parse_devcfg_show format
        return {
            "overload_threshold": self._thresholds["overload"],
            "near_overload_threshold": self._thresholds["nearover"],
            "low_load_threshold": self._thresholds["lowload"],
        }

    async def set_device_threshold(self, threshold_type: str, value: float) -> bool:
        if threshold_type in ("overload", "nearover", "lowload"):
            self._thresholds[threshold_type] = int(value)
            return True
        return False

    async def get_bank_thresholds(self) -> dict[int, dict]:
        # Match parse_bankcfg_show format: {bank: {overload, near_overload, low_load}}
        result: dict[int, dict] = {}
        for idx in range(1, self._num_banks + 1):
            bt = self._bank_thresholds.get(idx, self._thresholds)
            result[idx] = {
                "overload": bt.get("overload", 80),
                "near_overload": bt.get("nearover", 70),
                "low_load": bt.get("lowload", 10),
            }
        return result

    async def set_bank_threshold(self, bank: int, threshold_type: str,
                                 value: float) -> bool:
        if threshold_type not in ("overload", "nearover", "lowload"):
            return False
        if bank not in self._bank_thresholds:
            self._bank_thresholds[bank] = dict(self._thresholds)
        self._bank_thresholds[bank][threshold_type] = int(value)
        return True

    async def get_outlet_config(self) -> dict[int, dict]:
        return {k: dict(v) for k, v in self._outlet_config.items()}

    async def configure_outlet(self, outlet: int, name: str | None = None,
                               on_delay: int | None = None,
                               off_delay: int | None = None,
                               reboot_duration: int | None = None) -> bool:
        if outlet not in self._outlet_config:
            return False
        cfg = self._outlet_config[outlet]
        if name is not None:
            cfg["name"] = name
            self._outlet_names[outlet] = name
        if on_delay is not None:
            cfg["on_delay"] = on_delay
        if off_delay is not None:
            cfg["off_delay"] = off_delay
        if reboot_duration is not None:
            cfg["reboot_duration"] = reboot_duration
        return True

    async def get_event_log(self) -> list[dict]:
        # Match parse_eventlog_show format: [{timestamp, event_type, description}]
        return [
            {
                "timestamp": f"{e.get('date', '')} {e.get('time', '')}",
                "event_type": "info",
                "description": e.get("event", ""),
            }
            for e in self._eventlog
        ]

    async def get_source_config(self) -> dict:
        # Match parse_srccfg_show format
        return {
            "preferred_source": self._preferred_source,
            "voltage_sensitivity": self._ats_sensitivity.capitalize(),
            "transfer_voltage": self._transfer_voltage["lower"],
            "voltage_upper_limit": self._transfer_voltage["upper"],
            "voltage_lower_limit": self._transfer_voltage["lower"],
        }

    async def get_device_config(self) -> dict:
        return {
            "overload": self._thresholds["overload"],
            "nearover": self._thresholds["nearover"],
            "lowload": self._thresholds["lowload"],
            "coldstart_delay": self._coldstart_delay,
            "coldstart_state": self._coldstart_state,
        }

    async def set_preferred_source(self, source: str) -> bool:
        source = source.upper()
        if source in ("A", "B"):
            self._preferred_source = source
            return True
        return False

    async def set_voltage_sensitivity(self, sensitivity: str) -> bool:
        sensitivity = sensitivity.lower()
        if sensitivity in ("normal", "high", "low"):
            self._ats_sensitivity = sensitivity
            return True
        return False

    async def set_transfer_voltage(self, upper: float | None = None,
                                   lower: float | None = None) -> bool:
        if upper is not None:
            self._transfer_voltage["upper"] = int(upper)
        if lower is not None:
            self._transfer_voltage["lower"] = int(lower)
        return True

    async def set_coldstart_delay(self, seconds: int) -> bool:
        self._coldstart_delay = seconds
        return True

    async def set_coldstart_state(self, state: str) -> bool:
        state = state.lower()
        if state in ("allon", "prevstate"):
            self._coldstart_state = state
            return True
        return False

    async def set_auto_transfer(self, enabled: bool) -> bool:
        self._auto_transfer = enabled
        return True

    async def get_user_config(self) -> dict:
        return {
            name: {"access": info["access"]}
            for name, info in self._users.items()
        }

    async def get_trap_config(self) -> list[dict]:
        return [dict(t) for t in self._traps]

    async def set_trap_receiver(self, index: int, ip: str | None = None,
                                community: str | None = None,
                                severity: str | None = None,
                                enabled: bool | None = None) -> bool:
        if 0 <= index < len(self._traps):
            if ip is not None:
                self._traps[index]["ip"] = ip
            if community is not None:
                self._traps[index]["community"] = community
            if severity is not None:
                self._traps[index]["severity"] = severity
            if enabled is not None:
                self._traps[index]["enabled"] = enabled
            return True
        return False

    async def get_smtp_config(self) -> dict:
        return dict(self._smtp)

    async def set_smtp_config(self, server: str | None = None,
                              port: int | None = None,
                              from_addr: str | None = None,
                              auth_user: str | None = None,
                              auth_pass: str | None = None) -> bool:
        if server is not None:
            self._smtp["server"] = server
        if port is not None:
            self._smtp["port"] = port
        if from_addr is not None:
            self._smtp["from_addr"] = from_addr
        if auth_user is not None:
            self._smtp["auth_user"] = auth_user
        return True

    async def get_email_config(self) -> list[dict]:
        return [dict(e) for e in self._email]

    async def set_email_recipient(self, index: int, to: str | None = None,
                                  enabled: bool | None = None) -> bool:
        if 0 <= index < len(self._email):
            if to is not None:
                self._email[index]["to"] = to
            if enabled is not None:
                self._email[index]["enabled"] = enabled
            return True
        return False

    async def get_syslog_config(self) -> list[dict]:
        return [dict(s) for s in self._syslog]

    async def set_syslog_server(self, index: int, ip: str | None = None,
                                facility: str | None = None,
                                severity: str | None = None,
                                enabled: bool | None = None) -> bool:
        if 0 <= index < len(self._syslog):
            if ip is not None:
                self._syslog[index]["ip"] = ip
            if facility is not None:
                self._syslog[index]["facility"] = facility
            if severity is not None:
                self._syslog[index]["severity"] = severity
            if enabled is not None:
                self._syslog[index]["enabled"] = enabled
            return True
        return False

    async def get_energywise_config(self) -> dict:
        return dict(self._energywise)

    async def set_energywise_config(self, domain: str | None = None,
                                    port: int | None = None,
                                    secret: str | None = None,
                                    enabled: bool | None = None) -> bool:
        if domain is not None:
            self._energywise["domain"] = domain
        if port is not None:
            self._energywise["port"] = port
        if secret is not None:
            self._energywise["secret"] = secret
        if enabled is not None:
            self._energywise["enabled"] = enabled
        return True

    def close(self) -> None:
        pass
