"""climate 플랫폼 — 클라우드 서브 컴포넌트 + 로컬 API 엔티티.

두 종류를 함께 만든다.

1. `SubAirConditioner` — 코어가 무시하는 **서브 컴포넌트**(2 in 1 의 벽걸이)를
   SmartThings 클라우드로 제어한다. 코어의 `SmartThingsAirConditioner` 를
   그대로 상속하므로 제어 로직을 새로 구현하지 않는다.
2. `LocalAirConditioner` — 두 유닛 모두를 **로컬 API** 로 제어한다. 무풍·미풍·
   바람문처럼 클라우드에 아예 없는 것까지 다룬다. 자세한 배경은
   `local_climate.py` 참고.

둘은 같은 기기에 공존한다. 로컬이 끊겨도 클라우드 쪽은 살아 있다.

아래는 1번에 대한 설명이다.

코어 `SmartThingsEntity` 는 이미 `component` 인자를 받도록 되어 있고
(`execute_device_command`, `get_attribute_value`, 이벤트 구독이 전부
`self.component` 기준으로 동작한다), 단지 `SmartThingsAirConditioner.__init__`
이 그 인자를 노출하지 않을 뿐이다. 그래서 여기서는 조부모의 `__init__` 을
직접 호출해 component 를 주입한다.
"""

from __future__ import annotations

import logging

from pysmartthings import Capability, SmartThings

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.smartthings import FullDevice
from homeassistant.components.smartthings.climate import (
    AC_CAPABILITIES,
    SmartThingsAirConditioner,
)
from homeassistant.components.smartthings.const import MAIN
from homeassistant.components.smartthings.entity import SmartThingsEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SubAcConfigEntry
from .const import HEAT_PUMP_COMPONENTS, ST_DOMAIN
from .local_climate import LocalAirConditioner

_LOGGER = logging.getLogger(__name__)

# 코어 SmartThingsAirConditioner.__init__ 이 SmartThingsEntity 에 넘기는 집합과
# 동일해야 한다. 코어가 이 목록을 export 하지 않아 부득이하게 복제한다.
# (코어 업데이트 시 확인이 필요한 유일한 지점)
_AC_ENTITY_CAPABILITIES = {
    Capability.AIR_CONDITIONER_MODE,
    Capability.SWITCH,
    Capability.FAN_OSCILLATION_MODE,
    Capability.AIR_CONDITIONER_FAN_MODE,
    Capability.THERMOSTAT_COOLING_SETPOINT,
    Capability.TEMPERATURE_MEASUREMENT,
    Capability.CUSTOM_AIR_CONDITIONER_OPTIONAL_MODE,
    Capability.DEMAND_RESPONSE_LOAD_CONTROL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """서브 컴포넌트(클라우드)와 로컬 API 에어컨을 엔티티로 추가한다."""
    st_data = entry.runtime_data.st_entry.runtime_data

    entities: list[ClimateEntity] = []
    for device in st_data.devices.values():
        for component, status in device.status.items():
            if component == MAIN:
                continue  # 코어가 이미 만든다
            if component in HEAT_PUMP_COMPONENTS:
                continue  # 코어의 SmartThingsHeatPumpZone 이 이미 만든다
            if not all(capability in status for capability in AC_CAPABILITIES):
                continue

            _LOGGER.debug(
                "서브 에어컨 발견: %s (device_id=%s, component=%s)",
                device.device.label,
                device.device.device_id,
                component,
            )
            entities.append(SubAirConditioner(st_data.client, device, component))

    if not entities:
        _LOGGER.info(
            "에어컨 capability 를 가진 서브 컴포넌트를 찾지 못했습니다. "
            "기기가 2 in 1 이 아니거나 SmartThings 가 해당 컴포넌트를 "
            "노출하지 않는 경우입니다"
        )

    # 로컬 API 가 설정되어 있으면 두 유닛 모두에 로컬 엔티티를 만든다.
    # 위에서 만든 클라우드 엔티티와 같은 기기에 나란히 붙는다.
    if (coordinator := entry.runtime_data.local_coordinator) is not None:
        for device_id, device in coordinator.data.items():
            if device.get("type") != "Air_Conditioner":
                continue
            _LOGGER.debug(
                "로컬 에어컨 발견: %s (id=%s)", device.get("name"), device_id
            )
            entities.append(LocalAirConditioner(coordinator, device_id))

    async_add_entities(entities)


class SubAirConditioner(SmartThingsAirConditioner):
    """`main` 이 아닌 컴포넌트에 붙는 에어컨 엔티티."""

    _attr_name = None
    # 코어의 translation_key("air_conditioner")는 코어 통합의 strings 를
    # 가리키므로 우리 도메인에서는 쓸 수 없다.
    _attr_translation_key = None

    def __init__(
        self, client: SmartThings, device: FullDevice, component: str
    ) -> None:
        """Init the class."""
        # 부모(SmartThingsAirConditioner)의 __init__ 은 component 를 받지 않으므로
        # 조부모(SmartThingsEntity)를 직접 호출한다.
        SmartThingsEntity.__init__(
            self,
            client,
            device,
            _AC_ENTITY_CAPABILITIES,
            component=component,
        )

        # 아래 4줄은 코어 SmartThingsAirConditioner.__init__ 의 나머지 부분과 동일.
        self._attr_hvac_modes = self._determine_hvac_modes()
        self._attr_preset_modes = self._determine_preset_modes()
        if self.supports_capability(Capability.FAN_OSCILLATION_MODE):
            self._attr_swing_modes = self._determine_swing_modes()
        self._attr_supported_features = self._determine_supported_features()

        # 코어가 만든 기기의 하위 기기로 붙인다.
        # (코어 SmartThingsHeatPumpZone 과 동일한 패턴)
        self._attr_device_info = DeviceInfo(
            identifiers={(ST_DOMAIN, f"{device.device.device_id}_{component}")},
            via_device=(ST_DOMAIN, device.device.device_id),
            name=f"{device.device.label} {component}",
        )
