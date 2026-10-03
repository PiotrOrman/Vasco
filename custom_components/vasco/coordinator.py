"""Data update coordinator for Vasco."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    DeviceInfo,
    LiveData,
    VascoClient,
    VascoError,
    VascoReleased,
)
from .const import CONFIG_REFRESH_EVERY, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Readable registers outside the live block that are still not understood.
# Polled with the live block so their history is recorded (filter-reset hunt).
EXTRA_LIVE_REGS = (41001,)


@dataclass
class VascoData:
    live: LiveData
    config: dict[int, int | float] = field(default_factory=dict)
    extra: dict[int, int] = field(default_factory=dict)
    rssi: int | None = None


class VascoCoordinator(DataUpdateCoordinator[VascoData]):
    """Polls the live block every scan interval and the config block every ~10 min."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: VascoClient,
        device: DeviceInfo,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.device = device
        self._polls = 0
        self._config: dict[int, int | float] = {}
        self._rssi: int | None = None
        self._unsub_later: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> VascoData:
        try:
            live = await self.client.read_live()
        except VascoReleased as err:
            raise UpdateFailed(str(err)) from err
        except VascoError as err:
            raise UpdateFailed(f"live block: {err}") from err

        extra: dict[int, int] = {}
        for addr in EXTRA_LIVE_REGS:
            try:
                extra[addr] = (await self.client.read_raw(addr, 1, ack_retries=0))[0]
            except VascoError as err:
                _LOGGER.debug("extra register %s: %s", addr, err)

        if self._polls % CONFIG_REFRESH_EVERY == 0 or not self._config:
            await self._refresh_config()
        self._polls += 1

        return VascoData(live=live, config=dict(self._config), extra=extra, rssi=self._rssi)

    async def _refresh_config(self) -> None:
        # Exception 5 here just means "RF fetch pending": keep the old values.
        try:
            self._config = await self.client.read_config()
        except VascoError as err:
            _LOGGER.debug("config block not refreshed: %s", err)
        try:
            self._rssi = await self.client.read_rssi()
        except VascoError as err:
            _LOGGER.debug("rssi not refreshed: %s", err)

    async def async_refresh_config_now(self) -> None:
        """Re-read the config block (after a write) and push it to entities."""
        await self._refresh_config()
        if self.data is not None:
            self.data.config = dict(self._config)
            self.async_update_listeners()

    @callback
    def schedule_refresh_in(self, seconds: float) -> None:
        """The unit applies commands over RF; give it a moment before re-reading."""
        if self._unsub_later:
            self._unsub_later()

        @callback
        def _fire(_now) -> None:
            self._unsub_later = None
            self.hass.async_create_task(self.async_request_refresh())

        self._unsub_later = async_call_later(self.hass, seconds, _fire)

    async def async_shutdown(self) -> None:
        if self._unsub_later:
            self._unsub_later()
            self._unsub_later = None
        await super().async_shutdown()
