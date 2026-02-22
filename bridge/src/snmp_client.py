"""SNMP GET/SET wrapper for CyberPower PDU."""

import logging
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

logger = logging.getLogger(__name__)


class SNMPClient:
    def __init__(self, config: Config):
        self.config = config
        self.engine = SnmpEngine()
        self._read_community = CommunityData(config.pdu_community_read)
        self._write_community = CommunityData(config.pdu_community_write)
        self._target = UdpTransportTarget(
            (config.pdu_host, config.pdu_snmp_port),
            timeout=2.0,
            retries=1,
        )

    async def get(self, oid: str) -> Any | None:
        """SNMP GET a single OID. Returns the value or None on error."""
        try:
            error_indication, error_status, error_index, var_binds = await getCmd(
                self.engine,
                self._read_community,
                self._target,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )

            if error_indication:
                logger.error("SNMP GET %s: %s", oid, error_indication)
                return None
            if error_status:
                logger.error(
                    "SNMP GET %s: %s at %s",
                    oid,
                    error_status.prettyPrint(),
                    var_binds[int(error_index) - 1][0] if error_index else "?",
                )
                return None

            _oid, value = var_binds[0]
            return value

        except Exception:
            logger.exception("SNMP GET %s failed", oid)
            return None

    async def get_many(self, oids: list[str]) -> dict[str, Any]:
        """SNMP GET multiple OIDs. Returns {oid: value} for successful gets."""
        results = {}
        for oid in oids:
            value = await self.get(oid)
            if value is not None:
                results[oid] = value
        return results

    async def set(self, oid: str, value: int) -> bool:
        """SNMP SET an integer value. Returns True on success."""
        try:
            error_indication, error_status, error_index, var_binds = await setCmd(
                self.engine,
                self._write_community,
                self._target,
                ContextData(),
                ObjectType(ObjectIdentity(oid), Integer32(value)),
            )

            if error_indication:
                logger.error("SNMP SET %s=%s: %s", oid, value, error_indication)
                return False
            if error_status:
                logger.error(
                    "SNMP SET %s=%s: %s at %s",
                    oid,
                    value,
                    error_status.prettyPrint(),
                    var_binds[int(error_index) - 1][0] if error_index else "?",
                )
                return False

            return True

        except Exception:
            logger.exception("SNMP SET %s=%s failed", oid, value)
            return False

    def close(self):
        self.engine.close_dispatcher()
