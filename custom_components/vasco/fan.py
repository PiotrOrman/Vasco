"""Fan entity: the recuperator speed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import VascoConfigEntry
from .client import VascoError
from .const import PRESETS_ON, SPEED_CODE_TO_PRESET
from .entity import VascoEntity

COMMAND_SETTLE = 4  # s; the unit applies the command over RF


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VascoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([VascoFan(entry.runtime_data.coordinator)])


class VascoFan(VascoEntity, FanEntity):
    _attr_translation_key = "ventilation"
    _attr_name = None  # the device itself
    _attr_preset_modes = list(PRESETS_ON)
    _attr_speed_count = len(PRESETS_ON)
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "fan")
        self._optimistic: str | None = None

    @property
    def _code(self) -> int:
        return self.coordinator.data.live.speed_code

    @property
    def is_on(self) -> bool:
        if self._optimistic is not None:
            return self._optimistic != "off"
        return self._code != 0

    @property
    def preset_mode(self) -> str | None:
        if self._optimistic is not None:
            return None if self._optimistic == "off" else self._optimistic
        preset = SPEED_CODE_TO_PRESET.get(self._code)
        return None if preset in (None, "off") else preset

    @property
    def percentage(self) -> int | None:
        preset = self.preset_mode
        if not self.is_on:
            return 0
        if preset is None:
            return None  # e.g. the unexplained code 5
        return ordered_list_item_to_percentage(list(PRESETS_ON), preset)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"speed_code": self._code}

    def _handle_coordinator_update(self) -> None:
        self._optimistic = None
        super()._handle_coordinator_update()

    async def _send(self, preset: str) -> None:
        try:
            await self.coordinator.client.set_preset(preset)
        except VascoError as err:
            raise HomeAssistantError(f"Cannot set speed: {err}") from err
        self._optimistic = preset
        self.async_write_ha_state()
        self.coordinator.schedule_refresh_in(COMMAND_SETTLE)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._send(preset_mode)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self._send("off")
            return
        await self._send(percentage_to_ordered_list_item(list(PRESETS_ON), percentage))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if preset_mode:
            await self._send(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            await self._send("low")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send("off")
