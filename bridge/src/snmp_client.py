# CyberPower PDU Bridge
# Created by Matthew Valancy, Valpatel Software LLC
# Copyright 2026 MIT License
# https://github.com/mvalancy/CyberPower-PDU

"""SNMP GET/SET wrapper for CyberPower PDUs with health tracking.

Works with any CyberPower PDU model — device capabilities are
auto-detected, not hardcoded.
"""

import asyncio
import logging
import time
from typing import Any

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    Integer32,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    setCmd,
)

from .config import Config
from .pdu_config import PDUConfig
from .pdu_model import (
    DeviceIdentity,
    OID_DEVICE_NAME,
    OID_FW_MAIN,
    OID_FW_SECONDARY,
    OID_HW_REV,
    OID_MAX_CURRENT,
    OID_MODEL,
    OID_OUTLET_COUNT,
    OID_PHASE_COUNT,
    OID_SERIAL_HW,
    OID_SERIAL_NUM,
    OID_SYS_CONTACT,
    OID_SYS_DESCR,
    OID_SYS_LOCATION,
    OID_SYS_NAME,
    OID_SYS_UPTIME,
)

logger = logging.getLogger(__name__)


class SNMPClient:
    """SNMP client that can be constructed from either Config or PDUConfig."""

    def __init__(self, config: Config | None = None,
                 pdu_config: PDUConfig | None = None,
                 global_config: Config | None = None):
        """Initialize from either a Config (legacy) or PDUConfig + global Config.

        Backward compatible: SNMPClient(config) still works.
        New multi-PDU path: SNMPClient(pdu_config=pdu, global_config=cfg)
        """
        if pdu_config is not None:
            self._host = pdu_config.host
            self._port = pdu_config.snmp_port
            read_community = pdu_config.community_read
            write_community = pdu_config.community_write
            timeout = global_config.snmp_timeout if global_config else 2.0
            retries = global_config.snmp_retries if global_config else 1
        elif config is not None:
            self._host = config.pdu_host
            self._port = config.pdu_snmp_port
            read_community = config.pdu_community_read
            write_community = config.pdu_community_write
            timeout = config.snmp_timeout
            retries = config.snmp_retries
        else:
            raise ValueError("SNMPClient requires either config or pdu_config")

        # Keep config reference for backward compat (health reporting)
        self.config = config

        self.engine = SnmpEngine()
        self._read_community = CommunityData(read_community)
        self._write_community = CommunityData(write_community)
        self._target = UdpTransportTarget(
            (self._host, self._port),
            timeout=timeout,
            retries=retries,
        )

        # Health tracking
        self._total_gets = 0
        self._failed_gets = 0
        self._total_sets = 0
        self._failed_sets = 0
        self._consecutive_failures = 0
        self._last_success_time: float | None = None
        self._last_error_time: float | None = None
        self._last_error_msg: str | None = None
        self._last_poll_duration: float | None = None

    def get_health(self) -> dict:
        """Return SNMP connection health metrics."""
        return {
            "target": f"{self._host}:{self._port}",
            "total_gets": self._total_gets,
            "failed_gets": self._failed_gets,
            "total_sets": self._total_sets,
            "failed_sets": self._failed_sets,
            "consecutive_failures": self._consecutive_failures,
            "last_success": self._last_success_time,
            "last_error": self._last_error_time,
            "last_error_msg": self._last_error_msg,
            "last_poll_duration_ms": (
                round(self._last_poll_duration * 1000, 1)
                if self._last_poll_duration is not None else None
            ),
            "reachable": self._consecutive_failures < 10,
        }

    async def get(self, oid: str) -> Any | None:
        """SNMP GET a single OID. Returns the value or None on error."""
        self._total_gets += 1
        try:
            error_indication, error_status, error_index, var_binds = await getCmd(
                self.engine,
                self._read_community,
                self._target,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )

            if error_indication:
                self._record_failure(f"GET {oid}: {error_indication}")
                return None
            if error_status:
                self._record_failure(
                    f"GET {oid}: {error_status.prettyPrint()} at "
                    f"{var_binds[int(error_index) - 1][0] if error_index else '?'}"
                )
                return None

            _oid, value = var_binds[0]
            self._record_success()
            return value

        except Exception as e:
            self._record_failure(f"GET {oid}: {e}")
            return None

    async def get_many(self, oids: list[str], batch_size: int = 10) -> dict[str, Any]:
        """SNMP GET multiple OIDs in parallel batches."""
        start = time.monotonic()
        results = {}
        for i in range(0, len(oids), batch_size):
            batch = oids[i:i + batch_size]
            values = await asyncio.gather(
                *(self.get(oid) for oid in batch),
                return_exceptions=True,
            )
            for oid, value in zip(batch, values):
                if isinstance(value, Exception):
                    logger.error("SNMP GET %s raised: %s", oid, value)
                elif value is not None:
                    results[oid] = value
        self._last_poll_duration = time.monotonic() - start
        return results

    async def set(self, oid: str, value: int) -> bool:
        """SNMP SET an integer value. Returns True on success."""
        self._total_sets += 1
        try:
            error_indication, error_status, error_index, var_binds = await setCmd(
                self.engine,
                self._write_community,
                self._target,
                ContextData(),
                ObjectType(ObjectIdentity(oid), Integer32(value)),
            )

            if error_indication:
                self._failed_sets += 1
                self._record_failure(f"SET {oid}={value}: {error_indication}")
                return False
            if error_status:
                self._failed_sets += 1
                self._record_failure(
                    f"SET {oid}={value}: {error_status.prettyPrint()} at "
                    f"{var_binds[int(error_index) - 1][0] if error_index else '?'}"
                )
                return False

            self._record_success()
            return True

        except Exception as e:
            self._failed_sets += 1
            self._record_failure(f"SET {oid}={value}: {e}")
            return False

    async def get_identity(self) -> DeviceIdentity:
        """Fetch all device identity OIDs in one batch.

        Called once at startup to discover what kind of PDU this is.
        Works across the CyberPower product family — unknown OIDs
        return gracefully as empty/zero.
        """
        oids = [
            OID_DEVICE_NAME, OID_FW_MAIN, OID_FW_SECONDARY,
            OID_SERIAL_NUM, OID_MODEL, OID_SERIAL_HW, OID_HW_REV,
            OID_OUTLET_COUNT, OID_PHASE_COUNT, OID_MAX_CURRENT,
            OID_SYS_DESCR, OID_SYS_UPTIME, OID_SYS_CONTACT,
            OID_SYS_NAME, OID_SYS_LOCATION,
        ]
        values = await self.get_many(oids)

        def s(oid: str) -> str:
            v = values.get(oid)
            return str(v).strip() if v is not None else ""

        def i(oid: str) -> int:
            v = values.get(oid)
            if v is None:
                return 0
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        max_current_raw = i(OID_MAX_CURRENT)

        return DeviceIdentity(
            serial=s(OID_SERIAL_HW),
            serial_numeric=s(OID_SERIAL_NUM),
            model=s(OID_MODEL),
            name=s(OID_DEVICE_NAME),
            firmware_main=s(OID_FW_MAIN),
            firmware_secondary=s(OID_FW_SECONDARY),
            hardware_rev=i(OID_HW_REV),
            max_current=max_current_raw / 10.0 if max_current_raw else 0.0,
            outlet_count=i(OID_OUTLET_COUNT),
            phase_count=i(OID_PHASE_COUNT) or 1,
            sys_description=s(OID_SYS_DESCR),
            sys_uptime=i(OID_SYS_UPTIME),
            sys_contact=s(OID_SYS_CONTACT),
            sys_name=s(OID_SYS_NAME),
            sys_location=s(OID_SYS_LOCATION),
        )

    def _record_success(self):
        self._consecutive_failures = 0
        self._last_success_time = time.time()

    def _record_failure(self, msg: str):
        self._failed_gets += 1
        self._consecutive_failures += 1
        self._last_error_time = time.time()
        self._last_error_msg = msg
        # Log at different levels based on consecutive failures
        if self._consecutive_failures == 1:
            logger.warning("SNMP: %s", msg)
        elif self._consecutive_failures <= 5:
            logger.error("SNMP: %s (failure %d)", msg, self._consecutive_failures)
        elif self._consecutive_failures % 30 == 0:
            logger.error(
                "SNMP: PDU unreachable for %d consecutive failures — %s",
                self._consecutive_failures, msg,
            )

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count (read-only)."""
        return self._consecutive_failures

    def update_target(self, host: str, port: int | None = None):
        """Replace the SNMP transport target (e.g. after DHCP IP change).

        Creates a fresh UdpTransportTarget; the existing SnmpEngine is reused.
        """
        self._host = host
        if port is not None:
            self._port = port
        self._target = UdpTransportTarget(
            (self._host, self._port),
            timeout=self._target._timeout if hasattr(self._target, '_timeout') else 2.0,
            retries=self._target._retries if hasattr(self._target, '_retries') else 1,
        )
        logger.info("SNMP target updated to %s:%d", self._host, self._port)

    def reset_health(self):
        """Zero out failure counters after successful recovery."""
        self._consecutive_failures = 0
        self._failed_gets = 0
        self._failed_sets = 0
        self._last_error_msg = None
        self._last_error_time = None

    def close(self):
        try:
            self.engine.close_dispatcher()
        except Exception:
            logger.debug("Error closing SNMP engine", exc_info=True)
