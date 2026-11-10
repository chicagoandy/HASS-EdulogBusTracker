"""Manage map-visible pickup and drop-off zones."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.zone import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
)
from homeassistant.components.zone.const import CONF_PASSIVE, DATA_ZONE
from homeassistant.const import CONF_ICON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, METERS_PER_MILE

if TYPE_CHECKING:
    from homeassistant.components.zone import ZoneData

_STORAGE_VERSION = 1
_ZONE_ICON = "mdi:bus-stop"


class EdulogZones:
    """Create, update, and remove this entry's persisted Home Assistant zones."""

    def __init__(
        self, hass: HomeAssistant, config_entry_id: str, diameter_miles: float
    ) -> None:
        self.hass = hass
        self._radius_meters = diameter_miles * METERS_PER_MILE / 2
        self._store: Store[dict[str, dict[str, str]]] = Store(
            hass, _STORAGE_VERSION, f"{DOMAIN}_zones_{config_entry_id}"
        )
        self._zone_ids: dict[str, str] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load the IDs of zones owned by this integration entry."""
        stored = await self._store.async_load()
        zones = stored.get("zones") if isinstance(stored, dict) else None
        self._zone_ids = (
            {
                key: zone_id
                for key, zone_id in zones.items()
                if isinstance(key, str) and isinstance(zone_id, str)
            }
            if isinstance(zones, dict)
            else {}
        )
        self._loaded = True

    async def async_sync(
        self, definitions: dict[str, tuple[str, float, float]]
    ) -> None:
        """Make persisted zones match the current trip stop coordinates."""
        if not self._loaded:
            raise RuntimeError("Edulog zones must be loaded before syncing.")

        zone_data: ZoneData = self.hass.data[DATA_ZONE]
        collection = zone_data.storage_collection
        existing_items = {
            item["id"]: item for item in collection.async_items()
        }
        changed = False

        for key in self._zone_ids.keys() - definitions.keys():
            zone_id = self._zone_ids.pop(key)
            changed = True
            if zone_id in existing_items:
                await collection.async_delete_item(zone_id)

        for key, (name, latitude, longitude) in definitions.items():
            zone_id = self._zone_ids.get(key)
            item = existing_items.get(zone_id) if zone_id else None
            updates: dict[str, Any] = {
                CONF_NAME: name,
                CONF_LATITUDE: latitude,
                CONF_LONGITUDE: longitude,
                CONF_RADIUS: self._radius_meters,
                CONF_ICON: _ZONE_ICON,
                CONF_PASSIVE: True,
            }
            if item is None:
                item = await collection.async_create_item(updates)
                self._zone_ids[key] = item["id"]
                changed = True
            else:
                zone_id = item["id"]
                if any(
                    item.get(field) != value for field, value in updates.items()
                ):
                    await collection.async_update_item(zone_id, updates)
                    changed = True

        if changed:
            await self._async_save()

    async def async_remove_owned_zones(self) -> None:
        """Delete zones created for this config entry."""
        if not self._loaded:
            await self.async_load()

        zone_data: ZoneData = self.hass.data[DATA_ZONE]
        existing_ids = {
            item["id"] for item in zone_data.storage_collection.async_items()
        }
        for zone_id in self._zone_ids.values():
            if zone_id in existing_ids:
                await zone_data.storage_collection.async_delete_item(zone_id)
        self._zone_ids.clear()
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save({"zones": self._zone_ids})
