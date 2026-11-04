"""Device tracker entities for Edulog student trips."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EdulogCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one tracker for each vehicle assigned to an approved student."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_tracker_ids: set[str] = set()

    def add_new_vehicles() -> None:
        new_entities = [
            EdulogVehicleTracker(coordinator, entry, tracker_id)
            for tracker_id, trip in coordinator.data.items()
            if trip.get("vehicle_id") and tracker_id not in added_tracker_ids
        ]
        if new_entities:
            added_tracker_ids.update(entity.tracker_id for entity in new_entities)
            async_add_entities(new_entities)

    registry = async_get_entity_registry(hass)
    active_unique_ids = {
        f"{entry.entry_id}_{tracker_id}"
        for tracker_id, trip in coordinator.data.items()
        if trip.get("vehicle_id")
    }
    for entity in tuple(registry.entities.values()):
        if (
            entity.config_entry_id == entry.entry_id
            and entity.platform == DOMAIN
            and entity.entity_id.startswith("device_tracker.")
            and entity.unique_id not in active_unique_ids
        ):
            registry.async_remove(entity.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(add_new_vehicles))
    add_new_vehicles()


class EdulogVehicleTracker(CoordinatorEntity[EdulogCoordinator], TrackerEntity):
    """Represent the location of one expected vehicle."""

    _attr_has_entity_name = True
    _attr_entity_category = None
    _attr_source_type = SourceType.GPS

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        tracker_id: str,
    ) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_name = f"Vehicle {vehicle['vehicle_id']}"
        self._attr_unique_id = f"{entry.entry_id}_{tracker_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{vehicle['student_id']}")},
            name=vehicle["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def _vehicle(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.tracker_id, {})

    @property
    def available(self) -> bool:
        """Only report a tracker while its vehicle remains assigned."""
        return super().available and self.tracker_id in self.coordinator.data

    @property
    def _location(self) -> dict[str, Any] | None:
        location = self._vehicle.get("location")
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
        """Expose the trips associated with this vehicle and its details."""
        location = self._location
        trip_names = self._vehicle.get("trip_names", [])
        attributes = {
            "trip": ", ".join(trip_names) if trip_names else None,
            "trips": self._vehicle.get("trips", []),
            "vehicle_id": (
                location.get("vehicleId")
                if location
                else self._vehicle.get("vehicle_id")
            ),
            "student_first_name": self._vehicle.get("student_first_name"),
            "student_last_name": self._vehicle.get("student_last_name"),
            "trip_type": self._vehicle.get("trip_type"),
            "pickup_stop_name": self._vehicle.get("pickup_stop_name"),
            "pickup_time": self._vehicle.get("pickup_time"),
            "dropoff_stop_name": self._vehicle.get("dropoff_stop_name"),
            "dropoff_time": self._vehicle.get("dropoff_time"),
        }
        if location:
            attributes["heading"] = location.get("heading")
            attributes["speed"] = location.get("speed")
        return attributes
