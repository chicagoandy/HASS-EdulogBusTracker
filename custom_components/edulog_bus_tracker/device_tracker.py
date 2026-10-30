"""Device tracker entities for Edulog student trips."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EdulogCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one tracker for each approved student trip."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_tracker_ids: set[str] = set()

    def add_new_students() -> None:
        new_entities = [
            EdulogStudentTracker(coordinator, entry, tracker_id)
            for tracker_id in coordinator.data
            if tracker_id not in added_tracker_ids
        ]
        if new_entities:
            added_tracker_ids.update(entity.tracker_id for entity in new_entities)
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_students))
    add_new_students()


class EdulogStudentTracker(CoordinatorEntity[EdulogCoordinator], TrackerEntity):
    """Represent the location of one trip's assigned bus."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.GPS

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        tracker_id: str,
    ) -> None:
        super().__init__(coordinator)
        trip = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_name = f"{trip['name']} - {trip['trip_name']}"
        self._attr_unique_id = f"{entry.entry_id}_{tracker_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self._attr_name,
            manufacturer="Edulog",
            model="Student trip bus location",
        )

    @property
    def _trip(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.tracker_id, {})

    @property
    def available(self) -> bool:
        """Only report a tracker while its trip remains in the account."""
        return super().available and self.tracker_id in self.coordinator.data

    @property
    def _location(self) -> dict[str, Any] | None:
        location = self._trip.get("location")
        return location if isinstance(location, dict) else None

    @property
    def latitude(self) -> float | None:
        """Return the current latitude."""
        return self._location.get("latitude") if self._location else None

    @property
    def longitude(self) -> float | None:
        """Return the current longitude."""
        return self._location.get("longitude") if self._location else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose vehicle details without including student identifiers."""
        location = self._location
        if not location:
            return {
                "trip": self._trip.get("trip_name"),
                "vehicle_id": self._trip.get("vehicle_id"),
            }
        return {
            "trip": self._trip.get("trip_name"),
            "vehicle_id": location.get("vehicleId"),
            "heading": location.get("heading"),
            "speed": location.get("speed"),
        }
