"""Edulog Bus Tracker integration."""

from __future__ import annotations

import logging
from math import isfinite

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulogApi, EdulogHttpError
from .const import (
    CONF_ZONE_DIAMETER_MILES,
    DEFAULT_ZONE_DIAMETER_MILES,
    DOMAIN,
)
from .coordinator import EdulogCoordinator
from .zones import EdulogZones

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Edulog from a config entry."""
    _LOGGER.info("Setting up Edulog Bus Tracker integration")

    def update_tokens(access_token: str, refresh_token: str) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "auth_token": access_token,
                "refresh_token": refresh_token,
            },
        )

    api = EdulogApi(
        async_get_clientsession(hass),
        entry.data.get("auth_token"),
        user_id=entry.unique_id,
        refresh_token=entry.data.get("refresh_token"),
        token_update_callback=update_tokens,
    )
    zone_diameter_miles = entry.options.get(
        CONF_ZONE_DIAMETER_MILES, DEFAULT_ZONE_DIAMETER_MILES
    )
    if (
        isinstance(zone_diameter_miles, bool)
        or not isinstance(zone_diameter_miles, (int, float))
        or not isfinite(zone_diameter_miles)
        or zone_diameter_miles <= 0
    ):
        raise ConfigEntryNotReady(
            "The configured pickup/drop-off zone diameter must be positive."
        )
    zones = EdulogZones(
        hass, entry.entry_id, float(zone_diameter_miles)
    )
    await zones.async_load()
    coordinator = EdulogCoordinator(
        hass,
        api,
        entry.entry_id,
        float(zone_diameter_miles),
        zones,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except EdulogHttpError as err:
        raise ConfigEntryNotReady("Edulog API is temporarily unavailable") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady("Could not connect to Edulog") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Edulog config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove pickup and drop-off zones created by this entry."""
    zones = EdulogZones(
        hass, entry.entry_id, DEFAULT_ZONE_DIAMETER_MILES
    )
    await zones.async_load()
    await zones.async_remove_owned_zones()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config-entry data formats."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={},
            version=2,
        )
        return True
    if entry.version == 2:
        hass.config_entries.async_update_entry(
            entry,
            data={"auth_token": entry.data["auth_token"]}
            if entry.data.get("auth_token")
            else {},
            version=3,
        )
        return True
    if entry.version == 3:
        hass.config_entries.async_update_entry(
            entry,
            data=dict(entry.data),
            version=4,
        )
        return True

    return False
