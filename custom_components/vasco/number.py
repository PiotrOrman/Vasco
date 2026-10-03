"""Configuration numbers (42xxx). Writes are off unless enabled in the options."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VascoConfigEntry
from .client import VascoError
from .const import (
    CONF_ALLOW_WRITES,
    DOMAIN,
    REG_BYPASS_THRESHOLD,
    REG_HIGH_A,
    REG_HIGH_B,
    REG_LOW_EXHAUST,
    REG_LOW_SUPPLY,
    REG_MID_EXHAUST,
    REG_MID_SUPPLY,
    REG_MIN_SUPPLY_TEMP,
    REG_PREHEATER_ACTIVATION,
)
from .coordinator import VascoCoordinator
from .entity import VascoEntity


@dataclass(frozen=True, kw_only=True)
class VascoNumberDescription(NumberEntityDescription):
    address: int


def _pct(key: str, address: int) -> VascoNumberDescription:
    return VascoNumberDescription(
        key=key,
        translation_key=key,
        address=address,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    )


def _flow(key: str, address: int) -> VascoNumberDescription:
    return VascoNumberDescription(
        key=key,
        translation_key=key,
        address=address,
        native_min_value=100,
        native_max_value=350,
        native_step=5,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        device_class=NumberDeviceClass.VOLUME_FLOW_RATE,
    )


def _temp(key: str, address: int, lo: float, hi: float) -> VascoNumberDescription:
    return VascoNumberDescription(
        key=key,
        translation_key=key,
        address=address,
        native_min_value=lo,
        native_max_value=hi,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
    )


NUMBERS: tuple[VascoNumberDescription, ...] = (
    _pct("low_supply", REG_LOW_SUPPLY),
    _pct("low_extract", REG_LOW_EXHAUST),
    _pct("mid_supply", REG_MID_SUPPLY),
    _pct("mid_extract", REG_MID_EXHAUST),
    _flow("high_a", REG_HIGH_A),
    _flow("high_b", REG_HIGH_B),
    _temp("bypass_threshold", REG_BYPASS_THRESHOLD, 18, 30),
    _temp("preheater_activation", REG_PREHEATER_ACTIVATION, -20, 5),
    _temp("min_supply_temp", REG_MIN_SUPPLY_TEMP, 5, 20),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VascoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data.coordinator
    async_add_entities(VascoNumber(c, d) for d in NUMBERS)


class VascoNumber(VascoEntity, NumberEntity):
    entity_description: VascoNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: VascoCoordinator, description: VascoNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.address in self.coordinator.data.config

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.data.config.get(self.entity_description.address)
        return None if val is None else float(val)

    @property
    def extra_state_attributes(self) -> dict[str, int | bool]:
        return {
            "register": self.entity_description.address,
            "writable": self.coordinator.config_entry.options.get(CONF_ALLOW_WRITES, False),
        }

    async def async_set_native_value(self, value: float) -> None:
        if not self.coordinator.config_entry.options.get(CONF_ALLOW_WRITES, False):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="writes_disabled"
            )
        try:
            await self.coordinator.client.write_config(self.entity_description.address, value)
        except VascoError as err:
            raise HomeAssistantError(f"Write failed: {err}") from err
        await self.coordinator.async_refresh_config_now()
