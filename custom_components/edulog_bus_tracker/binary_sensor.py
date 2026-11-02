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
    """Set up a polling sensor for each vehicle used by a student."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_vehicle_keys: set[tuple[str, str, str]] = set()

    def add_new_vehicles() -> None:
        vehicle_keys = {
            (
                trip["student_id"],
                trip["tenant_id"],
                trip["vehicle_id"],
            )
            for trip in coordinator.data.values()
            if trip.get("vehicle_id")
        }
        new_entities = [
            EdulogVehiclePollingSensor(
                coordinator, entry, student_id, tenant_id, vehicle_id
            )
            for student_id, tenant_id, vehicle_id in vehicle_keys
            if (student_id, tenant_id, vehicle_id) not in added_vehicle_keys
        ]
        if new_entities:
            added_vehicle_keys.update(entity.vehicle_key for entity in new_entities)
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_vehicles))
    add_new_vehicles()


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
        self._attr_name = f"Vehicle {vehicle_id} location polling"
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
