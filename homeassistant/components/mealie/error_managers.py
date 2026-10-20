"""Expception wrappers for Mealie error types."""

from collections.abc import Generator
from contextlib import contextmanager

from aiomealie import MealieConnectionError

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


@contextmanager
def handle_connection_error() -> Generator[None]:
    """Translate Mealie connection errors into HomeAssistantError."""
    try:
        yield
    except MealieConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="connection_error",
        ) from err
