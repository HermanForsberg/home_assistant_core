"""Config flow for Mealie."""

from collections.abc import Mapping
import re
from typing import Any, override

from aiomatpriskollen import Matpriskollen, MatpriskollenError
from aiomealie import MealieAuthenticationError, MealieClient, MealieConnectionError
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_TOKEN, CONF_HOST, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import CONF_POSTAL_CODE, DOMAIN, LOGGER, MIN_REQUIRED_MEALIE_VERSION
from .coordinator import MealieConfigEntry
from .utils import create_version

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
    }
)
DISCOVERY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
    }
)
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_POSTAL_CODE): str,
    }
)

EXAMPLE_URL = "http://192.168.1.123:1234"
POSTAL_CODE_PATTERN = re.compile(r"[0-9]{5}")


class MealieConfigFlow(ConfigFlow, domain=DOMAIN):
    """Mealie config flow."""

    VERSION = 1

    host: str | None = None
    verify_ssl: bool = True
    _hassio_discovery: dict[str, Any] | None = None

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: MealieConfigEntry) -> MealieOptionsFlow:
        """Create the options flow."""
        return MealieOptionsFlow()

    async def check_connection(
        self, api_token: str
    ) -> tuple[dict[str, str], str | None]:
        """Check connection to the Mealie API."""
        assert self.host is not None

        if "/app/" in self.host:
            return {"base": "ingress_url"}, None

        client = MealieClient(
            self.host,
            token=api_token,
            session=async_get_clientsession(self.hass, verify_ssl=self.verify_ssl),
        )
        try:
            info = await client.get_user_info()
            about = await client.get_about()
            version = create_version(about.version)
        except MealieConnectionError:
            return {"base": "cannot_connect"}, None
        except MealieAuthenticationError:
            return {"base": "invalid_auth"}, None
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error")
            return {"base": "unknown"}, None
        if version.valid and version < MIN_REQUIRED_MEALIE_VERSION:
            return {"base": "mealie_version"}, None
        return {}, info.user_id

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        if user_input:
            self.host = user_input[CONF_HOST]
            self.verify_ssl = user_input[CONF_VERIFY_SSL]
            errors, user_id = await self.check_connection(
                user_input[CONF_API_TOKEN],
            )
            if not errors:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Mealie",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
            description_placeholders={"example_url": EXAMPLE_URL},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self.host = entry_data[CONF_HOST]
        self.verify_ssl = entry_data.get(CONF_VERIFY_SSL, True)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        errors: dict[str, str] = {}
        if user_input:
            errors, user_id = await self.check_connection(
                user_input[CONF_API_TOKEN],
            )
            if not errors:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"example_url": EXAMPLE_URL},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        if user_input:
            self.host = user_input[CONF_HOST]
            self.verify_ssl = user_input[CONF_VERIFY_SSL]
            errors, user_id = await self.check_connection(
                user_input[CONF_API_TOKEN],
            )
            if not errors:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates={
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=USER_SCHEMA,
            errors=errors,
            description_placeholders={"example_url": EXAMPLE_URL},
        )

    @override
    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Prepare configuration for a Mealie app.

        This flow is triggered by the discovery component.
        """
        await self._async_handle_discovery_without_unique_id()

        self._hassio_discovery = discovery_info.config

        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Supervisor discovery and prompt for API token."""
        if user_input is None:
            return await self._show_hassio_form()

        assert self._hassio_discovery

        self.host = (
            f"{self._hassio_discovery[CONF_HOST]}:{self._hassio_discovery[CONF_PORT]}"
        )
        self.verify_ssl = True

        errors, user_id = await self.check_connection(
            user_input[CONF_API_TOKEN],
        )

        if not errors:
            await self.async_set_unique_id(user_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Mealie",
                data={
                    CONF_HOST: self.host,
                    CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                    CONF_VERIFY_SSL: self.verify_ssl,
                },
            )
        return await self._show_hassio_form(errors)

    async def _show_hassio_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show the Hass.io confirmation form to the user."""
        assert self._hassio_discovery
        return self.async_show_form(
            step_id="hassio_confirm",
            data_schema=DISCOVERY_SCHEMA,
            description_placeholders={"addon": self._hassio_discovery["addon"]},
            errors=errors or {},
        )


class MealieOptionsFlow(OptionsFlowWithReload):
    """Mealie options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the location used for grocery promotions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            postal_code = "".join(user_input.get(CONF_POSTAL_CODE, "").split())
            if not postal_code:
                return self.async_create_entry(data={})
            
            errors = await self._async_validate_postal_code(postal_code)
            if not errors:
                return self.async_create_entry(data={CONF_POSTAL_CODE: postal_code})
    
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )

    async def _async_validate_postal_code(self, postal_code: str) -> dict[str, str]:
        """Check that grocery promotions can be found for the postal code."""
        if not POSTAL_CODE_PATTERN.fullmatch(postal_code):
            return {CONF_POSTAL_CODE: "invalid_postal_code"}
        client = Matpriskollen(async_get_clientsession(self.hass))
        
        try:
            stores = await client.get_stores_from_postal_code(postal_code)
        except MatpriskollenError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error")
            return {"base": "unknown"}
        if not stores:
            return {CONF_POSTAL_CODE: "location_not_found"}
        return {}
