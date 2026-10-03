"""Buttons: hand the single bridge slot to another client, and take it back."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VascoConfigEntry
from .const import RELEASE_SECONDS
from .entity import VascoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VascoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data.coordinator
    async_add_entities([VascoReleaseButton(c), VascoResumeButton(c)])


class VascoReleaseButton(VascoEntity, ButtonEntity):
    _attr_translation_key = "release_connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "release_connection")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.client.release(RELEASE_SECONDS)
        await self.coordinator.async_refresh()  # marks entities unavailable now


class VascoResumeButton(VascoEntity, ButtonEntity):
    _attr_translation_key = "resume_connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "resume_connection")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.client.resume()
        await self.coordinator.async_refresh()
