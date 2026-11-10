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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

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
    registry = async_get_entity_registry(hass)
    vehicle_numbers: dict[str, int] = {}
    next_number_by_student: dict[str, int] = {}
    number_store: Store[dict[str, dict[str, str | int]]] = Store(
        hass, 1, f"{DOMAIN}_vehicle_numbers_{entry.entry_id}"
    )
    stored_numbers = await number_store.async_load()
    number_assignments: dict[str, tuple[str, int]] = {}
    if isinstance(stored_numbers, dict):
        for tracker_id, assignment in stored_numbers.items():
            if not isinstance(tracker_id, str) or not isinstance(assignment, dict):
                continue
            student_id = assignment.get("student_id")
            number = assignment.get("number")
            if (
                isinstance(student_id, str)
                and isinstance(number, int)
                and not isinstance(number, bool)
                and number > 0
            ):
                number_assignments[tracker_id] = (student_id, number)

    def assign_vehicle_numbers() -> list[tuple[str, dict[str, Any]]]:
        assignments_changed = False
        vehicle_items = sorted(
            (
                (tracker_id, vehicle)
                for tracker_id, vehicle in coordinator.data.items()
                if vehicle.get("vehicle_id")
            ),
            key=lambda item: (
                item[1]["student_id"],
                item[1]["tenant_id"],
                item[1]["vehicle_id"],
            ),
        )
        used_numbers_by_student: dict[str, set[int]] = {}
        for student_id, number in number_assignments.values():
            used_numbers_by_student.setdefault(student_id, set()).add(number)

        vehicles_by_unique_id = {
            f"{entry.entry_id}_{tracker_id}": (tracker_id, vehicle)
            for tracker_id, vehicle in vehicle_items
        }
        for entity in registry.entities.values():
            if (
                entity.config_entry_id != entry.entry_id
                or entity.platform != DOMAIN
            ):
                continue
            vehicle_match = vehicles_by_unique_id.get(entity.unique_id)
            if vehicle_match is None:
                continue
            tracker_id, vehicle = vehicle_match
            if tracker_id in number_assignments:
                continue
            object_id_prefix = _suggested_object_id(
                entry, vehicle["student_id"], 1
            ).rsplit("_vehicle_", 1)[0]
            entity_id_prefix = f"device_tracker.{object_id_prefix}_vehicle_"
            suffix = entity.entity_id.removeprefix(entity_id_prefix)
            number = int(suffix) if suffix.isdecimal() else 0
            if not number:
                original_name = getattr(entity, "original_name", None)
                name = original_name or entity.name
                if not isinstance(name, str) or not name.startswith("Vehicle "):
                    continue
                suffix = name.removeprefix("Vehicle ")
                if not suffix.isdecimal():
                    continue
                number = int(suffix)
            student_id = vehicle["student_id"]
            used_numbers = used_numbers_by_student.setdefault(student_id, set())
            if number > 0 and number not in used_numbers:
                number_assignments[tracker_id] = (student_id, number)
                used_numbers.add(number)
                assignments_changed = True
                next_number_by_student[student_id] = max(
                    next_number_by_student.get(student_id, 0), number
                )

        for tracker_id, vehicle in vehicle_items:
            assignment = number_assignments.get(tracker_id)
            if (
                assignment is not None
                and assignment[0] == vehicle["student_id"]
            ):
                vehicle_numbers[tracker_id] = assignment[1]
                continue
            student_id = vehicle["student_id"]
            used_numbers = used_numbers_by_student.setdefault(student_id, set())
            number = next_number_by_student.get(student_id, 0) + 1
            while number in used_numbers:
                number += 1
            number_assignments[tracker_id] = (student_id, number)
            used_numbers.add(number)
            vehicle_numbers[tracker_id] = number
            next_number_by_student[student_id] = number
            assignments_changed = True
        if assignments_changed:
            assignments_to_save = {
                key: {
                    "student_id": assignment[0],
                    "number": assignment[1],
                }
                for key, assignment in number_assignments.items()
            }
            hass.async_create_task(
                number_store.async_save(assignments_to_save)
            )
        return vehicle_items

    def add_new_vehicles() -> None:
        vehicle_items = assign_vehicle_numbers()
        new_entities = [
            EdulogVehicleTracker(
                coordinator, entry, tracker_id, vehicle_numbers[tracker_id]
            )
            for tracker_id, _vehicle in vehicle_items
            if tracker_id not in added_tracker_ids
        ]
        if new_entities:
            added_tracker_ids.update(entity.tracker_id for entity in new_entities)
            async_add_entities(new_entities)

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
        vehicle_number: int,
    ) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_name = f"Vehicle {vehicle_number}"
        self._attr_suggested_object_id = _suggested_object_id(
            entry, vehicle["student_id"], vehicle_number
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
    entry: ConfigEntry, student_id: str, vehicle_number: int
) -> str:
    """Return a stable, generic entity ID scoped to this account and student."""
    return slugify(
        f"edulog_{entry.unique_id or entry.entry_id}_"
        f"{student_id}_vehicle_{vehicle_number}"
    )
