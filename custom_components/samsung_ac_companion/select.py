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

# speedLevel ↔ 표시 이름. 앱의 "바람세기" 5단계와 그대로 대응한다.
#
# 무풍은 여기 없다 — 무풍은 `Comode_Nano`(별도 스위치)이고, 켜지면
# speedLevel 이 0 으로 밀릴 뿐이다. 앱에서도 "바람세기"와 "무풍"은 별개
# 메뉴다.
SPEED_LEVELS: dict[int, str] = {
    0: "자동풍",
    1: "미풍",
    2: "약풍",
    3: "강풍",
    4: "터보",
}
NAME_TO_LEVEL = {name: level for level, name in SPEED_LEVELS.items()}


# 운전기능 (`Comode_*`). 무풍·정음·스피드가 한 슬롯을 공유하므로 select 가
# 맞다. 앱의 "운전기능" 메뉴와 같은 구성이다.
COMODE_OPTIONS: dict[str, str] = {
    "Off": "해제",
    "Nano": "무풍",
    "Quiet": "정음",
    "Speed": "스피드",
    "LongWind": "롱바람",
}
COMODE_TO_VALUE = {name: value for value, name in COMODE_OPTIONS.items()}

# 바람 방향 (`Wind.direction`).
DIRECTION_OPTIONS: dict[str, str] = {
    "Fix": "고정",
    "Up_And_Low": "상하",
}
DIRECTION_TO_VALUE = {name: value for value, name in DIRECTION_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return

    entities: list[LocalAcEntity] = []
    for device_id, device in coordinator.data.items():
        if "Wind" in device:
            entities.append(FanSpeedSelect(coordinator, device_id))
            entities.append(WindDirectionSelect(coordinator, device_id))
        if LocalAcCoordinator.get_option(device, "Comode") is not None:
            entities.append(ComodeSelect(coordinator, device_id))
    async_add_entities(entities)


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

        무풍이 켜져 있으면 기기가 풍량 변경을 무시하므로 먼저 해제한다.
        """
        level = NAME_TO_LEVEL[option]

        if LocalAcCoordinator.get_option(self.device, "Comode") == "Nano":
            self.coordinator.apply_optimistic_option(self._device_id, "Comode", "Off")
            await self.coordinator.async_send(
                self._device_id, "mode", {"options": ["Comode_Off"]}
            )

        self.coordinator.apply_optimistic_wind(self._device_id, "speedLevel", level)
        await self.coordinator.async_send(
            self._device_id, "wind", {"speedLevel": level}
        )


class ComodeSelect(LocalAcEntity, SelectEntity):
    """운전기능 (해제/무풍/정음/스피드/롱바람).

    무풍 스위치와 같은 슬롯을 공유한다 — 한쪽을 바꾸면 다른 쪽도 따라간다.
    """

    _attr_name = "운전기능"
    _attr_icon = "mdi:tune-variant"
    _attr_options = list(COMODE_OPTIONS.values())

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, "comode")

    @property
    def current_option(self) -> str | None:
        """현재 운전기능."""
        value = LocalAcCoordinator.get_option(self.device, "Comode")
        if value is None:
            return None
        # 기기가 목록에 없는 값을 쓰면 원문을 그대로 보여준다.
        return COMODE_OPTIONS.get(value, value)

    async def async_select_option(self, option: str) -> None:
        """운전기능 변경."""
        value = COMODE_TO_VALUE[option]
        self.coordinator.apply_optimistic_option(self._device_id, "Comode", value)
        await self.coordinator.async_send(
            self._device_id, "mode", {"options": [f"Comode_{value}"]}
        )


class WindDirectionSelect(LocalAcEntity, SelectEntity):
    """바람 방향 (고정/상하)."""

    _attr_name = "바람 방향"
    _attr_icon = "mdi:arrow-oscillating"
    _attr_options = list(DIRECTION_OPTIONS.values())

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, "wind_direction")

    @property
    def current_option(self) -> str | None:
        """현재 바람 방향.

        무풍/정지 중에는 `Off` 가 되는데, 이는 선택지가 아니므로 None 을
        돌려준다(UI 에 '알 수 없음'으로 표시된다).
        """
        value = self.device.get("Wind", {}).get("direction")
        return DIRECTION_OPTIONS.get(value)

    async def async_select_option(self, option: str) -> None:
        """바람 방향 변경."""
        value = DIRECTION_TO_VALUE[option]
        self.coordinator.apply_optimistic_wind(self._device_id, "direction", value)
        await self.coordinator.async_send(
            self._device_id, "wind", {"direction": value}
        )
