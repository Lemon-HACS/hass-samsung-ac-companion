"""로컬 API 로 제어하는 에어컨 climate 엔티티.

코어 SmartThings 통합이 만든 클라우드 climate 와 **공존**한다. 클라우드
쪽은 건드리지 않고, 이쪽은 기기와 직접 통신해 클라우드가 표현하지 못하는
것까지 한 엔티티에 담는다.

| | 클라우드 | 로컬(이 엔티티) |
|---|---|---|
| 반영 속도 | 수십 초~수 분 | 즉시 |
| 풍량 | 4단계 | **5단계 (미풍 포함)** |
| 온도 범위 | 7~35 (기기와 무관한 값) | 기기가 알려주는 실제 값 |
| 운전기능 | 없음 | `preset_mode` |
| 바람 방향 / 바람문 | 없음 | `swing_mode` |

로컬 API 가 끊기면 이 엔티티는 unavailable 이 되지만 클라우드 쪽은 계속
동작하므로, 둘을 함께 두는 것이 폴백이 된다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .coordinator import LocalAcCoordinator
from .entity import LocalAcEntity

# --- 값 매핑 ---------------------------------------------------------------

LOCAL_TO_HVAC: dict[str, HVACMode] = {
    "Cool": HVACMode.COOL,
    "Dry": HVACMode.DRY,
    # 이 기기에 송풍은 없다. `Wind` 의 실제 표시명이 "청정"이다.
    "Wind": HVACMode.FAN_ONLY,
    "Auto": HVACMode.AUTO,
}
HVAC_TO_LOCAL = {hvac: local for local, hvac in LOCAL_TO_HVAC.items()}

# `CoolClean`/`DryClean` 처럼 청정이 덧붙는 모드가 있다. climate 는 앞부분만
# 다루고 접미사는 별도 스위치(switch.py 의 CleanSwitch)가 맡는다.
CLEAN_SUFFIX = "Clean"

# `wind.speedLevel` ↔ 앱의 "바람세기" 5단계.
# 무풍은 여기 없다 — 무풍은 `Comode_Nano`(preset)이고, 켜지면 speedLevel 이
# 0 으로 밀릴 뿐이다. 앱에서도 두 메뉴가 별개다.
SPEED_LEVELS: dict[int, str] = {
    0: "자동풍",
    1: "미풍",
    2: "약풍",
    3: "강풍",
    4: "터보",
}
FAN_TO_LEVEL = {name: level for level, name in SPEED_LEVELS.items()}

# 운전기능 (`Comode_*`). 한 슬롯을 공유하므로 preset 이 맞다.
COMODE_OPTIONS: dict[str, str] = {
    "Off": "해제",
    "Nano": "무풍",
    "Quiet": "정음",
    "Speed": "스피드",
    "LongWind": "롱바람",
}
PRESET_TO_COMODE = {name: value for value, name in COMODE_OPTIONS.items()}

# 벽걸이의 바람 방향 (`Wind.direction`).
DIRECTION_LABELS: dict[str, str] = {
    "Fix": "고정",
    "Up_And_Low": "상하",
}
SWING_TO_DIRECTION = {name: value for value, name in DIRECTION_LABELS.items()}

# 스탠드의 바람문 상/중/하 (`Blooming_N` 비트마스크, 1=상 2=중 4=하).
# 무풍 중에는 기기가 스스로 0(전부 닫힘)으로 만들기 때문에 0 도 있어야
# 현재 상태를 표시할 수 있다. 삽입 순서가 곧 UI 순서다.
BLOOMING_LABELS: dict[int, str] = {
    0: "닫힘",
    1: "상",
    2: "중",
    4: "하",
    3: "상+중",
    5: "상+하",
    6: "중+하",
    7: "전체",
}
SWING_TO_BLOOMING = {name: mask for mask, name in BLOOMING_LABELS.items()}

BLOOMING_MASK = 0b111


def strip_clean(mode: str) -> tuple[str, bool]:
    """`"CoolClean"` → `("Cool", True)`, `"Cool"` → `("Cool", False)`."""
    if mode.endswith(CLEAN_SUFFIX) and len(mode) > len(CLEAN_SUFFIX):
        return mode[: -len(CLEAN_SUFFIX)], True
    return mode, False


class LocalAirConditioner(LocalAcEntity, ClimateEntity):
    """로컬 API 로 제어하는 에어컨."""

    # 클라우드 엔티티와 같은 기기에 붙으므로 이름으로 구분한다.
    _attr_name = "Local"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_fan_modes = list(SPEED_LEVELS.values())
    _attr_preset_modes = list(COMODE_OPTIONS.values())

    def __init__(self, coordinator: LocalAcCoordinator, device_id: str) -> None:
        """지원 기능을 기기가 알려준 정보에서 결정한다."""
        super().__init__(coordinator, device_id, "climate")

        device = coordinator.data.get(device_id, {})
        self._supported_modes: list[str] = list(
            device.get("Mode", {}).get("supportedModes", [])
        )

        # 청정 조합(`*Clean`)은 접미사를 뗀 기본 모드로 흡수된다.
        hvac_modes: list[HVACMode] = [HVACMode.OFF]
        for raw in self._supported_modes:
            hvac = LOCAL_TO_HVAC.get(strip_clean(raw)[0])
            if hvac is not None and hvac not in hvac_modes:
                hvac_modes.append(hvac)
        self._attr_hvac_modes = hvac_modes

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if "Wind" in device:
            features |= ClimateEntityFeature.FAN_MODE
        if LocalAcCoordinator.get_option(device, "Comode") is not None:
            features |= ClimateEntityFeature.PRESET_MODE

        # 바람문과 바람 방향은 배타적이다. 스탠드는 바람문(`Blooming`)으로
        # 방향을 정하고 `Wind.direction` 은 계속 `Off` 다.
        self._uses_blooming = (
            LocalAcCoordinator.get_option(device, "Blooming") is not None
        )
        if self._uses_blooming:
            self._attr_swing_modes = list(BLOOMING_LABELS.values())
            features |= ClimateEntityFeature.SWING_MODE
        elif "Wind" in device:
            self._attr_swing_modes = list(DIRECTION_LABELS.values())
            features |= ClimateEntityFeature.SWING_MODE

        self._attr_supported_features = features

    # --- 읽기 --------------------------------------------------------------

    @property
    def _temperature(self) -> dict[str, Any]:
        """이 유닛의 온도 정보. 유닛마다 범위가 다르다(스탠드 18~30, 벽걸이 16~30)."""
        temperatures = self.device.get("Temperatures") or [{}]
        return temperatures[0]

    @property
    def _is_on(self) -> bool:
        return self.device.get("Operation", {}).get("power") == "On"

    @property
    def _mode(self) -> str:
        modes = self.device.get("Mode", {}).get("modes") or [""]
        return modes[0]

    @property
    def current_temperature(self) -> float | None:
        """실내 온도."""
        return self._temperature.get("current")

    @property
    def target_temperature(self) -> float | None:
        """희망 온도."""
        return self._temperature.get("desired")

    @property
    def min_temp(self) -> float:
        """기기가 알려주는 하한."""
        return self._temperature.get("minimum", 16)

    @property
    def max_temp(self) -> float:
        """기기가 알려주는 상한."""
        return self._temperature.get("maximum", 30)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """운전 모드. 청정 조합(`CoolClean`)은 기본 모드로 보인다."""
        if not self._is_on:
            return HVACMode.OFF
        return LOCAL_TO_HVAC.get(strip_clean(self._mode)[0])

    @property
    def fan_mode(self) -> str | None:
        """풍량."""
        level = self.device.get("Wind", {}).get("speedLevel")
        if level is None:
            return None
        return SPEED_LEVELS.get(int(level))

    @property
    def preset_mode(self) -> str | None:
        """운전기능."""
        value = LocalAcCoordinator.get_option(self.device, "Comode")
        if value is None:
            return None
        # 목록에 없는 값을 기기가 쓰면 원문을 그대로 보여준다.
        return COMODE_OPTIONS.get(value, value)

    @property
    def swing_mode(self) -> str | None:
        """바람문(스탠드) 또는 바람 방향(벽걸이)."""
        if self._uses_blooming:
            raw = LocalAcCoordinator.get_option(self.device, "Blooming")
            if raw is None or not raw.isdigit():
                return None
            return BLOOMING_LABELS.get(int(raw) & BLOOMING_MASK)
        # `Off`(정지 중)는 선택지가 아니므로 None 이 된다.
        return DIRECTION_LABELS.get(self.device.get("Wind", {}).get("direction"))

    # --- 쓰기 --------------------------------------------------------------

    async def async_turn_on(self) -> None:
        """Turn on."""
        await self._set_power("On")

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self._set_power("Off")

    async def _set_power(self, power: str, *, refresh: bool = True) -> None:
        self.coordinator.apply_optimistic_power(self._device_id, power)
        await self.coordinator.async_send(
            self._device_id, "operation", {"power": power}, refresh=refresh
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """운전 모드 변경."""
        if hvac_mode == HVACMode.OFF:
            await self._set_power("Off")
            return

        # 꺼져 있으면 기기가 설정 명령을 무시하므로 먼저 켠다.
        # 실제로 켜질 때까지 기다린다 — 곧바로 모드를 보내면 무시된다.
        if not self._is_on:
            await self._set_power("On")

        target = HVAC_TO_LOCAL[hvac_mode]
        # 냉방+청정 상태에서 건조로 바꾸면 건조+청정을 유지한다.
        if strip_clean(self._mode)[1]:
            combined = f"{target}{CLEAN_SUFFIX}"
            if combined in self._supported_modes:
                target = combined

        self.coordinator.apply_optimistic_mode(self._device_id, target)
        await self.coordinator.async_send(
            self._device_id, "mode", {"modes": [target]}
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """희망 온도 변경."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # 기기는 정수만 받는다.
        desired = int(round(temperature))
        temperature_id = self._temperature.get("id", "0")

        self.coordinator.apply_optimistic_temperature(self._device_id, desired)
        await self.coordinator.async_send(
            self._device_id, f"temperatures/{temperature_id}", {"desired": desired}
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """풍량 변경.

        무풍이 켜져 있으면 기기가 풍량 변경을 무시하므로 먼저 해제한다.
        """
        level = FAN_TO_LEVEL[fan_mode]

        if LocalAcCoordinator.get_option(self.device, "Comode") == "Nano":
            self.coordinator.apply_optimistic_option(self._device_id, "Comode", "Off")
            await self.coordinator.async_send(
                self._device_id, "mode", {"options": ["Comode_Off"]}, refresh=False
            )

        self.coordinator.apply_optimistic_wind(self._device_id, "speedLevel", level)
        await self.coordinator.async_send(
            self._device_id, "wind", {"speedLevel": level}
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """운전기능 변경."""
        value = PRESET_TO_COMODE[preset_mode]
        self.coordinator.apply_optimistic_option(self._device_id, "Comode", value)
        await self.coordinator.async_send(
            self._device_id, "mode", {"options": [f"Comode_{value}"]}
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """바람문(스탠드) 또는 바람 방향(벽걸이) 변경."""
        if self._uses_blooming:
            mask = SWING_TO_BLOOMING[swing_mode]
            self.coordinator.apply_optimistic_option(
                self._device_id, "Blooming", str(mask)
            )
            await self.coordinator.async_send(
                self._device_id, "mode", {"options": [f"Blooming_{mask}"]}
            )
            return

        direction = SWING_TO_DIRECTION[swing_mode]
        self.coordinator.apply_optimistic_wind(self._device_id, "direction", direction)
        await self.coordinator.async_send(
            self._device_id, "wind", {"direction": direction}
        )
