"""로컬 API 엔티티 공통 베이스."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ST_DOMAIN
from .coordinator import LocalAcCoordinator


class LocalAcEntity(CoordinatorEntity[LocalAcCoordinator]):
    """로컬 API 로 제어하는 엔티티의 베이스.

    코어 SmartThings 통합이 만든 기기에 붙는다. 로컬 API 의 유닛 id 와
    SmartThings 의 component 번호가 다르므로(로컬 0=스탠드, 1=벽걸이 /
    SmartThings main=스탠드, "1"=벽걸이) identifier 를 맞춰준다.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalAcCoordinator,
        device_id: str,
        key: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{coordinator.host}_{device_id}_{key}"

    @property
    def device(self) -> dict[str, Any]:
        """이 엔티티가 속한 유닛의 최신 상태."""
        return self.coordinator.data.get(self._device_id, {})

    @property
    def available(self) -> bool:
        """유닛이 응답하고 있는지."""
        return super().available and bool(self.device.get("connected", False))

    @property
    def device_info(self) -> DeviceInfo:
        """코어 SmartThings 기기에 붙인다."""
        base = self.coordinator.base_uuid
        if base is None:
            # 아직 base uuid 를 모르면 로컬 uuid 로 독립 기기를 만든다.
            return DeviceInfo(
                identifiers={(ST_DOMAIN, self.device.get("uuid", self._device_id))}
            )
        identifier = base if self._device_id == "0" else f"{base}_{self._device_id}"
        return DeviceInfo(identifiers={(ST_DOMAIN, identifier)})
