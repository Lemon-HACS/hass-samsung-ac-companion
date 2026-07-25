"""로컬 API 기반 스위치 (무풍 / 자동청소건조 / 무드등)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SubAcConfigEntry
from .coordinator import LocalAcCoordinator
from .entity import LocalAcEntity


@dataclass(frozen=True, kw_only=True)
class LocalSwitchDescription(SwitchEntityDescription):
    """옵션 하나를 켜고 끄는 스위치 정의."""

    # Mode.options 에서 이 접두사를 가진 값을 찾는다 (예: "Comode")
    prefix: str
    on_value: str
    off_value: str


SWITCHES: tuple[LocalSwitchDescription, ...] = (
    LocalSwitchDescription(
        key="windfree",
        translation_key="windfree",
        name="무풍",
        icon="mdi:weather-windy",
        prefix="Comode",
        # Comode 슬롯은 정음/스피드와 공유한다. 무풍이 아닌 값이 들어있으면
        # 이 스위치는 꺼진 것으로 보이고, 켜면 무풍으로 덮어쓴다.
        on_value="Nano",
        off_value="Off",
    ),
    LocalSwitchDescription(
        key="autoclean",
        translation_key="autoclean",
        name="자동청소건조",
        icon="mdi:air-filter",
        prefix="Autoclean",
        on_value="On",
        off_value="Off",
    ),
    LocalSwitchDescription(
        key="light",
        translation_key="light",
        name="무드등",
        icon="mdi:lightbulb",
        prefix="Light",
        on_value="On",
        off_value="Off",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return

    entities: list[SwitchEntity] = []
    for device_id, device in coordinator.data.items():
        for description in SWITCHES:
            # 해당 유닛이 지원하지 않는 옵션은 건너뛴다.
            if LocalAcCoordinator.get_option(device, description.prefix) is None:
                continue
            entities.append(LocalAcSwitch(coordinator, device_id, description))

        # 바람문 상/중/하 (비트마스크). 이 옵션이 있는 유닛에만 만든다.
        if LocalAcCoordinator.get_option(device, "Blooming") is not None:
            for bit, label in ((1, "상"), (2, "중"), (4, "하")):
                entities.append(BloomingSwitch(coordinator, device_id, bit, label))

    async_add_entities(entities)


class LocalAcSwitch(LocalAcEntity, SwitchEntity):
    """옵션 토글 스위치."""

    entity_description: LocalSwitchDescription

    def __init__(
        self,
        coordinator: LocalAcCoordinator,
        device_id: str,
        description: LocalSwitchDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """현재 값이 on_value 인지."""
        value = LocalAcCoordinator.get_option(
            self.device, self.entity_description.prefix
        )
        return value == self.entity_description.on_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._set(self.entity_description.on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._set(self.entity_description.off_value)

    async def _set(self, value: str) -> None:
        prefix = self.entity_description.prefix
        # 폴링을 기다리지 않고 UI 를 먼저 움직인다. 실제 값은 뒤이은
        # refresh 가 덮어쓰므로, 기기가 명령을 거부해도 곧 제자리로 돌아온다.
        self.coordinator.apply_optimistic_option(self._device_id, prefix, value)
        await self.coordinator.async_send(
            self._device_id, "mode", {"options": [f"{prefix}_{value}"]}
        )


class BloomingSwitch(LocalAcEntity, SwitchEntity):
    """바람문 상/중/하 (`Blooming_N` 비트마스크).

    `1`=상, `2`=중, `4`=하 이고 합산으로 조합된다 (`5`=상+하, `7`=전부).
    각 위치를 독립 스위치로 노출한다.
    """

    _attr_icon = "mdi:air-conditioner"

    def __init__(
        self,
        coordinator: LocalAcCoordinator,
        device_id: str,
        bit: int,
        label: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, device_id, f"blooming_{bit}")
        self._bit = bit
        self._attr_name = f"바람문 {label}"

    @property
    def _mask(self) -> int | None:
        raw = LocalAcCoordinator.get_option(self.device, "Blooming")
        if raw is None or not raw.isdigit():
            return None
        return int(raw)

    @property
    def is_on(self) -> bool:
        """이 위치의 비트가 켜져 있는지."""
        mask = self._mask
        return bool(mask is not None and mask & self._bit)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._apply(lambda mask: mask | self._bit)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._apply(lambda mask: mask & ~self._bit)

    async def _apply(self, transform) -> None:
        mask = self._mask
        if mask is None:
            return
        new_mask = transform(mask) & 0b111
        if new_mask == mask:
            return
        # 전부 닫으면 바람이 나올 곳이 없다. 기기가 거부할 수 있으므로 막는다.
        if new_mask == 0:
            raise HomeAssistantError(
                "바람문을 전부 닫을 수는 없습니다. 최소 한 곳은 열려 있어야 합니다."
            )

        self.coordinator.apply_optimistic_option(
            self._device_id, "Blooming", str(new_mask)
        )
        await self.coordinator.async_send(
            self._device_id, "mode", {"options": [f"Blooming_{new_mask}"]}
        )
