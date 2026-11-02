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
    """Set up polling diagnostics for each approved student trip."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_tracker_ids: set[str] = set()

    def add_new_trips() -> None:
        new_entities = [
            sensor_type(coordinator, entry, tracker_id)
            for tracker_id in coordinator.data
            if tracker_id not in added_tracker_ids
            for sensor_type in (
                EdulogTripPollingIntervalSensor,
                EdulogTripNextPollSensor,
            )
        ]
        if new_entities:
            added_tracker_ids.update(
                entity.tracker_id for entity in new_entities
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_trips))
    add_new_trips()


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


class EdulogTripNextPollSensor(EdulogTripDiagnosticSensor):
    """Report the next eligible location poll time for a trip's vehicle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: EdulogCoordinator, entry: ConfigEntry, tracker_id: str
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            tracker_id,
            "next_location_poll",
            "next eligible location poll",
        )

    @property
    def available(self) -> bool:
        """Report the next poll time while active or awaiting the window."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the next refresh time or the scheduled polling-window start."""
        next_poll = self._trip.get("next_location_poll")
        return next_poll if isinstance(next_poll, datetime) else None
