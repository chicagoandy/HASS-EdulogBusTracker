"""Device tracker entities for Edulog student trips."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import EdulogConfigEntry
from .const import DOMAIN
from .coordinator import EdulogCoordinator

_VEHICLE_NAME_PREFIX = "Vehicle "


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulogConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one tracker for each vehicle assigned to an approved student."""
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    added_tracker_ids: set[str] = set()
    used_numbers_by_student: dict[str, set[int]] = {}

    def registered_number(tracker_id: str) -> int | None:
        """Return the number this tracker was first registered with, if any.

        This reads the tracker's own registry entry and changes nothing:
        original_name is what the integration set, so a user's rename or
        entity ID change does not affect it.
        """
        entity_id = registry.async_get_entity_id(
            Platform.DEVICE_TRACKER, DOMAIN, f"{entry.entry_id}_{tracker_id}"
        )
        if entity_id is None or (entity := registry.async_get(entity_id)) is None:
            return None
        suffix = (entity.original_name or "").removeprefix(_VEHICLE_NAME_PREFIX)
        return int(suffix) if suffix.isdecimal() and int(suffix) > 0 else None

    def add_new_vehicles() -> None:
        new_vehicles = sorted(
            (
                (tracker_id, vehicle)
                for tracker_id, vehicle in coordinator.data.items()
                if vehicle.get("vehicle_id") and tracker_id not in added_tracker_ids
            ),
            key=lambda item: (
                item[1]["student_id"],
                item[1]["tenant_id"],
                item[1]["vehicle_id"],
            ),
        )
        # Reserve the numbers of already registered trackers first, so a new
        # vehicle never takes the number of one that is known but unassigned.
        numbers = {
            tracker_id: registered_number(tracker_id)
            for tracker_id, _vehicle in new_vehicles
        }
        for tracker_id, vehicle in new_vehicles:
            if (number := numbers[tracker_id]) is not None:
                used_numbers_by_student.setdefault(
                    vehicle["student_id"], set()
                ).add(number)

        new_entities: list[EdulogVehicleTracker] = []
        for tracker_id, vehicle in new_vehicles:
            used = used_numbers_by_student.setdefault(vehicle["student_id"], set())
            number = numbers[tracker_id]
            if number is None:
                number = next(n for n in range(1, len(used) + 2) if n not in used)
                used.add(number)
            new_entities.append(
                EdulogVehicleTracker(coordinator, entry, tracker_id, number)
            )
        if new_entities:
            added_tracker_ids.update(entity.tracker_id for entity in new_entities)
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_vehicles))
    add_new_vehicles()


class EdulogVehicleTracker(CoordinatorEntity[EdulogCoordinator], TrackerEntity):
    """Represent the location of one expected vehicle."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.GPS

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        tracker_id: str,
        vehicle_number: int,
    ) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_name = f"{_VEHICLE_NAME_PREFIX}{vehicle_number}"
        # Suggest a generic, account/student-scoped entity ID for new trackers
        # so the child's name is not part of it. Home Assistant only uses this
        # on first registration; afterwards the registry (and any ID the user
        # chose) wins, so nothing here rewrites existing entities.
        self.entity_id = (
            f"{Platform.DEVICE_TRACKER}."
            f"{_suggested_object_id(entry, vehicle['student_id'], vehicle_number)}"
        )
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


def _suggested_object_id(
    entry: EdulogConfigEntry, student_id: str, vehicle_number: int
) -> str:
    """Return a stable, generic entity ID scoped to this account and student."""
    return slugify(
        f"edulog_{entry.unique_id or entry.entry_id}_"
        f"{student_id}_vehicle_{vehicle_number}"
    )
