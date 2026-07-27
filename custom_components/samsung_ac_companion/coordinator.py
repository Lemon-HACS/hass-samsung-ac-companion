"""로컬 API 폴링 코디네이터.

`GET /devices` 한 번으로 두 유닛(스탠드/벽걸이)의 상태를 모두 받아온다.
엔티티들은 이 결과를 공유한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import local_api
from .const import DEFAULT_LOCAL_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# 명령을 보낸 뒤 상태를 다시 읽기까지 기다리는 시간(초).
COMMAND_SETTLE_SECONDS = 2.0

# 기기가 명령을 따라올 때까지 지시한 값을 유지하는 최대 시간(초).
#
# 반영 시간이 들쭉날쭉하다 — 전원은 보통 5~6초지만 수십 초에서 몇 분까지
# 늘어질 때가 있다. 그동안 폴링이 돌려주는 옛 값을 그대로 보여주면 화면이
# "꺼짐"으로 되돌아가고, 안 켜진 줄 안 사용자가 다시 누르면 그게 토글이라
# 이번에는 진짜로 꺼진다. 그래서 기기가 따라올 때까지는 지시한 값을 지킨다.
#
# 이 시간이 지나도 반영되지 않으면 기기가 거부한 것으로 보고 실제 값을 보여준다.
PENDING_TIMEOUT_SECONDS = 180.0


@dataclass
class _Pending:
    """기기가 아직 따라오지 못한 명령."""

    apply: Callable[[dict[str, Any]], None]
    """지시한 값을 폴링 결과 위에 다시 씌운다."""

    reached: Callable[[dict[str, Any]], bool]
    """기기가 목표에 도달했는지."""

    expires_at: float


class LocalAcCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """에어컨 로컬 API 상태를 주기적으로 가져온다."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        token: str,
        port: int = local_api.DEFAULT_PORT,
        scan_interval: int = DEFAULT_LOCAL_SCAN_INTERVAL,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name="samsung_ac_companion local",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.host = host
        self.token = token
        self.port = port
        self._ssl_context: ssl.SSLContext | None = None
        # id 0 유닛의 uuid. SmartThings device_id 와 동일하며, 하위 유닛의
        # 기기 identifier 를 만들 때 기준이 된다.
        self.base_uuid: str | None = None
        # 기기가 아직 따라오지 못한 명령. 폴링 결과 위에 다시 씌워진다.
        self._pending: dict[str, _Pending] = {}

    async def _get_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            self._ssl_context = await self.hass.async_add_executor_job(
                local_api.build_ssl_context, local_api.cert_path()
            )
        return self._ssl_context

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """기기 목록을 받아 id 별로 정리한다."""
        context = await self._get_context()
        try:
            status, _, body = await local_api.raw_request(
                context, self.host, self.port, "GET", "/devices",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        except Exception as err:
            raise UpdateFailed(f"로컬 API 연결 실패: {err}") from err

        if status == 401:
            raise UpdateFailed(
                "토큰이 유효하지 않습니다. samsung_ac_companion.local_token 으로 재발급하세요."
            )
        if status != 200:
            raise UpdateFailed(f"예상치 못한 응답: {status}")

        try:
            payload = json.loads(body)
        except ValueError as err:
            raise UpdateFailed(f"응답 파싱 실패: {err}") from err

        devices: dict[str, dict[str, Any]] = {}
        for device in payload.get("Devices", []):
            device_id = str(device.get("id"))
            devices[device_id] = device
            if device_id == "0":
                self.base_uuid = device.get("uuid")

        if not devices:
            raise UpdateFailed("기기 목록이 비어 있습니다")

        self._merge_pending(devices)
        return devices

    def _merge_pending(self, devices: dict[str, dict[str, Any]]) -> None:
        """아직 반영되지 않은 명령을 폴링 결과 위에 다시 씌운다."""
        now = self.hass.loop.time()
        for key, pending in list(self._pending.items()):
            device = devices.get(key.split(":", 1)[0])
            if device is None:
                continue
            if pending.reached(device):
                del self._pending[key]
            elif now >= pending.expires_at:
                # 기기가 끝내 받아들이지 않았다. 이제부터 실제 값을 보여준다.
                del self._pending[key]
                _LOGGER.warning("명령이 반영되지 않았습니다: %s", key)
            else:
                pending.apply(device)

    @callback
    def _optimistic(
        self,
        key: str,
        device_id: str,
        apply: Callable[[dict[str, Any]], None],
        reached: Callable[[dict[str, Any]], bool],
    ) -> None:
        """지시한 값을 즉시 반영하고, 기기가 따라올 때까지 유지한다."""
        device = (self.data or {}).get(device_id)
        if device is None:
            return
        apply(device)
        self._pending[key] = _Pending(
            apply, reached, self.hass.loop.time() + PENDING_TIMEOUT_SECONDS
        )
        self.async_update_listeners()

    async def async_send(
        self,
        device_id: str,
        resource: str,
        payload: dict,
        *,
        refresh: bool = True,
    ) -> None:
        """명령을 보내고 상태를 갱신한다.

        여러 명령을 연달아 보낼 때는 마지막 것만 `refresh=True` 로 두면
        중간 대기(각 2초)를 건너뛸 수 있다.

        기기가 늦게 따라와도 지시한 값은 `_merge_pending` 이 지켜주므로,
        여기서는 한 번만 읽으면 된다.
        """
        context = await self._get_context()
        status, _, body = await local_api.raw_request(
            context,
            self.host,
            self.port,
            "PUT",
            f"/devices/{device_id}/{resource}",
            headers={"Authorization": f"Bearer {self.token}"},
            body=json.dumps(payload),
        )
        if not 200 <= status < 300:
            raise RuntimeError(f"명령 실패 ({status}): {body.strip()}")

        if not refresh:
            return

        # 기기가 값을 보정하거나 아예 무시할 수 있으므로 실제 상태를 다시 읽는다.
        await asyncio.sleep(COMMAND_SETTLE_SECONDS)
        # async_request_refresh 는 debounce 되어 최대 10초를 더 기다린다.
        # 명령 직후에는 곧바로 읽어야 한다.
        await self.async_refresh()

    @callback
    def apply_optimistic_option(
        self, device_id: str, prefix: str, value: str
    ) -> None:
        """`Mode.options` 의 한 항목을 바꾼다."""
        target = f"{prefix}_{value}"

        def apply(device: dict[str, Any]) -> None:
            options = device.get("Mode", {}).get("options")
            if options is None:
                return
            for index, option in enumerate(options):
                if option.startswith(f"{prefix}_"):
                    options[index] = target
                    return
            options.append(target)

        self._optimistic(
            f"{device_id}:option:{prefix}",
            device_id,
            apply,
            lambda device: self.get_option(device, prefix) == value,
        )

    @callback
    def apply_optimistic_wind(self, device_id: str, key: str, value: Any) -> None:
        """`Wind` 의 한 필드를 바꾼다."""

        def apply(device: dict[str, Any]) -> None:
            wind = device.get("Wind")
            if wind is not None:
                wind[key] = value

        self._optimistic(
            f"{device_id}:wind:{key}",
            device_id,
            apply,
            lambda device: device.get("Wind", {}).get(key) == value,
        )

    @callback
    def apply_optimistic_power(self, device_id: str, power: str) -> None:
        """전원을 바꾼다."""

        def apply(device: dict[str, Any]) -> None:
            if "Operation" in device:
                device["Operation"]["power"] = power

        self._optimistic(
            f"{device_id}:power",
            device_id,
            apply,
            lambda device: device.get("Operation", {}).get("power") == power,
        )

    @callback
    def apply_optimistic_mode(self, device_id: str, mode: str) -> None:
        """운전 모드를 바꾼다."""

        def apply(device: dict[str, Any]) -> None:
            if "Mode" in device:
                device["Mode"]["modes"] = [mode]

        self._optimistic(
            f"{device_id}:mode",
            device_id,
            apply,
            lambda device: (device.get("Mode", {}).get("modes") or [None])[0] == mode,
        )

    @callback
    def apply_optimistic_temperature(self, device_id: str, desired: float) -> None:
        """희망 온도를 바꾼다."""

        def apply(device: dict[str, Any]) -> None:
            temperatures = device.get("Temperatures")
            if temperatures:
                temperatures[0]["desired"] = desired

        self._optimistic(
            f"{device_id}:temperature",
            device_id,
            apply,
            lambda device: (device.get("Temperatures") or [{}])[0].get("desired")
            == desired,
        )

    @staticmethod
    def get_option(device: dict[str, Any], prefix: str) -> str | None:
        """`Mode.options` 에서 `prefix_` 로 시작하는 값을 찾는다.

        예: prefix="Comode" → "Comode_Nano" 면 "Nano" 를 돌려준다.
        """
        for option in device.get("Mode", {}).get("options", []):
            if option.startswith(f"{prefix}_"):
                return option[len(prefix) + 1 :]
        return None
