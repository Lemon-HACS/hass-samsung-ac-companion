"""로컬 API 기반 센서 (순간 전력).

SmartThings 클라우드에는 순간 전력이 노출되지 않는다.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SubAcConfigEntry
from .coordinator import LocalAcCoordinator
from .entity import LocalAcEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return

    async_add_entities(
        InstantPowerSensor(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if "instantaneousPower" in device.get("EnergyConsumption", {})
    )


class InstantPowerSensor(LocalAcEntity, SensorEntity):
    """순간 소비 전력."""

    _attr_name = "순간 전력"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, "instant_power")

    @property
    def native_value(self) -> int | None:
        """현재 전력(W)."""
        return self.device.get("EnergyConsumption", {}).get("instantaneousPower")
