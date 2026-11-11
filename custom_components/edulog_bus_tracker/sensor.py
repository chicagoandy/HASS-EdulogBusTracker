"""Diagnostic sensors for Edulog student trips."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EdulogConfigEntry
from .const import DOMAIN, SCAN_INTERVAL
from .coordinator import EdulogCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulogConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostics for each student vehicle."""
    coordinator = entry.runtime_data
    added_vehicle_keys: set[tuple[str, str, str]] = set()

    def add_new_diagnostics() -> None:
        vehicle_keys = {
            (trip["student_id"], trip["tenant_id"], trip["vehicle_id"])
            for trip in coordinator.data.values()
            if trip.get("vehicle_id")
        }
        new_entities: list[SensorEntity] = []
        for student_id, tenant_id, vehicle_id in vehicle_keys:
            if (student_id, tenant_id, vehicle_id) in added_vehicle_keys:
                continue
            new_entities.extend(
                (
                    EdulogVehiclePollingIntervalSensor(
                        coordinator, entry, student_id, tenant_id, vehicle_id
                    ),
                    EdulogVehicleNextPollSensor(
                        coordinator, entry, student_id, tenant_id, vehicle_id
                    ),
                    EdulogVehicleTelemetryHealthSensor(
                        coordinator, entry, student_id, tenant_id, vehicle_id
                    ),
                )
            )
        if new_entities:
            added_vehicle_keys.update(
                entity.vehicle_key for entity in new_entities
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_diagnostics))
    add_new_diagnostics()


class EdulogVehicleDiagnosticSensor(
    CoordinatorEntity[EdulogCoordinator], SensorEntity
):
    """Base class for diagnostic sensors associated with a student vehicle."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
        sensor_key: str,
        sensor_name: str,
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
        self._attr_name = sensor_name
        self._attr_unique_id = (
            f"{entry.entry_id}_{student_id}_{tenant_id}_{vehicle_id}_{sensor_key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{student_id}")},
            name=student_trip["name"],
            manufacturer="Edulog",
            model="Student bus tracking",
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the vehicle associated with this diagnostic sensor."""
        return {"vehicle_id": self.vehicle_id}

    @property
    def available(self) -> bool:
        """Only report vehicles still assigned to this student."""
        return super().available and any(
            trip["student_id"] == self.student_id
            and trip["tenant_id"] == self.tenant_id
            and trip["vehicle_id"] == self.vehicle_id
            for trip in self.coordinator.data.values()
        )


class EdulogVehiclePollingIntervalSensor(EdulogVehicleDiagnosticSensor):
    """Report the configured location polling interval for a vehicle."""

    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            student_id,
            tenant_id,
            vehicle_id,
            "polling_interval",
            "Location polling interval",
        )

    @property
    def available(self) -> bool:
        """The configured polling cadence is known independently of API state."""
        return True

    @property
    def native_value(self) -> float:
        """Return the configured location polling interval in seconds."""
        return SCAN_INTERVAL.total_seconds()


class EdulogVehicleNextPollSensor(EdulogVehicleDiagnosticSensor):
    """Report the next eligible location poll time for a student's vehicle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            student_id,
            tenant_id,
            vehicle_id,
            "next_location_poll",
            "Next eligible location poll",
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


class EdulogVehicleTelemetryHealthSensor(EdulogVehicleDiagnosticSensor):
    """Report whether the latest vehicle telemetry request succeeded."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:heart-pulse"
    _attr_options = ["unknown", "healthy", "no_data", "stale"]

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: EdulogConfigEntry,
        student_id: str,
        tenant_id: str,
        vehicle_id: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            student_id,
            tenant_id,
            vehicle_id,
            "telemetry_health",
            "Telemetry health",
        )

    @property
    def native_value(self) -> str:
        """Return the health of the latest vehicle telemetry request."""
        return next(
            (
                trip.get("telemetry_health", "unknown")
                for trip in self.coordinator.data.values()
                if trip["student_id"] == self.student_id
                and trip["tenant_id"] == self.tenant_id
                and trip["vehicle_id"] == self.vehicle_id
            ),
            "unknown",
        )
