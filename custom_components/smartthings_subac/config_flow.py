"""Config flow for SmartThings Sub A/C."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, ST_DOMAIN


class SmartThingsSubAcConfigFlow(ConfigFlow, domain=DOMAIN):
    """설정값이 없는 단순 config flow.

    인증은 코어 SmartThings 통합의 것을 그대로 쓰므로 입력받을 것이 없다.
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
