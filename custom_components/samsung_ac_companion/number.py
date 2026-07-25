"""열대야 쾌면 타이머.

`Sleep_N` 의 N 은 **30분 단위**다 (`Sleep_8` = 4시간). 실기기에서 확인했다.
앱의 최대치 12시간은 `Sleep_24` 에 해당한다.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SubAcConfigEntry
from .coordinator import LocalAcCoordinator
from .entity import LocalAcEntity

# 기기 값 1 = 30분
MINUTES_PER_STEP = 30
MAX_HOURS = 12


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up numbers."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return

    async_add_entities(
        SleepTimerNumber(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if LocalAcCoordinator.get_option(device, "Sleep") is not None
    )


class SleepTimerNumber(LocalAcEntity, NumberEntity):
    """열대야 쾌면 (시간 단위, 30분 간격). 0 이면 해제."""

    _attr_name = "열대야 쾌면"
    _attr_icon = "mdi:sleep"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_HOURS
    _attr_native_step = MINUTES_PER_STEP / 60  # 0.5시간
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, "sleep_timer")

    @property
    def native_value(self) -> float | None:
        """설정된 시간."""
        raw = LocalAcCoordinator.get_option(self.device, "Sleep")
        if raw is None or not raw.isdigit():
            return None
        return int(raw) * MINUTES_PER_STEP / 60

    async def async_set_native_value(self, value: float) -> None:
        """타이머 설정. 0 이면 해제."""
        steps = round(value * 60 / MINUTES_PER_STEP)
        self.coordinator.apply_optimistic_option(
            self._device_id, "Sleep", str(steps)
        )
        await self.coordinator.async_send(
            self._device_id, "mode", {"options": [f"Sleep_{steps}"]}
        )
