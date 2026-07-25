"""Config flow for SmartThings Sub A/C."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from . import local_api
from .const import (
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_LOCAL_TOKEN,
    DOMAIN,
    ST_DOMAIN,
)


class SmartThingsSubAcConfigFlow(ConfigFlow, domain=DOMAIN):
    """설정값이 없는 단순 config flow.

    인증은 코어 SmartThings 통합의 것을 그대로 쓰므로 입력받을 것이 없다.
    로컬 API(무풍 등)는 설치 후 옵션에서 설정한다.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if not self.hass.config_entries.async_entries(ST_DOMAIN):
            return self.async_abort(reason="smartthings_not_configured")

        if user_input is None:
            return self.async_show_form(step_id="user")

        return self.async_create_entry(title="SmartThings Sub A/C", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow."""
        return SmartThingsSubAcOptionsFlow()


class SmartThingsSubAcOptionsFlow(OptionsFlow):
    """로컬 API 설정.

    토큰은 `smartthings_subac.local_token` 서비스로 발급받아 여기에 넣는다.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage local API options."""
        if user_input is not None:
            # 빈 값은 저장하지 않는다 (로컬 API 비활성화).
            cleaned = {
                key: value
                for key, value in user_input.items()
                if value not in (None, "")
            }
            return self.async_create_entry(data=cleaned)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LOCAL_HOST,
                    description={"suggested_value": options.get(CONF_LOCAL_HOST)},
                ): str,
                vol.Optional(
                    CONF_LOCAL_TOKEN,
                    description={"suggested_value": options.get(CONF_LOCAL_TOKEN)},
                ): str,
                vol.Optional(
                    CONF_LOCAL_PORT,
                    default=options.get(CONF_LOCAL_PORT, local_api.DEFAULT_PORT),
                ): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
