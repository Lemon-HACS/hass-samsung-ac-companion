"""SmartThings 기기의 서브 컴포넌트를 엔티티로 노출하는 통합.

HA 코어의 SmartThings 통합은 에어컨 엔티티를 `main` 컴포넌트에 대해서만
만든다. 삼성 2 in 1 에어컨처럼 하나의 SmartThings 기기가 여러 컴포넌트를
가지는 경우(스탠드 = main, 벽걸이 = "1") 벽걸이 쪽을 조작할 수 없다.

이 통합은 코어 SmartThings config entry 가 이미 만들어 둔 인증된 클라이언트를
그대로 빌려 쓴다. 따라서 별도의 토큰(PAT)이나 인증 설정이 전혀 필요 없고,
상태 갱신도 코어와 동일한 실시간 push 를 사용한다.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import ST_DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE]

# runtime_data 로 코어 SmartThings 의 config entry 를 들고 있는다.
# 실제 데이터(client, devices)는 그쪽 entry.runtime_data 에 있다.
type SubAcConfigEntry = ConfigEntry[ConfigEntry]


@callback
def _find_loaded_smartthings_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """로드가 끝난 코어 SmartThings config entry 를 찾는다."""
    for entry in hass.config_entries.async_entries(ST_DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


async def async_setup_entry(hass: HomeAssistant, entry: SubAcConfigEntry) -> bool:
    """Set up SmartThings Sub A/C from a config entry."""
    st_entry = _find_loaded_smartthings_entry(hass)
    if st_entry is None:
        # 코어 통합이 아직 안 올라왔거나 재인증 중인 상태.
        # HA 가 알아서 재시도한다.
        raise ConfigEntryNotReady(
            "SmartThings 통합이 아직 로드되지 않았습니다. 먼저 SmartThings를 설정하세요."
        )

    entry.runtime_data = st_entry

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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SubAcConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
