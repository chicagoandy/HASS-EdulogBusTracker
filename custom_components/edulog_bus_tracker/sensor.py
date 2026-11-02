"""Diagnostic sensors for Edulog student trips."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SCAN_INTERVAL
from .coordinator import EdulogCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up trip intervals and vehicle-specific next-poll diagnostics."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_tracker_ids: set[str] = set()
    added_vehicle_keys: set[tuple[str, str, str]] = set()

    def add_new_diagnostics() -> None:
        new_entities: list[SensorEntity] = [
            EdulogTripPollingIntervalSensor(coordinator, entry, tracker_id)
            for tracker_id in coordinator.data
            if tracker_id not in added_tracker_ids
        ]
        vehicle_keys = {
            (trip["student_id"], trip["tenant_id"], trip["vehicle_id"])
            for trip in coordinator.data.values()
            if trip.get("vehicle_id")
        }
        new_entities.extend(
            EdulogVehicleNextPollSensor(
                coordinator, entry, student_id, tenant_id, vehicle_id
            )
            for student_id, tenant_id, vehicle_id in vehicle_keys
            if (student_id, tenant_id, vehicle_id) not in added_vehicle_keys
        )
        if new_entities:
            added_tracker_ids.update(
                entity.tracker_id
                for entity in new_entities
                if isinstance(entity, EdulogTripPollingIntervalSensor)
            )
            added_vehicle_keys.update(
                entity.vehicle_key
                for entity in new_entities
                if isinstance(entity, EdulogVehicleNextPollSensor)
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_diagnostics))
    add_new_diagnostics()


class EdulogTripDiagnosticSensor(CoordinatorEntity[EdulogCoordinator], SensorEntity):
    """Base class for diagnostic sensors associated with a student trip."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        tracker_id: str,
        sensor_key: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator)
        trip = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_name = f"{trip['trip_name']} {sensor_name}"
        self._attr_unique_id = f"{entry.entry_id}_{tracker_id}_{sensor_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{trip['student_id']}")},
            name=trip["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def available(self) -> bool:
        """Only report diagnostics while this trip remains in the account."""
        return super().available and self.tracker_id in self.coordinator.data

    @property
    def _trip(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.tracker_id, {})


class EdulogTripPollingIntervalSensor(EdulogTripDiagnosticSensor):
    """Report the current location polling interval for a trip's vehicle."""

    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: EdulogCoordinator, entry: ConfigEntry, tracker_id: str
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            tracker_id,
            "polling_interval",
            "location polling interval",
        )

    @property
    def available(self) -> bool:
        """The configured polling cadence is known independently of API state."""
        return True

    @property
    def native_value(self) -> float:
        """Return the configured location polling interval in seconds."""
        return SCAN_INTERVAL.total_seconds()


class EdulogVehicleNextPollSensor(
    CoordinatorEntity[EdulogCoordinator], SensorEntity
):
    """Report the next eligible location poll time for a student's vehicle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
    ) -> None:
        super().__init__(coordinator)
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
        self.vehicle_key = (student_id, tenant_id, vehicle_id)
        self._attr_name = f"Vehicle {vehicle_id} next eligible location poll"
        self._attr_unique_id = (
            f"{entry.entry_id}_{student_id}_{tenant_id}_{vehicle_id}_next_location_poll"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{student_id}")},
            name=student_trip["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def available(self) -> bool:
        """Only report vehicles still assigned to this student."""
        return super().available and any(
            trip["student_id"] == self.student_id
            and trip["tenant_id"] == self.tenant_id
            and trip["vehicle_id"] == self.vehicle_id
            for trip in self.coordinator.data.values()
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the earliest next poll time for this vehicle."""
        next_polls = [
            trip["next_location_poll"]
            for trip in self.coordinator.data.values()
            if trip["tenant_id"] == self.tenant_id
            and trip["vehicle_id"] == self.vehicle_id
            and isinstance(trip.get("next_location_poll"), datetime)
        ]
        return min(next_polls) if next_polls else None
