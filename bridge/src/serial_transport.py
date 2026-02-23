# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 GPL-3.0 License
# https://github.com/mvalancy/CyberPower-PDU

"""Serial transport — wraps SerialClient + parsers into PDUTransport.

Maps the transport interface to sequential CLI commands:
  poll()          -> devsta show + oltsta show + srccfg show
  get_identity()  -> sys show
  command_outlet() -> oltctrl index N act <cmd>
"""

import logging

from .pdu_config import PDUConfig
from .pdu_model import DeviceIdentity, PDUData
from .serial_client import SerialClient
from .serial_parser import (
    build_pdu_data,
    parse_bankcfg_show,
    parse_devcfg_show,
    parse_devsta_show,
    parse_emailcfg_show,
    parse_energywise_show,
    parse_eventlog_show,
    parse_netcfg_show,
    parse_oltcfg_show,
    parse_oltsta_show,
    parse_smtpcfg_show,
    parse_srccfg_show,
    parse_sys_show,
    parse_syslogcfg_show,
    parse_trapcfg_show,
    parse_usercfg_show,
)

logger = logging.getLogger(__name__)


class SerialTransport:
    """PDUTransport implementation backed by serial console CLI."""

    def __init__(self, serial_client: SerialClient, pdu_cfg: PDUConfig):
        self._serial = serial_client
        self._pdu_cfg = pdu_cfg
        self._identity: DeviceIdentity | None = None
        self._num_banks = pdu_cfg.num_banks

    @property
    def serial_client(self) -> SerialClient:
        """Direct access to underlying SerialClient."""
        return self._serial

    async def connect(self) -> None:
        """Open serial port and login."""
        await self._serial.connect()

    async def poll(self) -> PDUData:
        """Poll via CLI commands and return a PDUData snapshot."""
        devsta_text = await self._serial.execute("devsta show")
        oltsta_text = await self._serial.execute("oltsta show")
        srccfg_text = await self._serial.execute("srccfg show")
        devcfg_text = await self._serial.execute("devcfg show")

        devsta = parse_devsta_show(devsta_text)
        outlets = parse_oltsta_show(oltsta_text)
        srccfg = parse_srccfg_show(srccfg_text)
        devcfg = parse_devcfg_show(devcfg_text)

        data = build_pdu_data(devsta, outlets, srccfg, self._identity, devcfg=devcfg)
        return data

    async def get_identity(self) -> DeviceIdentity:
        """Query identity via 'sys show'."""
        text = await self._serial.execute("sys show")
        identity = parse_sys_show(text)

        # Also get outlet count from oltsta show
        oltsta_text = await self._serial.execute("oltsta show")
        outlets = parse_oltsta_show(oltsta_text)
        identity.outlet_count = len(outlets)

        self._identity = identity
        return identity

    async def discover_num_banks(self) -> int:
        """Detect bank count from devsta show output."""
        text = await self._serial.execute("devsta show")
        devsta = parse_devsta_show(text)
        bank_currents = devsta.get("bank_currents", {})
        if bank_currents:
            count = len(bank_currents)
            self._num_banks = count
            return count

        # Check if we have dual source voltages
        if (devsta.get("source_a_voltage") is not None
                and devsta.get("source_b_voltage") is not None):
            self._num_banks = 2
            return 2

        return self._pdu_cfg.num_banks

    async def query_startup_data(self, outlet_count: int) -> tuple[dict, dict]:
        """Serial CLI doesn't provide per-outlet bank assignments.

        Returns empty dicts — bank assignment is inferred from
        the outlet index (alternating between banks).
        """
        return {}, {}

    async def command_outlet(self, outlet: int, command: str) -> bool:
        """Execute outlet command via CLI.

        Maps: on/off/reboot -> 'oltctrl index N act <cmd>'
              delayon/delayoff -> 'oltctrl index N act <cmd>'
              cancel -> 'oltctrl index N act cancel'
        """
        valid_commands = ("on", "off", "reboot", "delayon", "delayoff", "cancel")
        if command not in valid_commands:
            logger.error("Serial: unknown command '%s'", command)
            return False

        try:
            cmd = f"oltctrl index {outlet} act {command}"
            response = await self._serial.execute(cmd)
            logger.info("Serial: outlet %d %s -> response: %s",
                        outlet, command, response[:100])
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: outlet command failed: %s", e)
            return False

    async def set_device_field(self, field: str, value: str) -> bool:
        """Set device field via CLI (limited support)."""
        # Map field names to CLI commands
        cmd_map = {
            "device_name": f"syscfg set name {value}",
            "sys_name": f"syscfg set name {value}",
            "sys_location": f"syscfg set location {value}",
            "sys_contact": f"syscfg set contact {value}",
        }
        cmd = cmd_map.get(field)
        if not cmd:
            logger.error("Serial: unknown field '%s'", field)
            return False

        try:
            response = await self._serial.execute(cmd)
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set field failed: %s", e)
            return False

    # -- Management methods (serial-specific, not in PDUTransport) ----------

    async def configure_outlet(self, outlet: int, name: str | None = None,
                               on_delay: int | None = None,
                               off_delay: int | None = None,
                               reboot_duration: int | None = None) -> bool:
        """Configure outlet name and timing via 'oltcfg set'."""
        try:
            if name is not None:
                cmd = f"oltcfg set {outlet} name {name}"
                response = await self._serial.execute(cmd)
                if "error" in response.lower() or "fail" in response.lower():
                    return False

            if on_delay is not None:
                cmd = f"oltcfg set {outlet} ondelay {on_delay}"
                response = await self._serial.execute(cmd)
                if "error" in response.lower() or "fail" in response.lower():
                    return False

            if off_delay is not None:
                cmd = f"oltcfg set {outlet} offdelay {off_delay}"
                response = await self._serial.execute(cmd)
                if "error" in response.lower() or "fail" in response.lower():
                    return False

            if reboot_duration is not None:
                cmd = f"oltcfg set {outlet} rebootdur {reboot_duration}"
                response = await self._serial.execute(cmd)
                if "error" in response.lower() or "fail" in response.lower():
                    return False

            return True
        except Exception as e:
            logger.error("Serial: configure_outlet failed: %s", e)
            return False

    async def set_device_threshold(self, threshold_type: str, value: float) -> bool:
        """Set device-level load threshold via 'devcfg' command.

        threshold_type: "overload", "nearover", or "lowload"
        """
        valid = ("overload", "nearover", "lowload")
        if threshold_type not in valid:
            logger.error("Serial: invalid threshold type '%s'", threshold_type)
            return False
        try:
            cmd = f"devcfg {threshold_type} {int(value)}"
            response = await self._serial.execute(cmd)
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_device_threshold failed: %s", e)
            return False

    async def set_bank_threshold(self, bank: int, threshold_type: str,
                                 value: float) -> bool:
        """Set per-bank load threshold via 'bankcfg' command.

        threshold_type: "overload", "nearover", or "lowload"
        """
        valid = ("overload", "nearover", "lowload")
        if threshold_type not in valid:
            logger.error("Serial: invalid threshold type '%s'", threshold_type)
            return False
        try:
            cmd = f"bankcfg index b{bank} {threshold_type} {int(value)}"
            response = await self._serial.execute(cmd)
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_bank_threshold failed: %s", e)
            return False

    # -- ATS configuration methods ------------------------------------------

    async def set_preferred_source(self, source: str) -> bool:
        """Set preferred ATS source via 'srccfg set preferred A/B'."""
        source = source.upper()
        if source not in ("A", "B"):
            logger.error("Serial: invalid source '%s' (must be A or B)", source)
            return False
        try:
            response = await self._serial.execute(f"srccfg set preferred {source}")
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_preferred_source failed: %s", e)
            return False

    async def set_voltage_sensitivity(self, sensitivity: str) -> bool:
        """Set voltage sensitivity via 'srccfg set sensitivity normal/high/low'."""
        sensitivity = sensitivity.lower()
        if sensitivity not in ("normal", "high", "low"):
            logger.error("Serial: invalid sensitivity '%s'", sensitivity)
            return False
        try:
            response = await self._serial.execute(f"srccfg set sensitivity {sensitivity}")
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_voltage_sensitivity failed: %s", e)
            return False

    async def set_transfer_voltage(self, upper: float | None = None,
                                   lower: float | None = None) -> bool:
        """Set transfer voltage limits via 'srccfg set uppervoltage/lowervoltage'."""
        try:
            if upper is not None:
                response = await self._serial.execute(f"srccfg set uppervoltage {int(upper)}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            if lower is not None:
                response = await self._serial.execute(f"srccfg set lowervoltage {int(lower)}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            return True
        except Exception as e:
            logger.error("Serial: set_transfer_voltage failed: %s", e)
            return False

    async def get_source_config(self) -> dict:
        """Query full source config via 'srccfg show'."""
        text = await self._serial.execute("srccfg show")
        return parse_srccfg_show(text)

    async def set_coldstart_delay(self, seconds: int) -> bool:
        """Set coldstart delay via 'devcfg coldstadly <N>'."""
        try:
            response = await self._serial.execute(f"devcfg coldstadly {int(seconds)}")
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_coldstart_delay failed: %s", e)
            return False

    async def set_coldstart_state(self, state: str) -> bool:
        """Set coldstart state via 'devcfg coldstastate allon/prevstate'."""
        state = state.lower()
        if state not in ("allon", "prevstate"):
            logger.error("Serial: invalid coldstart state '%s'", state)
            return False
        try:
            response = await self._serial.execute(f"devcfg coldstastate {state}")
            if "error" in response.lower() or "fail" in response.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: set_coldstart_state failed: %s", e)
            return False

    async def get_device_config(self) -> dict:
        """Query device config (thresholds + coldstart) via 'devcfg show'."""
        text = await self._serial.execute("devcfg show")
        return parse_devcfg_show(text)

    # -- Network config write -----------------------------------------------

    async def set_network_config(self, ip: str | None = None,
                                 subnet: str | None = None,
                                 gateway: str | None = None,
                                 dhcp: bool | None = None) -> bool:
        """Set PDU network config via sequential 'netcfg set' commands."""
        try:
            if dhcp is not None:
                val = "enabled" if dhcp else "disabled"
                response = await self._serial.execute(f"netcfg set dhcp {val}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            if ip is not None:
                response = await self._serial.execute(f"netcfg set ip {ip}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            if subnet is not None:
                response = await self._serial.execute(f"netcfg set subnet {subnet}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            if gateway is not None:
                response = await self._serial.execute(f"netcfg set gateway {gateway}")
                if "error" in response.lower() or "fail" in response.lower():
                    return False
            return True
        except Exception as e:
            logger.error("Serial: set_network_config failed: %s", e)
            return False

    # -- User management ----------------------------------------------------

    async def get_user_config(self) -> dict:
        """Query user accounts via 'usercfg show'."""
        try:
            text = await self._serial.execute("usercfg show")
            return parse_usercfg_show(text)
        except Exception as e:
            logger.error("Serial: get_user_config failed: %s", e)
            return {"error": str(e)}

    # -- Notification configuration -----------------------------------------

    async def get_trap_config(self) -> list[dict]:
        """Query SNMP trap receivers via 'trapcfg show'."""
        try:
            text = await self._serial.execute("trapcfg show")
            return parse_trapcfg_show(text)
        except Exception as e:
            logger.error("Serial: get_trap_config failed: %s", e)
            return []

    async def set_trap_receiver(self, index: int, ip: str | None = None,
                                community: str | None = None,
                                severity: str | None = None,
                                enabled: bool | None = None) -> bool:
        """Configure a trap receiver via 'trapcfg set'."""
        try:
            if ip is not None:
                await self._serial.execute(f"trapcfg set {index} ip {ip}")
            if community is not None:
                await self._serial.execute(f"trapcfg set {index} community {community}")
            if severity is not None:
                await self._serial.execute(f"trapcfg set {index} severity {severity}")
            if enabled is not None:
                val = "enabled" if enabled else "disabled"
                await self._serial.execute(f"trapcfg set {index} status {val}")
            return True
        except Exception as e:
            logger.error("Serial: set_trap_receiver failed: %s", e)
            return False

    async def get_smtp_config(self) -> dict:
        """Query SMTP configuration via 'smtpcfg show'."""
        try:
            text = await self._serial.execute("smtpcfg show")
            return parse_smtpcfg_show(text)
        except Exception as e:
            logger.error("Serial: get_smtp_config failed: %s", e)
            return {}

    async def set_smtp_config(self, server: str | None = None,
                              port: int | None = None,
                              from_addr: str | None = None,
                              auth_user: str | None = None,
                              auth_pass: str | None = None) -> bool:
        """Configure SMTP settings via 'smtpcfg set'."""
        try:
            if server is not None:
                await self._serial.execute(f"smtpcfg set server {server}")
            if port is not None:
                await self._serial.execute(f"smtpcfg set port {int(port)}")
            if from_addr is not None:
                await self._serial.execute(f"smtpcfg set from {from_addr}")
            if auth_user is not None:
                await self._serial.execute(f"smtpcfg set user {auth_user}")
            if auth_pass is not None:
                await self._serial.execute(f"smtpcfg set password {auth_pass}")
            return True
        except Exception as e:
            logger.error("Serial: set_smtp_config failed: %s", e)
            return False

    async def get_email_config(self) -> list[dict]:
        """Query email recipients via 'emailcfg show'."""
        try:
            text = await self._serial.execute("emailcfg show")
            return parse_emailcfg_show(text)
        except Exception as e:
            logger.error("Serial: get_email_config failed: %s", e)
            return []

    async def set_email_recipient(self, index: int, to: str | None = None,
                                  enabled: bool | None = None) -> bool:
        """Configure an email recipient via 'emailcfg set'."""
        try:
            if to is not None:
                await self._serial.execute(f"emailcfg set {index} to {to}")
            if enabled is not None:
                val = "enabled" if enabled else "disabled"
                await self._serial.execute(f"emailcfg set {index} status {val}")
            return True
        except Exception as e:
            logger.error("Serial: set_email_recipient failed: %s", e)
            return False

    async def get_syslog_config(self) -> list[dict]:
        """Query syslog servers via 'syslog show'."""
        try:
            text = await self._serial.execute("syslog show")
            return parse_syslogcfg_show(text)
        except Exception as e:
            logger.error("Serial: get_syslog_config failed: %s", e)
            return []

    async def set_syslog_server(self, index: int, ip: str | None = None,
                                facility: str | None = None,
                                severity: str | None = None,
                                enabled: bool | None = None) -> bool:
        """Configure a syslog server via 'syslog set'."""
        try:
            if ip is not None:
                await self._serial.execute(f"syslog set {index} ip {ip}")
            if facility is not None:
                await self._serial.execute(f"syslog set {index} facility {facility}")
            if severity is not None:
                await self._serial.execute(f"syslog set {index} severity {severity}")
            if enabled is not None:
                val = "enabled" if enabled else "disabled"
                await self._serial.execute(f"syslog set {index} status {val}")
            return True
        except Exception as e:
            logger.error("Serial: set_syslog_server failed: %s", e)
            return False

    # -- EnergyWise configuration -------------------------------------------

    async def get_energywise_config(self) -> dict:
        """Query EnergyWise configuration via 'energywise show'."""
        try:
            text = await self._serial.execute("energywise show")
            return parse_energywise_show(text)
        except Exception as e:
            logger.error("Serial: get_energywise_config failed: %s", e)
            return {}

    async def set_energywise_config(self, domain: str | None = None,
                                    port: int | None = None,
                                    secret: str | None = None,
                                    enabled: bool | None = None) -> bool:
        """Configure EnergyWise settings."""
        try:
            if domain is not None:
                await self._serial.execute(f"energywise set domain {domain}")
            if port is not None:
                await self._serial.execute(f"energywise set port {int(port)}")
            if secret is not None:
                await self._serial.execute(f"energywise set secret {secret}")
            if enabled is not None:
                val = "enabled" if enabled else "disabled"
                await self._serial.execute(f"energywise set status {val}")
            return True
        except Exception as e:
            logger.error("Serial: set_energywise_config failed: %s", e)
            return False

    async def get_outlet_config(self) -> dict[int, dict]:
        """Query outlet configuration via 'oltcfg show'."""
        text = await self._serial.execute("oltcfg show")
        return parse_oltcfg_show(text)

    async def get_device_thresholds(self) -> dict:
        """Query device-level thresholds via 'devcfg show'."""
        text = await self._serial.execute("devcfg show")
        return parse_devcfg_show(text)

    async def get_bank_thresholds(self) -> dict[int, dict]:
        """Query per-bank thresholds via 'bankcfg show'."""
        text = await self._serial.execute("bankcfg show")
        return parse_bankcfg_show(text)

    async def get_network_config(self) -> dict:
        """Query network configuration via 'netcfg show'."""
        text = await self._serial.execute("netcfg show")
        return parse_netcfg_show(text)

    async def get_event_log(self) -> list[dict]:
        """Query PDU event log via 'eventlog show'."""
        text = await self._serial.execute("eventlog show")
        return parse_eventlog_show(text)

    async def check_default_credentials(self) -> bool:
        """Check if default cyber/cyber credentials still work.

        Returns True if default creds work (security risk).
        """
        from .serial_client import SerialClient
        test_client = SerialClient(
            port=self._serial.port,
            username="cyber",
            password="cyber",
            baud=9600,
            timeout=5.0,
        )
        try:
            await test_client.connect()
            return True  # Default creds work — security risk
        except ConnectionError:
            return False  # Default creds rejected — good
        except Exception:
            return False
        finally:
            test_client.close()

    async def change_password(self, account_type: str,
                              new_password: str) -> bool:
        """Change PDU admin or viewer password via 'usercfg' interactive command.

        account_type: "admin" or "viewer"
        """
        if account_type not in ("admin", "viewer"):
            logger.error("Serial: invalid account_type '%s'", account_type)
            return False
        try:
            result = await self._serial.execute_interactive([
                (f"usercfg {account_type} password", "New Password:"),    # CLI cmd → \n
                (new_password, "Confirm Password:", " "),                  # password → SPACE
                (new_password, "CyberPower >", " "),                      # confirm → SPACE
            ])
            if "error" in result.lower() or "fail" in result.lower():
                return False
            return True
        except Exception as e:
            logger.error("Serial: change_password failed: %s", e)
            return False

    def get_health(self) -> dict:
        """Return serial health metrics."""
        health = self._serial.get_health()
        health["transport"] = "serial"
        return health

    @property
    def consecutive_failures(self) -> int:
        return self._serial.consecutive_failures

    def reset_health(self) -> None:
        self._serial.reset_health()

    def close(self) -> None:
        self._serial.close()
