"""Config flow for Vasco."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .client import DeviceInfo, VascoClient, VascoConnectionError, VascoError
from .const import (
    CONF_ALLOW_WRITES,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PRODUCT_ID_VMD_17RPS01,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(
                CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        }
    )


async def _probe(data: dict[str, Any]) -> DeviceInfo:
    client = VascoClient(data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT_ID])
    try:
        info = await client.read_device_info()
        await client.read_live()
        return info
    finally:
        await client.close()


class VascoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(
        self, user_input: dict[str, Any]
    ) -> tuple[DeviceInfo | None, dict[str, str]]:
        errors: dict[str, str] = {}
        try:
            info = await _probe(user_input)
        except VascoConnectionError:
            errors["base"] = "cannot_connect"
            return None, errors
        except VascoError as err:
            _LOGGER.debug("probe failed: %s", err)
            errors["base"] = "unexpected_response"
            return None, errors
        if info.product_id != PRODUCT_ID_VMD_17RPS01:
            errors["base"] = "unsupported_device"
            return None, errors
        return info, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # A configured bridge is busy (single TCP slot), so match on host first.
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            info, errors = await self._validate(user_input)
            if info is not None:
                await self.async_set_unique_id(str(info.rf_address))
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title="Rekuperator Vasco", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            # The bridge takes a single client: let go of the running connection first.
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.client.release(60)
            info, errors = await self._validate(user_input)
            if info is not None:
                await self.async_set_unique_id(str(info.rf_address))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.client.resume()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VascoOptionsFlow()


class VascoOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=600)),
                    vol.Required(
                        CONF_ALLOW_WRITES, default=opts.get(CONF_ALLOW_WRITES, False)
                    ): bool,
                }
            ),
        )
