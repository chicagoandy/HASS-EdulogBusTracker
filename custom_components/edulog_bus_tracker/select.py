"""Select entities that choose which stop centres each trip's geofences."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EdulogConfigEntry
from .const import DOMAIN, FENCE_CENTER_OPTIONS
from .coordinator import EdulogCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulogConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one fence-centre selector for each trip with a vehicle."""
    coordinator = entry.runtime_data
    added_trip_keys: set[str] = set()

    def add_new_trips() -> None:
        new_entities: list[EdulogFenceCenterSelect] = []
        for tracker in coordinator.data.values():
            for fence_trip in tracker.get("fence_trips", []):
                if fence_trip["key"] in added_trip_keys:
                    continue
                added_trip_keys.add(fence_trip["key"])
                new_entities.append(
                    EdulogFenceCenterSelect(
                        coordinator,
                        entry,
                        tracker["student_id"],
                        tracker["name"],
                        fence_trip["key"],
                        fence_trip["name"],
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_trips))
    add_new_trips()


class EdulogFenceCenterSelect(CoordinatorEntity[EdulogCoordinator], SelectEntity):
    """Choose whether a trip's geofences centre on its pickup or drop-off."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:map-marker-radius"
    _attr_options = FENCE_CENTER_OPTIONS
    _attr_translation_key = "fence_center"

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        student_id: str,
        student_name: str,
        trip_key: str,
        trip_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.trip_key = trip_key
        self._attr_name = f"{trip_name} fence center"
        self._attr_unique_id = f"{entry.entry_id}_{trip_key}_fence_center"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{student_id}")},
            name=student_name,
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def _trip(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return this trip's fence entry and its trip details, if assigned."""
        for tracker in self.coordinator.data.values():
            for fence_trip, details in zip(
                tracker.get("fence_trips", []), tracker.get("trips", [])
            ):
                if fence_trip["key"] == self.trip_key:
                    return fence_trip, details
        return None

    @property
    def available(self) -> bool:
        """Only report while the trip remains assigned."""
        return super().available and self._trip is not None

    @property
    def current_option(self) -> str | None:
        """Return the stop currently centring this trip's fences."""
        trip = self._trip
        trip_type = trip[1].get("trip_type") if trip else None
        return self.coordinator.fence_center(self.trip_key, trip_type)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Report whether the API supplied coordinates for the chosen stop."""
        trip = self._trip
        return {
            "stop_located": (
                bool(trip[1].get("fence_center_located")) if trip else None
            )
        }

    async def async_select_option(self, option: str) -> None:
        """Store the choice; the next coordinator update applies it."""
        self.coordinator.async_set_fence_center(self.trip_key, option)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
