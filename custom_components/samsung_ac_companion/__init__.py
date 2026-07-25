"""SmartThings 기기의 서브 컴포넌트를 엔티티로 노출하는 통합.

HA 코어의 SmartThings 통합은 에어컨 엔티티를 `main` 컴포넌트에 대해서만
만든다. 삼성 2 in 1 에어컨처럼 하나의 SmartThings 기기가 여러 컴포넌트를
가지는 경우(스탠드 = main, 벽걸이 = "1") 벽걸이 쪽을 조작할 수 없다.

이 통합은 코어 SmartThings config entry 가 이미 만들어 둔 인증된 클라이언트를
그대로 빌려 쓴다. 따라서 별도의 토큰(PAT)이나 인증 설정이 전혀 필요 없고,
상태 갱신도 코어와 동일한 실시간 push 를 사용한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from pysmartthings import Capability, Command

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from . import local_api
from .coordinator import LocalAcCoordinator
from .const import (
    ATTR_ARGUMENTS,
    ATTR_BODY,
    ATTR_CALLBACK_HOST,
    ATTR_CALLBACK_PORT,
    ATTR_CAPABILITY,
    ATTR_COMMAND,
    ATTR_COMPONENT,
    ATTR_HOST,
    ATTR_HREF,
    ATTR_INCLUDE_RAW,
    ATTR_METHOD,
    ATTR_PARAMS,
    ATTR_PATH,
    ATTR_PORT,
    ATTR_ST_DEVICE_ID,
    ATTR_TOKEN,
    ATTR_WAIT,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_LOCAL_TOKEN,
    DOMAIN,
    SERVICE_API_GET,
    SERVICE_LOCAL_REQUEST,
    SERVICE_LOCAL_TOKEN,
    SERVICE_PROBE_OCF,
    SERVICE_SEND_COMMAND,
    ST_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE]

# 로컬 API 가 설정되어 있을 때만 올리는 플랫폼.
LOCAL_PLATFORMS = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class SubAcData:
    """이 통합의 런타임 데이터.

    `st_entry` 는 코어 SmartThings 의 config entry 다. 실제 클라이언트와
    기기 목록은 그쪽 `runtime_data` 에 있다.
    """

    st_entry: ConfigEntry
    local_coordinator: LocalAcCoordinator | None = None
    platforms: list[Platform] = field(default_factory=list)


type SubAcConfigEntry = ConfigEntry[SubAcData]

PROBE_OCF_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ST_DEVICE_ID): cv.string,
        vol.Required(ATTR_HREF): cv.string,
        vol.Optional(ATTR_COMPONENT, default="main"): cv.string,
        vol.Optional(ATTR_WAIT, default=3.0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=30)
        ),
        vol.Optional(ATTR_INCLUDE_RAW, default=False): cv.boolean,
    }
)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ST_DEVICE_ID): cv.string,
        vol.Required(ATTR_CAPABILITY): cv.string,
        vol.Required(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_COMPONENT, default="main"): cv.string,
        # 리스트 그대로 SmartThings 의 commands.arguments 로 전달된다.
        # execute 의 경우 ["mode/vs/0", {"x.com.samsung.da.options": [...]}] 형태.
        vol.Optional(ATTR_ARGUMENTS): list,
    }
)

LOCAL_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HOST): cv.string,
        vol.Optional(ATTR_PORT, default=local_api.DEFAULT_PORT): cv.port,
        # 생략하면 기기에 도달하는 이쪽 IP 를 자동으로 알아낸다.
        vol.Optional(ATTR_CALLBACK_HOST): cv.string,
        vol.Optional(
            ATTR_CALLBACK_PORT, default=local_api.DEFAULT_CALLBACK_PORT
        ): cv.port,
        vol.Optional(ATTR_WAIT, default=90.0): vol.All(
            vol.Coerce(float), vol.Range(min=10, max=300)
        ),
    }
)

LOCAL_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HOST): cv.string,
        vol.Required(ATTR_TOKEN): cv.string,
        vol.Required(ATTR_PATH): cv.string,
        vol.Optional(ATTR_PORT, default=local_api.DEFAULT_PORT): cv.port,
        vol.Optional(ATTR_METHOD, default="GET"): cv.string,
        vol.Optional(ATTR_BODY): cv.string,
    }
)

API_GET_SCHEMA = vol.Schema(
    {
        # https://api.smartthings.com/ 뒤에 붙는 경로.
        # 예) devices/{deviceId}/presentation
        vol.Required(ATTR_PATH): cv.string,
        vol.Optional(ATTR_PARAMS): dict,
        vol.Optional("accept", default="application/json"): cv.string,
        # 추가/덮어쓰기용 임의 헤더 (Authorization 은 항상 클라이언트 것 사용)
        vol.Optional("headers"): dict,
    }
)


@callback
def _find_loaded_smartthings_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """로드가 끝난 코어 SmartThings config entry 를 찾는다."""
    for entry in hass.config_entries.async_entries(ST_DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


async def async_setup_entry(hass: HomeAssistant, entry: SubAcConfigEntry) -> bool:
    """Set up Samsung AC Companion from a config entry."""
    st_entry = _find_loaded_smartthings_entry(hass)
    if st_entry is None:
        # 코어 통합이 아직 안 올라왔거나 재인증 중인 상태.
        # HA 가 알아서 재시도한다.
        raise ConfigEntryNotReady(
            "SmartThings 통합이 아직 로드되지 않았습니다. 먼저 SmartThings를 설정하세요."
        )

    data = SubAcData(st_entry=st_entry)
    entry.runtime_data = data

    @callback
    def _reload_on_core_unload() -> None:
        """코어 entry 가 언로드되면 우리도 다시 올린다.

        코어가 리로드되면 client 와 devices 객체가 통째로 새로 만들어지므로,
        우리가 붙들고 있던 참조는 낡은 것이 된다. 그대로 두면 명령이 나가지
        않거나 상태가 갱신되지 않는다.
        """
        # 우리 entry 가 먼저 제거된 뒤에 코어가 언로드되는 경우를 방어한다.
        # (async_on_unload 로 등록한 콜백은 개별 해제가 불가능하다)
        if hass.config_entries.async_get_entry(entry.entry_id) is None:
            return
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    st_entry.async_on_unload(_reload_on_core_unload)

    async def _probe_ocf(call: ServiceCall) -> ServiceResponse:
        """`execute` capability 로 OCF 리소스를 읽어본다 (조사용).

        SmartThings 는 execute 결과를 status API 에 저장하지 않는다
        (`/devices/{id}/status` 의 execute.data 는 계속 null 이다).
        결과는 오직 **device event** 로만 흘러오므로, 명령을 보내기 전에
        execute capability 의 이벤트 리스너를 걸어두고 기다려야 한다.
        """
        client = st_entry.runtime_data.client
        device_id: str = call.data[ATTR_ST_DEVICE_ID]
        href: str = call.data[ATTR_HREF]
        component: str = call.data[ATTR_COMPONENT]
        timeout: float = call.data[ATTR_WAIT]

        future: asyncio.Future[dict[str, Any]] = hass.loop.create_future()

        @callback
        def _on_execute_event(event: Any) -> None:
            """execute capability 이벤트 수신."""
            if not future.done():
                future.set_result(
                    {
                        "value": getattr(event, "value", None),
                        "data": getattr(event, "data", None),
                        "attribute": str(getattr(event, "attribute", "")),
                    }
                )

        remove_listener = client.add_device_capability_event_listener(
            device_id, component, Capability.EXECUTE, _on_execute_event
        )

        try:
            try:
                await client.execute_device_command(
                    device_id,
                    Capability.EXECUTE,
                    Command.EXECUTE,
                    component,
                    argument=href,
                )
            except Exception as err:
                raise HomeAssistantError(
                    f"execute 명령 실패 (href={href}, component={component}): {err}"
                ) from err

            try:
                event_result: dict[str, Any] | None = await asyncio.wait_for(
                    future, timeout=timeout
                )
                timed_out = False
            except TimeoutError:
                event_result = None
                timed_out = True
        finally:
            remove_listener()

        result: dict[str, Any] = {
            "href": href,
            "component": component,
            "event": event_result,
            "timed_out": timed_out,
        }

        if call.data[ATTR_INCLUDE_RAW]:
            raw: dict[str, Any] = await client.get_raw_device_status(device_id)
            result["raw"] = raw

        _LOGGER.debug(
            "probe_ocf %s [%s] -> %s (timeout=%s)",
            href,
            component,
            event_result,
            timed_out,
        )
        return result

    async def _send_command(call: ServiceCall) -> ServiceResponse:
        """임의의 SmartThings 명령을 보낸다 (조사/고급 사용자용).

        device profile 에 없는 capability 도 시도해볼 수 있도록 검증 없이
        그대로 전달한다. 실패해도 예외 대신 오류 내용을 응답으로 돌려준다.
        """
        client = st_entry.runtime_data.client
        arguments: list[Any] | None = call.data.get(ATTR_ARGUMENTS)

        try:
            await client.execute_device_command(
                call.data[ATTR_ST_DEVICE_ID],
                call.data[ATTR_CAPABILITY],
                call.data[ATTR_COMMAND],
                call.data[ATTR_COMPONENT],
                argument=arguments,
            )
        except Exception as err:
            return {"success": False, "error": f"{type(err).__name__}: {err}"}

        return {"success": True}

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE_OCF,
        _probe_ocf,
        schema=PROBE_OCF_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _api_get(call: ServiceCall) -> ServiceResponse:
        """인증된 클라이언트로 SmartThings API 에 GET 요청을 보낸다 (조사용).

        pysmartthings 가 래핑하지 않는 엔드포인트(예: device presentation)를
        조회할 때 쓴다. pysmartthings 의 `_get` 은 Accept 헤더를
        `application/vnd.smartthings+json;v=1` 로 하드코딩하는데 presentation
        엔드포인트가 이를 거부하므로(NotAcceptableError), 클라이언트의 세션과
        인증 헤더만 빌려서 `application/json` 으로 직접 요청한다.
        """
        import json as _json

        client = st_entry.runtime_data.client
        path: str = call.data[ATTR_PATH].lstrip("/")
        params: dict[str, Any] | None = call.data.get(ATTR_PARAMS)

        try:
            await client.refresh_token()
            headers = {
                "Accept": call.data["accept"],
                **(call.data.get("headers") or {}),
                **client._get_headers(),  # noqa: SLF001 — Authorization 재사용
            }
            async with asyncio.timeout(30):
                response = await client.session.request(
                    "GET",
                    f"https://api.smartthings.com/{path}",
                    headers=headers,
                    params=params,
                )
                text = await response.text()
        except Exception as err:
            return {"success": False, "error": f"{type(err).__name__}: {err}"}

        try:
            body: Any = _json.loads(text)
        except ValueError:
            body = text

        return {"success": True, "status": response.status, "body": body}

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_GET,
        _api_get,
        schema=API_GET_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _get_local_ssl_context() -> Any:
        """기기용 SSL 컨텍스트 (파일 I/O 라 executor 에서 만든다)."""
        return await hass.async_add_executor_job(
            local_api.build_ssl_context, local_api.cert_path()
        )

    async def _local_token(call: ServiceCall) -> ServiceResponse:
        """로컬 API 토큰을 발급받는다.

        이 서비스가 도는 동안 **에어컨 전원을 껐다 켜야** 기기가 토큰을 보낸다.
        """
        host: str = call.data[ATTR_HOST]
        port: int = call.data[ATTR_PORT]

        callback_host: str = call.data.get(ATTR_CALLBACK_HOST) or (
            await hass.async_add_executor_job(local_api.detect_local_ip, host, port)
        )

        try:
            context = await _get_local_ssl_context()
            # 기기는 콜백을 TLS 로 보내므로 리스너도 TLS 서버여야 한다.
            server_context = await hass.async_add_executor_job(
                local_api.build_server_ssl_context, local_api.cert_path()
            )
        except Exception as err:
            return {"success": False, "error": f"인증서 로드 실패: {err}"}

        try:
            result = await local_api.request_token(
                context,
                host,
                server_context=server_context,
                port=port,
                callback_host=callback_host,
                callback_port=call.data[ATTR_CALLBACK_PORT],
                wait=call.data[ATTR_WAIT],
            )
        except Exception as err:
            return {"success": False, "error": f"{type(err).__name__}: {err}"}

        result["success"] = "token" in result

        # 발급에 성공하면 옵션에 바로 저장한다 (entry 리로드 → 엔티티 생성).
        if token := result.get("token"):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_LOCAL_HOST: host,
                    CONF_LOCAL_PORT: port,
                    CONF_LOCAL_TOKEN: token,
                },
            )
            result["saved_to_options"] = True

        return result

    async def _local_request(call: ServiceCall) -> ServiceResponse:
        """발급받은 토큰으로 로컬 API 를 호출한다."""
        host: str = call.data[ATTR_HOST]
        port: int = call.data[ATTR_PORT]
        path: str = call.data[ATTR_PATH]
        if not path.startswith("/"):
            path = "/" + path

        try:
            context = await _get_local_ssl_context()
        except Exception as err:
            return {"success": False, "error": f"인증서 로드 실패: {err}"}

        try:
            status, headers, body = await local_api.raw_request(
                context,
                host,
                port,
                call.data[ATTR_METHOD],
                path,
                headers={"Authorization": f"Bearer {call.data[ATTR_TOKEN]}"},
                body=call.data.get(ATTR_BODY),
            )
        except Exception as err:
            return {"success": False, "error": f"{type(err).__name__}: {err}"}

        result: dict[str, Any] = {
            "success": 200 <= status < 300,
            "status": status,
            "headers": headers,
        }
        stripped = body.strip()
        try:
            result["body"] = json.loads(stripped) if stripped else None
        except ValueError:
            result["body"] = stripped
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_LOCAL_TOKEN,
        _local_token,
        schema=LOCAL_TOKEN_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LOCAL_REQUEST,
        _local_request,
        schema=LOCAL_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    # --- 로컬 API (선택) ---------------------------------------------------
    # 무풍·미풍처럼 클라우드에 없는 기능은 기기와 직접 통신해야 한다.
    # 옵션에 host/token 이 설정되어 있을 때만 활성화한다.
    platforms = list(PLATFORMS)

    local_host = entry.options.get(CONF_LOCAL_HOST)
    local_token = entry.options.get(CONF_LOCAL_TOKEN)
    if local_host and local_token:
        coordinator = LocalAcCoordinator(
            hass,
            local_host,
            local_token,
            port=entry.options.get(CONF_LOCAL_PORT, local_api.DEFAULT_PORT),
        )
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception:  # noqa: BLE001 — 로컬이 안 되어도 코어 기능은 살린다
            _LOGGER.exception(
                "로컬 API 초기화 실패 — 무풍/풍량 엔티티 없이 계속합니다"
            )
        else:
            data.local_coordinator = coordinator
            platforms += LOCAL_PLATFORMS

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    data.platforms = platforms
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: SubAcConfigEntry) -> None:
    """옵션이 바뀌면 다시 올린다 (로컬 API 설정 반영)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SubAcConfigEntry) -> bool:
    """Unload a config entry."""
    for service in (
        SERVICE_PROBE_OCF,
        SERVICE_SEND_COMMAND,
        SERVICE_API_GET,
        SERVICE_LOCAL_TOKEN,
        SERVICE_LOCAL_REQUEST,
    ):
        hass.services.async_remove(DOMAIN, service)

    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms or PLATFORMS
    )
