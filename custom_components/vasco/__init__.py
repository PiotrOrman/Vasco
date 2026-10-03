"""Vasco recuperator (Airios VMD-17RPS01) over a BRDG-02EM23 Modbus TCP bridge."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .client import VascoClient, VascoError
from .const import (
    CONF_ALLOW_WRITES,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    PRODUCT_ID_VMD_17RPS01,
)
from .coordinator import VascoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY = "config_entry_id"
ATTR_ADDRESS = "address"
ATTR_COUNT = "count"
ATTR_DEVICE_ID = "device_id"
ATTR_START = "start"
ATTR_END = "end"
ATTR_VALUES = "values"
MAX_SCAN = 200

_REG = vol.All(vol.Coerce(int), vol.Range(min=0, max=65535))
_DEV = vol.All(vol.Coerce(int), vol.Range(min=1, max=247))

READ_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_ADDRESS): _REG,
        vol.Optional(ATTR_COUNT, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=125)),
        vol.Optional(ATTR_DEVICE_ID): _DEV,
    }
)
SCAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_START): _REG,
        vol.Required(ATTR_END): _REG,
        vol.Optional(ATTR_DEVICE_ID): _DEV,
    }
)
WRITE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_ADDRESS): _REG,
        vol.Required(ATTR_VALUES): vol.All(
            cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=65535))]
        ),
        vol.Optional(ATTR_DEVICE_ID): _DEV,
    }
)


@dataclass
class VascoRuntime:
    client: VascoClient
    coordinator: VascoCoordinator
    last_scans: dict[tuple[int, int, int], tuple[str, dict[int, int | str]]] = field(
        default_factory=dict
    )


type VascoConfigEntry = ConfigEntry[VascoRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the reverse-engineering services (they share the entry's connection)."""

    def _runtime(call: ServiceCall) -> tuple[VascoConfigEntry, VascoRuntime]:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if entry_id := call.data.get(ATTR_ENTRY):
            entries = [e for e in entries if e.entry_id == entry_id]
        if len(entries) != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found"
            )
        return entries[0], entries[0].runtime_data

    async def read_registers(call: ServiceCall) -> ServiceResponse:
        _, rt = _runtime(call)
        addr = call.data[ATTR_ADDRESS]
        try:
            regs = await rt.client.read_raw(
                addr, call.data[ATTR_COUNT], call.data.get(ATTR_DEVICE_ID)
            )
        except VascoError as err:
            return {"address": addr, "error": str(err)}
        return {
            "address": addr,
            "registers": {str(addr + i): v for i, v in enumerate(regs)},
        }

    async def scan_registers(call: ServiceCall) -> ServiceResponse:
        _, rt = _runtime(call)
        start, end = call.data[ATTR_START], call.data[ATTR_END]
        if end < start or end - start >= MAX_SCAN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="bad_scan_range",
                translation_placeholders={"max": str(MAX_SCAN)},
            )
        dev = call.data.get(ATTR_DEVICE_ID) or rt.client.unit_id
        try:
            result = await rt.client.scan(start, end, dev)
        except VascoError as err:
            raise HomeAssistantError(str(err)) from err
        key = (dev, start, end)
        prev = rt.last_scans.get(key)
        now = dt_util.now().isoformat(timespec="seconds")
        rt.last_scans[key] = (now, result)
        changed: dict[str, Any] = {}
        if prev:
            before = prev[1]
            for addr in sorted(set(before) | set(result)):
                if before.get(addr) != result.get(addr):
                    changed[str(addr)] = {"before": before.get(addr), "after": result.get(addr)}
        return {
            "device_id": dev,
            "scanned_at": now,
            "registers": {str(a): v for a, v in result.items()},
            "previous_scan": prev[0] if prev else None,
            "changed": changed,
        }

    async def write_registers(call: ServiceCall) -> None:
        entry, rt = _runtime(call)
        if not entry.options.get(CONF_ALLOW_WRITES, False):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="writes_disabled"
            )
        try:
            await rt.client.write_raw(
                call.data[ATTR_ADDRESS], call.data[ATTR_VALUES], call.data.get(ATTR_DEVICE_ID)
            )
        except VascoError as err:
            raise HomeAssistantError(str(err)) from err
        _LOGGER.warning(
            "Raw write %s <- %s (device %s)",
            call.data[ATTR_ADDRESS],
            call.data[ATTR_VALUES],
            call.data.get(ATTR_DEVICE_ID) or rt.client.unit_id,
        )
        rt.coordinator.schedule_refresh_in(5)

    hass.services.async_register(
        DOMAIN, "read_registers", read_registers, READ_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, "scan_registers", scan_registers, SCAN_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(DOMAIN, "write_registers", write_registers, WRITE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VascoConfigEntry) -> bool:
    client = VascoClient(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data.get(CONF_UNIT_ID, DEFAULT_UNIT_ID),
    )
    try:
        device = await client.read_device_info()
    except VascoError as err:
        await client.close()
        raise ConfigEntryNotReady(str(err)) from err
    if device.product_id != PRODUCT_ID_VMD_17RPS01:
        _LOGGER.warning(
            "Unexpected product id 0x%X (expected 0x%X); register map may not match",
            device.product_id,
            PRODUCT_ID_VMD_17RPS01,
        )

    coordinator = VascoCoordinator(
        hass,
        entry,
        client,
        device,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.close()
        raise

    entry.runtime_data = VascoRuntime(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: VascoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VascoConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.client.close()
    return ok
