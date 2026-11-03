"""Binary sensors for Edulog student trips."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up student trip-state and vehicle polling diagnostic sensors."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_vehicle_keys: set[tuple[str, str, str]] = set()
    added_student_ids: set[str] = set()

    def add_new_diagnostics() -> None:
        student_ids = {
            trip["student_id"] for trip in coordinator.data.values()
        }
        vehicle_keys = {
            (
                trip["student_id"],
                trip["tenant_id"],
                trip["vehicle_id"],
            )
            for trip in coordinator.data.values()
            if trip.get("vehicle_id")
        }
        new_entities: list[BinarySensorEntity] = [
            EdulogStudentActiveTripsSensor(coordinator, entry, student_id)
            for student_id in student_ids
            if student_id not in added_student_ids
        ]
        new_entities.extend(
            EdulogVehiclePollingSensor(
                coordinator, entry, student_id, tenant_id, vehicle_id
            )
            for student_id, tenant_id, vehicle_id in vehicle_keys
            if (student_id, tenant_id, vehicle_id) not in added_vehicle_keys
        )
        if new_entities:
            added_student_ids.update(
                entity.student_id
                for entity in new_entities
                if isinstance(entity, EdulogStudentActiveTripsSensor)
            )
            added_vehicle_keys.update(
                entity.vehicle_key
                for entity in new_entities
                if isinstance(entity, EdulogVehiclePollingSensor)
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_diagnostics))
    add_new_diagnostics()


class EdulogStudentActiveTripsSensor(
    CoordinatorEntity[EdulogCoordinator], BinarySensorEntity
):
    """Indicate whether a student has any trip currently being polled."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        student_id: str,
    ) -> None:
        super().__init__(coordinator)
        student_trip = next(
            trip
            for trip in coordinator.data.values()
            if trip["student_id"] == student_id
        )
        self.student_id = student_id
        self._attr_name = "Any trip active"
        self._attr_unique_id = f"{entry.entry_id}_{student_id}_any_trip_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{student_id}")},
            name=student_trip["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def available(self) -> bool:
        """Only report while the student remains in the coordinator data."""
        return super().available and any(
            trip["student_id"] == self.student_id
            for trip in self.coordinator.data.values()
        )

    @property
    def is_on(self) -> bool:
        """Return whether any of the student's trips is in its polling window."""
        return any(
            trip["student_id"] == self.student_id
            and trip.get("polling_active", False)
            for trip in self.coordinator.data.values()
        )


class EdulogVehiclePollingSensor(
    CoordinatorEntity[EdulogCoordinator], BinarySensorEntity
):
    """Indicate when a student's vehicle is actively polled."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sync"

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.vehicle_key = (student_id, tenant_id, vehicle_id)
        student_trip = next(
            trip
            for trip in coordinator.data.values()
            if trip["student_id"] == student_id
            and trip["tenant_id"] == tenant_id
            and trip["vehicle_id"] == vehicle_id
        )
        self.student_id = student_id
        self.tenant_id = tenant_id
        self.vehicle_id = vehicle_id
        self._attr_name = "Location polling"
        self._attr_unique_id = (
            f"{entry.entry_id}_{student_id}_{tenant_id}_{vehicle_id}_location_polling"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{student_id}")},
            name=student_trip["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the vehicle associated with this polling sensor."""
        return {"vehicle_id": self.vehicle_id}

    @property
    def available(self) -> bool:
        """Only report a vehicle while it remains assigned to this student."""
        return super().available and any(
            trip["student_id"] == self.student_id
            and trip["tenant_id"] == self.tenant_id
            and trip["vehicle_id"] == self.vehicle_id
            for trip in self.coordinator.data.values()
        )

    @property
    def is_on(self) -> bool:
        """Return whether this student's vehicle is actively polled."""
        return any(
            trip["tenant_id"] == self.tenant_id
            and trip["vehicle_id"] == self.vehicle_id
            and trip.get("polling_active", False)
            for trip in self.coordinator.data.values()
        )
