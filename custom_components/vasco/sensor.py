"""Sensors for Vasco."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VascoConfigEntry
from .client import regs_to_float
from .coordinator import VascoCoordinator, VascoData
from .entity import VascoEntity

# Below this indoor/outdoor difference the efficiency formula is just noise.
EFFICIENCY_MIN_DELTA = 3.0


def _efficiency(d: VascoData) -> float | None:
    live = d.live
    if live.bypass_position != 0:
        return None  # bypass open: heat recovery deliberately off
    delta = live.temp_exhaust - live.temp_inlet
    if abs(delta) < EFFICIENCY_MIN_DELTA:
        return None
    return round((live.temp_supply - live.temp_inlet) / delta * 100, 0)


@dataclass(frozen=True, kw_only=True)
class VascoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[VascoData], float | int | None]


def _temp(key: str, attr: str) -> VascoSensorDescription:
    return VascoSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d, a=attr: getattr(d.live, a),
    )


SENSORS: tuple[VascoSensorDescription, ...] = (
    _temp("temp_outdoor", "temp_inlet"),
    _temp("temp_supply", "temp_supply"),
    _temp("temp_extract", "temp_exhaust"),
    _temp("temp_exhaust", "temp_outlet"),
    VascoSensorDescription(
        key="fan_supply",
        translation_key="fan_supply",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.live.fan_supply,
    ),
    VascoSensorDescription(
        key="fan_extract",
        translation_key="fan_extract",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.live.fan_exhaust,
    ),
    VascoSensorDescription(
        key="efficiency",
        translation_key="efficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_efficiency,
    ),
    VascoSensorDescription(
        key="speed_code",
        translation_key="speed_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.live.speed_code,
    ),
    VascoSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.rssi,
    ),
)

# Readable registers whose meaning is still unknown. Recorded so that their
# history shows what changes when e.g. the filter is reset from the panel/app.
# (source, address, is_float)
UNKNOWN_REGS: tuple[tuple[str, int, bool], ...] = (
    ("extra", 41001, False),
    ("live", 41004, False),
    ("live", 41007, True),
    ("live", 41009, True),
    ("live", 41019, False),
    ("live", 41020, False),
    ("live", 41021, False),
    ("live", 41023, False),
    ("config", 42014, False),
    ("config", 42015, False),
    ("config", 42016, False),
    ("config", 42017, False),
    ("config", 42018, False),
    ("config", 42019, False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VascoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [VascoSensor(coordinator, d) for d in SENSORS]
    entities += [VascoRawSensor(coordinator, *spec) for spec in UNKNOWN_REGS]
    async_add_entities(entities)


class VascoSensor(VascoEntity, SensorEntity):
    entity_description: VascoSensorDescription

    def __init__(self, coordinator: VascoCoordinator, description: VascoSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self.coordinator.data)


class VascoRawSensor(VascoEntity, SensorEntity):
    """A not yet understood register, shown raw for reverse engineering."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: VascoCoordinator, source: str, address: int, is_float: bool
    ) -> None:
        super().__init__(coordinator, f"raw_{address}")
        self._source = source
        self._address = address
        self._is_float = is_float
        self._attr_translation_key = "raw_float" if is_float else "raw_register"
        self._attr_translation_placeholders = {"address": str(address)}
        if is_float:
            self._attr_suggested_display_precision = 2

    def _regs(self) -> dict[int, int | float]:
        d = self.coordinator.data
        if self._source == "live":
            return d.live.raw
        if self._source == "config":
            return d.config
        return d.extra

    @property
    def available(self) -> bool:
        return super().available and self._address in self._regs()

    @property
    def native_value(self) -> float | int | None:
        regs = self._regs()
        if self._is_float:
            lo, hi = regs.get(self._address), regs.get(self._address + 1)
            if lo is None or hi is None:
                return None
            return round(regs_to_float((int(lo), int(hi))), 3)
        return regs.get(self._address)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        val = self._regs().get(self._address)
        if self._is_float or val is None:
            return None
        return {"hex": f"0x{int(val):04X}"}
