"""Base entity for Vasco."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VascoCoordinator


class VascoEntity(CoordinatorEntity[VascoCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: VascoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        dev = coordinator.device
        self._attr_unique_id = f"{dev.rf_address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(dev.rf_address))},
            manufacturer="Vasco / Airios",
            model=dev.product_name or "VMD-17RPS01",
            model_id=f"0x{dev.product_id:08X}",
            name="Rekuperator Vasco",
            sw_version=str(dev.software_version),
            serial_number=str(dev.rf_address),
        )
