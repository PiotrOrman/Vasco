"""Async Modbus TCP client for a Vasco / Airios VMD-17RPS01 behind a BRDG-02EM23.

Free of Home Assistant imports on purpose, so it can be exercised from a plain
Python shell (``python -m custom_components.vasco.client 192.168.2.140``).

Bridge quirks handled here (all observed on real hardware, 2026-10-03):

* only ONE TCP client at a time -> a single persistent connection guarded by a lock,
  plus :meth:`VascoClient.release` to hand the slot to another tool for a while;
* a request sent right after the TCP accept makes the bridge drop the socket
  -> wait ``POST_CONNECT_DELAY`` after connecting;
* Modbus exception 5 (ACKNOWLEDGE) means "value not cached yet, fetching over RF"
  -> retry after a few seconds instead of treating it as an error;
* exception 2 (ILLEGAL DATA ADDRESS) means the register does not exist;
* writes must use FC16 (write multiple registers), FC06 is rejected;
* floats are IEEE754 with the LOW word first.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .const import (
    CONFIG_FLOAT,
    CONFIG_U16,
    LIVE_COUNT,
    LIVE_START,
    PRESET_TO_REQUEST,
    REG_REQUESTED_SPEED,
    REG_RF_LAST_RSSI,
)

_LOGGER = logging.getLogger(__name__)

POST_CONNECT_DELAY = 0.5
REQUEST_GAP = 0.05
ACK_RETRY_DELAY = 5.0
ACK_RETRIES = 2
BACKOFF_START = 5.0
BACKOFF_MAX = 300.0

EXC_ILLEGAL_ADDRESS = 2
EXC_ACKNOWLEDGE = 5


class VascoError(Exception):
    """Base error."""


class VascoConnectionError(VascoError):
    """Bridge unreachable, slot taken or the connection dropped."""


class VascoReleased(VascoConnectionError):
    """The connection was deliberately released for a while."""


class VascoModbusError(VascoError):
    """The device answered with a Modbus exception."""

    def __init__(self, address: int, code: int) -> None:
        super().__init__(f"register {address}: modbus exception {code}")
        self.address = address
        self.code = code


class RegisterAbsent(VascoModbusError):
    """Exception 2: the register does not exist on this device."""


class RegisterBusy(VascoModbusError):
    """Exception 5 that did not clear: value pending or the register is write-only."""


# --- value helpers -------------------------------------------------------------


def regs_to_float(regs: list[int] | tuple[int, ...]) -> float:
    """Decode a word-swapped float (low word first)."""
    lo, hi = regs[0], regs[1]
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def float_to_regs(value: float) -> list[int]:
    """Encode a float as two registers, low word first."""
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [lo, hi]


def regs_to_u32(regs: list[int] | tuple[int, ...]) -> int:
    """Decode a U32 stored low word first."""
    return regs[0] | (regs[1] << 16)


def regs_to_i16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def regs_to_str(regs: list[int] | tuple[int, ...]) -> str:
    raw = b"".join(struct.pack(">H", r) for r in regs)
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


# --- data containers -------------------------------------------------------------


@dataclass
class DeviceInfo:
    rf_address: int
    product_id: int
    software_version: int
    oem_number: int
    product_name: str


@dataclass
class LiveData:
    speed_code: int
    fan_exhaust: int
    fan_supply: int
    temp_exhaust: float
    temp_inlet: float
    temp_outlet: float
    temp_supply: float
    bypass_position: int
    raw: dict[int, int] = field(default_factory=dict)
    """Every register of the live block (address -> raw value), for diagnostics."""


def _decode_live(regs: list[int]) -> LiveData:
    raw = {LIVE_START + i: v for i, v in enumerate(regs)}

    def f(addr: int) -> float:
        return round(regs_to_float((raw[addr], raw[addr + 1])), 2)

    return LiveData(
        speed_code=raw[41003],
        fan_exhaust=raw[41005],
        fan_supply=raw[41006],
        temp_exhaust=f(41011),
        temp_inlet=f(41013),
        temp_outlet=f(41015),
        temp_supply=f(41017),
        bypass_position=raw[41022],
        raw=raw,
    )


# --- client ------------------------------------------------------------------------


class VascoClient:
    """One persistent, serialised Modbus TCP connection to the bridge."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        unit_id: int = 2,
        *,
        timeout: float = 3.0,
        ack_retry_delay: float = ACK_RETRY_DELAY,
        ack_retries: int = ACK_RETRIES,
    ) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self._timeout = timeout
        self._ack_retry_delay = ack_retry_delay
        self._ack_retries = ack_retries
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._released_until = 0.0
        self._next_attempt = 0.0
        self._backoff = BACKOFF_START
        self.last_error: str | None = None

    # -- connection management --

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    @property
    def released_until(self) -> float:
        """Monotonic deadline of a manual release (0 when not released)."""
        return self._released_until if self._released_until > time.monotonic() else 0.0

    async def _ensure_connected(self) -> AsyncModbusTcpClient:
        now = time.monotonic()
        if self._released_until > now:
            raise VascoReleased(
                f"connection released for another {int(self._released_until - now)} s"
            )
        if self.connected:
            assert self._client is not None
            return self._client
        if now < self._next_attempt:
            raise VascoConnectionError(
                f"reconnect back-off, next attempt in {int(self._next_attempt - now)} s"
                + (f" (last error: {self.last_error})" if self.last_error else "")
            )
        await self._drop()
        # reconnect_delay=0: we do our own back-off, pymodbus must not grab the slot
        client = AsyncModbusTcpClient(
            self.host,
            port=self.port,
            timeout=self._timeout,
            retries=1,
            reconnect_delay=0,
        )
        try:
            ok = await client.connect()
        except (OSError, ModbusException) as err:
            ok = False
            self.last_error = str(err)
        if not ok:
            client.close()
            self._schedule_backoff()
            raise VascoConnectionError(
                f"cannot connect to {self.host}:{self.port} "
                "(bridge off, or another client holds the single TCP slot)"
            )
        await asyncio.sleep(POST_CONNECT_DELAY)
        self._client = client
        self._backoff = BACKOFF_START
        self._next_attempt = 0.0
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)
        return client

    def _schedule_backoff(self) -> None:
        self._next_attempt = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)

    async def _drop(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):  # closing must never raise
                self._client.close()
            self._client = None

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def release(self, seconds: float) -> None:
        """Close the connection and stay away for ``seconds`` (frees the slot)."""
        async with self._lock:
            await self._drop()
            self._released_until = time.monotonic() + seconds
            _LOGGER.info("Bridge connection released for %s s", seconds)

    async def resume(self) -> None:
        self._released_until = 0.0
        self._next_attempt = 0.0

    # -- raw access --

    async def _call(
        self, address: int, fn_name: str, *, ack_retries: int | None = None, **kwargs: Any
    ) -> Any:
        """Run one request with ACK retry. Must be called with the lock held."""
        retries = self._ack_retries if ack_retries is None else ack_retries
        for attempt in range(retries + 1):
            client = await self._ensure_connected()
            try:
                resp = await getattr(client, fn_name)(address, **kwargs)
            except (ModbusException, OSError, asyncio.TimeoutError) as err:
                self.last_error = f"{type(err).__name__}: {err}"
                await self._drop()
                self._schedule_backoff()
                raise VascoConnectionError(self.last_error) from err
            await asyncio.sleep(REQUEST_GAP)
            if not resp.isError():
                self.last_error = None
                return resp
            code = getattr(resp, "exception_code", None)
            if code == EXC_ILLEGAL_ADDRESS:
                raise RegisterAbsent(address, code)
            if code == EXC_ACKNOWLEDGE and attempt < retries:
                _LOGGER.debug(
                    "Register %s: ACK (RF fetch pending), retry in %ss",
                    address,
                    self._ack_retry_delay,
                )
                await asyncio.sleep(self._ack_retry_delay)
                continue
            if code == EXC_ACKNOWLEDGE:
                raise RegisterBusy(address, code)
            raise VascoModbusError(address, code if code is not None else -1)
        raise AssertionError("unreachable")

    async def read_raw(
        self,
        address: int,
        count: int = 1,
        device_id: int | None = None,
        *,
        ack_retries: int | None = None,
    ) -> list[int]:
        dev = self.unit_id if device_id is None else device_id
        async with self._lock:
            resp = await self._call(
                address,
                "read_holding_registers",
                ack_retries=ack_retries,
                count=count,
                device_id=dev,
            )
        return list(resp.registers)

    async def scan(
        self, start: int, end: int, device_id: int | None = None
    ) -> dict[int, int | str]:
        """Read ``start..end`` one register at a time (reverse-engineering helper).

        Absent registers (exception 2) are left out; other exceptions are reported
        as ``"exc<N>"``. No ACK retry, so a pending value shows up as ``exc5``.
        """
        out: dict[int, int | str] = {}
        for addr in range(start, end + 1):
            try:
                out[addr] = (await self.read_raw(addr, 1, device_id, ack_retries=0))[0]
            except RegisterAbsent:
                continue
            except VascoModbusError as err:
                out[addr] = f"exc{err.code}"
        return out

    async def write_raw(
        self, address: int, values: list[int], device_id: int | None = None
    ) -> None:
        """FC16 write. The bridge rejects FC06, so even single values go here."""
        dev = self.unit_id if device_id is None else device_id
        for v in values:
            if not 0 <= int(v) <= 0xFFFF:
                raise ValueError(f"register value out of range: {v}")
        async with self._lock:
            await self._call(
                address,
                "write_registers",
                values=[int(v) for v in values],
                device_id=dev,
            )

    # -- typed access --

    async def read_device_info(self) -> DeviceInfo:
        regs = await self.read_raw(40000, 17)
        return DeviceInfo(
            rf_address=regs_to_u32(regs[0:2]),
            product_id=regs_to_u32(regs[2:4]),
            software_version=regs[4],
            oem_number=regs[5],
            product_name=regs_to_str(regs[11:17]),
        )

    async def read_live(self) -> LiveData:
        return _decode_live(await self.read_raw(LIVE_START, LIVE_COUNT))

    async def read_config(self) -> dict[int, int | float]:
        """Read 42000 and 42003..42019 (42001/42002 are absent, so two requests).

        Returns address -> value: floats for CONFIG_FLOAT registers, raw U16 for
        everything else (including the not yet understood 42014..42019).
        """
        out: dict[int, int | float] = {}
        out[42000] = (await self.read_raw(42000, 1))[0]
        regs = await self.read_raw(42003, 17)
        raw = {42003 + i: v for i, v in enumerate(regs)}
        for addr, val in raw.items():
            if addr in CONFIG_FLOAT:
                out[addr] = round(regs_to_float((val, raw[addr + 1])), 2)
            elif addr - 1 in CONFIG_FLOAT:
                continue  # second word of a float
            else:
                out[addr] = val
        return out

    async def read_rssi(self) -> int:
        return regs_to_i16((await self.read_raw(REG_RF_LAST_RSSI, 1))[0])

    async def set_preset(self, preset: str) -> None:
        await self.write_raw(REG_REQUESTED_SPEED, [PRESET_TO_REQUEST[preset]])

    async def write_config(self, address: int, value: float) -> None:
        if address in CONFIG_FLOAT:
            await self.write_raw(address, float_to_regs(value))
        elif address in CONFIG_U16:
            await self.write_raw(address, [int(round(value))])
        else:
            raise ValueError(f"{address} is not a known configuration register")


# --- manual smoke test (read-only) ----------------------------------------------


async def _main(host: str) -> None:  # pragma: no cover
    logging.basicConfig(level=logging.DEBUG)
    c = VascoClient(host)
    try:
        t0 = time.monotonic()
        print("device ", await c.read_device_info())
        print("live   ", await c.read_live())
        print("config ", await c.read_config())
        print("rssi   ", await c.read_rssi())
        print(f"total {time.monotonic() - t0:.1f} s")
    finally:
        await c.close()


if __name__ == "__main__":  # pragma: no cover
    import sys

    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "192.168.2.140"))
