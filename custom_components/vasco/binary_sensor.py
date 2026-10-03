"""Binary sensors for Vasco."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VascoConfigEntry
from .entity import VascoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VascoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data.coordinator
    async_add_entities([VascoBypass(c), VascoConnection(c)])


class VascoBypass(VascoEntity, BinarySensorEntity):
    _attr_translation_key = "bypass"
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "bypass")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.live.bypass_position > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"position": self.coordinator.data.live.bypass_position}


class VascoConnection(VascoEntity, BinarySensorEntity):
    """Bridge link state. Stays available so that 'disconnected' is visible."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self.coordinator.client
        err = self.coordinator.last_exception
        return {
            "host": f"{client.host}:{client.port}",
            "released": bool(client.released_until),
            "last_error": str(err) if err and not self.coordinator.last_update_success else None,
        }
