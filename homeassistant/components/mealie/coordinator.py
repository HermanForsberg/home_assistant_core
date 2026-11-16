"""Define an object to manage fetching Mealie data."""

from abc import abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import override

from aiomatpriskollen import Matpriskollen, MatpriskollenError, Offer, Store
from aiomealie import (
    MealieAuthenticationError,
    MealieClient,
    MealieConnectionError,
    Mealplan,
    MealplanEntryType,
    ShoppingItem,
    ShoppingList,
    Statistics,
)
from awesomeversion import AwesomeVersion

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER

WEEK = timedelta(days=7)
PROMOTIONS_UPDATE_INTERVAL = timedelta(days=1)


@dataclass
class MealieData:
    """Mealie data type."""

    client: MealieClient
    version: AwesomeVersion
    mealplan_coordinator: MealieMealplanCoordinator
    shoppinglist_coordinator: MealieShoppingListCoordinator
    statistics_coordinator: MealieStatisticsCoordinator
    promotions_coordinator: MealiePromotionsCoordinator | None


type MealieConfigEntry = ConfigEntry[MealieData]


class MealieDataUpdateCoordinator[_DataT](DataUpdateCoordinator[_DataT]):
    """Base coordinator."""

    config_entry: MealieConfigEntry
    _name: str
    _update_interval: timedelta

    def __init__(
        self, hass: HomeAssistant, config_entry: MealieConfigEntry, client: MealieClient
    ) -> None:
        """Initialize the Mealie data coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"Mealie {self._name}",
            update_interval=self._update_interval,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> _DataT:
        """Fetch data from Mealie."""
        try:
            return await self._async_update_internal()
        except MealieAuthenticationError as error:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from error
        except MealieConnectionError as error:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key=f"update_failed_{self._name}",
            ) from error

    @abstractmethod
    async def _async_update_internal(self) -> _DataT:
        """Fetch data from Mealie."""


class MealieMealplanCoordinator(
    MealieDataUpdateCoordinator[dict[MealplanEntryType, list[Mealplan]]]
):
    """Class to manage fetching Mealie data."""

    _name = "mealplan"
    _update_interval = timedelta(hours=1)

    @override
    async def _async_update_internal(self) -> dict[MealplanEntryType, list[Mealplan]]:
        next_week = dt_util.now() + WEEK
        current_date = dt_util.now().date()
        next_week_date = next_week.date()
        response = await self.client.get_mealplans(current_date, next_week_date)
        res: dict[MealplanEntryType, list[Mealplan]] = {
            type_: [] for type_ in MealplanEntryType
        }
        for meal in response.items:
            res[meal.entry_type].append(meal)
        return res


@dataclass
class ShoppingListData:
    """Data class for shopping list data."""

    shopping_list: ShoppingList
    items: list[ShoppingItem]


class MealieShoppingListCoordinator(
    MealieDataUpdateCoordinator[dict[str, ShoppingListData]]
):
    """Class to manage fetching Mealie Shopping list data."""

    _name = "shopping_list"
    _update_interval = timedelta(minutes=5)

    @override
    async def _async_update_internal(
        self,
    ) -> dict[str, ShoppingListData]:
        shopping_list_items = {}
        shopping_lists = (await self.client.get_shopping_lists()).items
        for shopping_list in shopping_lists:
            shopping_list_id = shopping_list.list_id

            shopping_items = (
                await self.client.get_shopping_items(shopping_list_id)
            ).items

            shopping_list_items[shopping_list_id] = ShoppingListData(
                shopping_list=shopping_list, items=shopping_items
            )
        return shopping_list_items


class MealieStatisticsCoordinator(MealieDataUpdateCoordinator[Statistics]):
    """Class to manage fetching Mealie Statistics data."""

    _name = "statistics"
    _update_interval = timedelta(minutes=15)

    @override
    async def _async_update_internal(
        self,
    ) -> Statistics:
        return await self.client.get_statistics()


@dataclass
class StorePromotions:
    """Promotions offered by a single store."""

    store: Store
    offers: list[Offer]


class MealiePromotionsCoordinator(DataUpdateCoordinator[list[StorePromotions] | None]):
    """Class to manage fetching grocery promotions near the configured location."""

    config_entry: MealieConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MealieConfigEntry,
        client: Matpriskollen,
        postal_code: str,
    ) -> None:
        """Initialize the promotions coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="Mealie promotions",
            update_interval=PROMOTIONS_UPDATE_INTERVAL,
        )
        self.client = client
        self.postal_code = postal_code

    @override
    async def _async_update_data(self) -> list[StorePromotions]:
        """Fetch the promotions of the stores near the configured location."""
        try:
            stores = await self.client.get_stores_from_postal_code(self.postal_code)
            return [
                StorePromotions(
                    store, await self.client.get_deals_from_store(store.uuid)
                )
                for store in stores
            ]
        except MatpriskollenError as error:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed_promotions",
            ) from error
