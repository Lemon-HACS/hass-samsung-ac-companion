"""로컬 API 기반 풍량 선택 (미풍 포함 5단계).

SmartThings 클라우드는 `auto/medium/high/turbo` 4개만 노출하고 **미풍을
표현하지 못한다.** 로컬 API 의 `wind.speedLevel` 은 0~4 숫자라 미풍(1)까지
지정할 수 있다.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SubAcConfigEntry
from .coordinator import LocalAcCoordinator
from .entity import LocalAcEntity

# speedLevel ↔ 표시 이름
SPEED_LEVELS: dict[int, str] = {
    0: "무풍",
    1: "미풍",
    2: "약풍",
    3: "강풍",
    4: "터보",
}
NAME_TO_LEVEL = {name: level for level, name in SPEED_LEVELS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return

    async_add_entities(
        FanSpeedSelect(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if "Wind" in device
    )


class FanSpeedSelect(LocalAcEntity, SelectEntity):
    """풍량 5단계."""

    _attr_name = "풍량"
    _attr_icon = "mdi:fan"
    _attr_options = list(SPEED_LEVELS.values())

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, "fan_speed")

    @property
    def current_option(self) -> str | None:
        """현재 풍량."""
        level = self.device.get("Wind", {}).get("speedLevel")
        if level is None:
            return None
        return SPEED_LEVELS.get(int(level))

    async def async_select_option(self, option: str) -> None:
        """풍량 변경.

        무풍(0)은 speedLevel 로 직접 지정되지 않고 `Comode_Nano` 로 켜야 한다.
        반대로 무풍이 켜진 상태에서는 speedLevel 변경이 무시되므로 먼저 끈다.
        """
        level = NAME_TO_LEVEL[option]
        comode = LocalAcCoordinator.get_option(self.device, "Comode")

        if level == 0:
            await self.coordinator.async_send(
                self._device_id, "mode", {"options": ["Comode_Nano"]}
            )
            return

        if comode == "Nano":
            await self.coordinator.async_send(
                self._device_id, "mode", {"options": ["Comode_Off"]}
            )

        await self.coordinator.async_send(
            self._device_id, "wind", {"speedLevel": level}
        )
