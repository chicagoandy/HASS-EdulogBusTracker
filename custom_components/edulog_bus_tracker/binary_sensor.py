"""Binary sensors for Edulog student trips."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
    """Set up a polling sensor for each approved student trip."""
    coordinator: EdulogCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_tracker_ids: set[str] = set()

    def add_new_trips() -> None:
        new_entities = [
            EdulogTripPollingSensor(coordinator, entry, tracker_id)
            for tracker_id in coordinator.data
            if tracker_id not in added_tracker_ids
        ]
        if new_entities:
            added_tracker_ids.update(entity.tracker_id for entity in new_entities)
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_trips))
    add_new_trips()


class EdulogTripPollingSensor(CoordinatorEntity[EdulogCoordinator], BinarySensorEntity):
    """Indicate when a trip's vehicle location is polled at the active cadence."""

    _attr_has_entity_name = True
    _attr_name = "Location polling"
    _attr_icon = "mdi:sync"

    def __init__(
        self,
        coordinator: EdulogCoordinator,
        entry: ConfigEntry,
        tracker_id: str,
    ) -> None:
        super().__init__(coordinator)
        trip = coordinator.data[tracker_id]
        self.tracker_id = tracker_id
        self._attr_unique_id = f"{entry.entry_id}_{tracker_id}_location_polling"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{tracker_id}")},
            name=f"{trip['name']} - {trip['trip_name']}",
            manufacturer="Edulog",
            model="Student trip bus location",
        )

    @property
    def available(self) -> bool:
        """Only report a sensor while its trip remains in the account."""
        return super().available and self.tracker_id in self.coordinator.data

    @property
    def is_on(self) -> bool:
        """Return whether the trip is in its active location polling window."""
        trip = self.coordinator.data.get(self.tracker_id, {})
        return bool(trip.get("polling_active", False))
