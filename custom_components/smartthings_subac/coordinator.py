"""로컬 API 폴링 코디네이터.

`GET /devices` 한 번으로 두 유닛(스탠드/벽걸이)의 상태를 모두 받아온다.
엔티티들은 이 결과를 공유한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import local_api
from .const import DEFAULT_LOCAL_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# 명령을 보낸 뒤 기기가 반영할 때까지 기다리는 시간(초).
# 이보다 빨리 읽으면 아직 옛 값이 돌아와 낙관적 반영이 지워진다.
COMMAND_SETTLE_SECONDS = 2.0


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
            name="smartthings_subac local",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.host = host
        self.token = token
        self.port = port
        self._ssl_context: ssl.SSLContext | None = None
        # id 0 유닛의 uuid. SmartThings device_id 와 동일하며, 하위 유닛의
        # 기기 identifier 를 만들 때 기준이 된다.
        self.base_uuid: str | None = None

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
                "토큰이 유효하지 않습니다. smartthings_subac.local_token 으로 재발급하세요."
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
        return devices

    async def async_send(self, device_id: str, resource: str, payload: dict) -> None:
        """명령을 보내고 즉시 상태를 갱신한다."""
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

        # 기기가 값을 보정하거나 아예 무시할 수 있으므로 실제 상태를 다시 읽는다.
        # 다만 곧바로 읽으면 아직 반영 전이라 낙관적 값이 옛 값으로 덮어써진다.
        # 기기가 반영할 시간을 준 뒤에 읽는다.
        await asyncio.sleep(COMMAND_SETTLE_SECONDS)
        await self.async_request_refresh()

    @callback
    def apply_optimistic_option(
        self, device_id: str, prefix: str, value: str
    ) -> None:
        """`Mode.options` 값을 즉시 바꿔치기하고 리스너를 깨운다.

        명령을 보내도 기기 반영과 다음 폴링까지 시간이 걸려서, 그동안 UI 가
        예전 상태로 되돌아간 것처럼 보인다. 낙관적으로 먼저 반영해 두고
        실제 값은 뒤이은 refresh 가 덮어쓴다.
        """
        device = (self.data or {}).get(device_id)
        if not device:
            return
        options = device.get("Mode", {}).get("options")
        if options is None:
            return

        target = f"{prefix}_{value}"
        for index, option in enumerate(options):
            if option.startswith(f"{prefix}_"):
                options[index] = target
                break
        else:
            options.append(target)
        self.async_update_listeners()

    @callback
    def apply_optimistic_wind(self, device_id: str, key: str, value: Any) -> None:
        """`Wind` 값을 즉시 바꿔치기하고 리스너를 깨운다."""
        device = (self.data or {}).get(device_id)
        if not device:
            return
        wind = device.get("Wind")
        if wind is None:
            return
        wind[key] = value
        self.async_update_listeners()

    @staticmethod
    def get_option(device: dict[str, Any], prefix: str) -> str | None:
        """`Mode.options` 에서 `prefix_` 로 시작하는 값을 찾는다.

        예: prefix="Comode" → "Comode_Nano" 면 "Nano" 를 돌려준다.
        """
        for option in device.get("Mode", {}).get("options", []):
            if option.startswith(f"{prefix}_"):
                return option[len(prefix) + 1 :]
        return None
