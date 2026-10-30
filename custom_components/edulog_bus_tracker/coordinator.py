"""Data update coordinator for Edulog student bus locations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import EdulogApi, EdulogAuthError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class EdulogCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch student assignments and their associated live vehicle locations."""

    def __init__(self, hass: HomeAssistant, api: EdulogApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Retrieve approved students, their trips, and latest bus positions."""
        try:
            return await self._async_fetch_data()
        except EdulogAuthError as err:
            raise ConfigEntryAuthFailed(
                "Edulog access token expired; sign in again."
            ) from err

    async def _async_fetch_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all student trips and their vehicle locations."""
        if self.api.user_id is None:
            raise EdulogAuthError("Edulog account ID is missing.")

        student_records = await self.api.async_get_students()
        approved_students: dict[str, dict[str, str]] = {}
        trip_requests: list[dict[str, str]] = []
        for record in student_records:
            if str(record.get("status", "")).strip().lower() != "approved":
                continue
            student_id = _string(record.get("studentId"))
            tenant_id = _string(record.get("tenantId"))
            if not student_id or not tenant_id:
                _LOGGER.warning("Skipping an approved student with incomplete identifiers")
                continue
            full_name = " ".join(
                part
                for part in (
                    _string(record.get("firstName")),
                    _string(record.get("lastName")),
                )
                if part
            )
            approved_students[student_id] = {
                "name": full_name or _string(record.get("name")) or "Edulog student",
                "tenant_id": tenant_id,
            }
            trip_requests.append({"studentId": student_id, "tenantId": tenant_id})

        if not trip_requests:
            return {}

        trip_lookups = await self.api.async_get_trips(trip_requests)
        student_ids = [request["studentId"] for request in trip_requests]
        trackers: dict[str, dict[str, Any]] = {}
        requests_by_vehicle: dict[tuple[str, str], list[str]] = {}
        matched_students: set[str] = set()

        def add_unassigned_tracker(student_id: str) -> None:
            student = approved_students[student_id]
            tracker_id = f"{student_id}_unassigned"
            trackers[tracker_id] = {
                "student_id": student_id,
                "name": student["name"],
                "trip_name": "No assigned trip",
                "tenant_id": student["tenant_id"],
                "vehicle_id": None,
                "location": None,
            }

        for index, lookup in enumerate(trip_lookups):
            student = lookup.get("student")
            student_id = (
                _string(student.get("studentId"))
                if isinstance(student, Mapping)
                else _string(lookup.get("studentId"))
            )
            if not student_id and isinstance(student, Mapping):
                student_id = _string(student.get("id"))
            if not student_id and len(trip_lookups) == len(student_ids):
                student_id = student_ids[index]
            if not student_id or student_id not in approved_students:
                continue

            matched_students.add(student_id)
            trips = lookup.get("trips")
            if not isinstance(trips, list):
                add_unassigned_tracker(student_id)
                continue
            if not trips:
                add_unassigned_tracker(student_id)
                continue

            valid_trip_count = 0
            trip_name_counts: dict[str, int] = {}
            for trip_index, trip in enumerate(trips):
                if not isinstance(trip, Mapping):
                    continue
                valid_trip_count += 1
                trip_id = (
                    _string(trip.get("tripId"))
                    or _string(trip.get("id"))
                    or f"trip_{trip_index + 1}"
                )
                tracker_id = f"{student_id}_{trip_id}"
                if tracker_id in trackers:
                    tracker_id = f"{tracker_id}_{trip_index + 1}"
                vehicle_id = _string(trip.get("vehicleId"))
                trip_name = next(
                    (
                        value
                        for key in ("tripName", "name", "routeName", "direction")
                        if (value := _string(trip.get(key)))
                    ),
                    f"Trip {trip_index + 1}",
                )
                trip_name_counts[trip_name] = trip_name_counts.get(trip_name, 0) + 1
                if trip_name_counts[trip_name] > 1:
                    trip_name = f"{trip_name} {trip_name_counts[trip_name]}"
                trackers[tracker_id] = {
                    "student_id": student_id,
                    "name": approved_students[student_id]["name"],
                    "trip_name": trip_name,
                    "tenant_id": approved_students[student_id]["tenant_id"],
                    "vehicle_id": vehicle_id,
                    "location": None,
                }
                if vehicle_id:
                    key = (vehicle_id, approved_students[student_id]["tenant_id"])
                    requests_by_vehicle.setdefault(key, []).append(tracker_id)
            if not valid_trip_count:
                add_unassigned_tracker(student_id)

        for student_id in approved_students:
            if student_id not in matched_students:
                add_unassigned_tracker(student_id)

        semaphore = asyncio.Semaphore(5)

        async def fetch_location(vehicle_id: str, tenant_id: str) -> dict[str, Any] | None:
            async with semaphore:
                return await self.api.async_get_vehicle_location(vehicle_id, tenant_id)

        vehicle_locations = await asyncio.gather(
            *(
                fetch_location(vehicle_id, tenant_id)
                for vehicle_id, tenant_id in requests_by_vehicle
            )
        )
        for (vehicle_id, tenant_id), location in zip(
            requests_by_vehicle, vehicle_locations, strict=True
        ):
            for tracker_id in requests_by_vehicle[(vehicle_id, tenant_id)]:
                trackers[tracker_id]["location"] = location

        return trackers


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
