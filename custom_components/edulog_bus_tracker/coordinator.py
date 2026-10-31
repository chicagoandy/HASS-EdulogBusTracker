"""Data update coordinator for Edulog student bus locations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import EdulogApi, EdulogApiError, EdulogAuthError
from .const import (
    DOMAIN,
    REAUTH_ISSUE_ID_PREFIX,
    SCAN_INTERVAL,
    STUDENT_LIST_REFRESH_INTERVAL,
    TRIP_SCHEDULE_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class EdulogCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch student assignments and their associated live vehicle locations."""

    def __init__(
        self, hass: HomeAssistant, api: EdulogApi, config_entry_id: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self.config_entry_id = config_entry_id
        self._student_records: list[dict[str, Any]] | None = None
        self._last_student_refresh: datetime | None = None
        self._trip_lookups: list[dict[str, Any]] | None = None
        self._last_trip_refresh: datetime | None = None
        self._last_trip_requests: list[dict[str, str]] | None = None
        self._vehicle_locations: dict[
            tuple[str, str], dict[str, Any] | None
        ] = {}
        self._last_vehicle_refresh: dict[tuple[str, str], datetime] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Retrieve approved students, their trips, and latest bus positions."""
        try:
            return await self._async_fetch_data()
        except EdulogAuthError as err:
            _LOGGER.warning(
                "Edulog authentication failed; reauthentication is required"
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{REAUTH_ISSUE_ID_PREFIX}_{self.config_entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="reauthenticate",
                config_entry_id=self.config_entry_id,
            )
            raise ConfigEntryAuthFailed(
                "Edulog access token expired; sign in again."
            ) from err
        except EdulogApiError as err:
            _LOGGER.error("Edulog API data refresh failed: %s", err)
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error(
                "Could not connect to the Edulog API (%s)",
                type(err).__name__,
            )
            raise

    async def _async_fetch_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all student trips and their vehicle locations."""
        if self.api.user_id is None:
            raise EdulogAuthError("Edulog account ID is missing.")

        now = dt_util.now()
        student_list_refreshed = _refresh_due(
            self._last_student_refresh, now, STUDENT_LIST_REFRESH_INTERVAL
        )
        if student_list_refreshed:
            self._student_records = await self.api.async_get_students()
            self._last_student_refresh = now
            _LOGGER.debug(
                "Fetched %d student access requests",
                len(self._student_records),
            )

        student_records = self._student_records or []
        approved_students: dict[str, dict[str, Any]] = {}
        trip_requests: list[dict[str, str]] = []
        for record in student_records:
            if str(record.get("status", "")).strip().lower() != "approved":
                continue
            student_id = _string(record.get("studentId"))
            tenant_id = _string(record.get("tenantId"))
            if not student_id or not tenant_id:
                if student_list_refreshed:
                    _LOGGER.warning(
                        "Skipping an approved student with incomplete identifiers"
                    )
                continue
            first_name = _string(_field(record, "firstName"))
            last_name = _string(_field(record, "lastName"))
            full_name = " ".join(part for part in (first_name, last_name) if part)
            approved_students[student_id] = {
                "name": full_name or _string(record.get("name")) or "Edulog student",
                "tenant_id": tenant_id,
                "student_first_name": first_name,
                "student_last_name": last_name,
            }
            trip_requests.append({"studentId": student_id, "tenantId": tenant_id})

        if not trip_requests:
            if student_list_refreshed:
                _LOGGER.warning(
                    "No approved students with complete identifiers were found; "
                    "skipping the trip schedule request"
                )
            self._trip_lookups = []
            self._last_trip_requests = []
            self._last_trip_refresh = now
            return {}

        refresh_trip_schedule = (
            _refresh_due(
                self._last_trip_refresh,
                now,
                TRIP_SCHEDULE_REFRESH_INTERVAL,
            )
            or trip_requests != self._last_trip_requests
        )
        if refresh_trip_schedule:
            _LOGGER.debug(
                "Requesting trip schedules for %d approved students",
                len(trip_requests),
            )
            self._trip_lookups = await self.api.async_get_trips(trip_requests)
            self._last_trip_refresh = now
            self._last_trip_requests = trip_requests
            _LOGGER.debug(
                "Received %d trip schedule records",
                len(self._trip_lookups),
            )

        trip_lookups = self._trip_lookups or []
        student_ids = [request["studentId"] for request in trip_requests]
        trackers: dict[str, dict[str, Any]] = {}
        requests_by_vehicle: dict[tuple[str, str], list[str]] = {}
        matched_students: set[str] = set()
        assigned_trip_count = 0

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
                "polling_active": False,
                "polling_interval_seconds": None,
                "next_location_poll": None,
                "trip_type": None,
                "pickup_stop_name": None,
                "pickup_time": None,
                "dropoff_stop_name": None,
                "dropoff_time": None,
                "student_first_name": student["student_first_name"],
                "student_last_name": student["student_last_name"],
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
                assigned_trip_count += 1
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
                    "polling_active": bool(vehicle_id)
                    and _trip_is_active(trip, now),
                    "polling_interval_seconds": None,
                    "next_location_poll": None,
                    **_trip_attributes(trip),
                    "student_first_name": approved_students[student_id][
                        "student_first_name"
                    ],
                    "student_last_name": approved_students[student_id][
                        "student_last_name"
                    ],
                }
                if vehicle_id:
                    key = (vehicle_id, approved_students[student_id]["tenant_id"])
                    requests_by_vehicle.setdefault(key, []).append(tracker_id)
            if not valid_trip_count:
                add_unassigned_tracker(student_id)

        for student_id in approved_students:
            if student_id not in matched_students:
                add_unassigned_tracker(student_id)

        if refresh_trip_schedule:
            if not trip_lookups:
                _LOGGER.warning(
                    "The trip schedule API returned no records for %d approved "
                    "students; existing trip entities may be unavailable",
                    len(approved_students),
                )
            elif len(matched_students) != len(approved_students):
                _LOGGER.warning(
                    "Trip schedule data matched %d of %d approved students; "
                    "existing trip entities for unmatched students may be unavailable",
                    len(matched_students),
                    len(approved_students),
                )
            elif assigned_trip_count == 0:
                _LOGGER.warning(
                    "No assigned trips were returned for %d approved students",
                    len(approved_students),
                )
            else:
                _LOGGER.debug(
                    "Prepared %d trip trackers for %d approved students",
                    len(trackers),
                    len(approved_students),
                )

        semaphore = asyncio.Semaphore(5)

        async def fetch_location(vehicle_id: str, tenant_id: str) -> dict[str, Any] | None:
            async with semaphore:
                return await self.api.async_get_vehicle_location(vehicle_id, tenant_id)

        vehicle_intervals = {
            vehicle: SCAN_INTERVAL
            for vehicle, tracker_ids in requests_by_vehicle.items()
            if any(
                trackers[tracker_id]["polling_active"]
                for tracker_id in tracker_ids
            )
        }
        vehicles_to_refresh = [
            vehicle
            for vehicle, interval in vehicle_intervals.items()
            if _refresh_due(self._last_vehicle_refresh.get(vehicle), now, interval)
        ]
        vehicle_locations = await asyncio.gather(
            *(
                fetch_location(vehicle_id, tenant_id)
                for vehicle_id, tenant_id in vehicles_to_refresh
            )
        )
        for vehicle, location in zip(
            vehicles_to_refresh, vehicle_locations, strict=True
        ):
            self._vehicle_locations[vehicle] = location
            self._last_vehicle_refresh[vehicle] = dt_util.now()

        for vehicle, tracker_ids in requests_by_vehicle.items():
            location = self._vehicle_locations.get(vehicle)
            interval = vehicle_intervals.get(vehicle)
            last_refresh = self._last_vehicle_refresh.get(vehicle)
            next_location_poll = (
                last_refresh + interval
                if interval is not None and last_refresh is not None
                else None
            )
            for tracker_id in tracker_ids:
                trackers[tracker_id]["location"] = location
                trackers[tracker_id]["polling_interval_seconds"] = (
                    interval.total_seconds() if interval is not None else None
                )
                trackers[tracker_id]["next_location_poll"] = next_location_poll

        return trackers


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _refresh_due(last_refresh: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last_refresh is None or now - last_refresh >= interval


def _field(record: Mapping[str, Any], name: str) -> Any:
    """Return a field by name without depending on API capitalization."""
    normalized_name = name.casefold()
    return next(
        (value for key, value in record.items() if key.casefold() == normalized_name),
        None,
    )


def _trip_attributes(trip: Mapping[str, Any]) -> dict[str, Any]:
    """Select the configured trip details without exposing the full API record."""
    return {
        "trip_type": _string(_field(trip, "type")),
        "pickup_stop_name": _nested_field(trip, ("pickup", "stopName")),
        "pickup_time": _nested_field(trip, ("pickup", "time")),
        "dropoff_stop_name": _nested_field(trip, ("dropoff", "stopName")),
        "dropoff_time": _nested_field(trip, ("dropoff", "time")),
    }


def _nested_field(record: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = _field(value, part)
    return value if isinstance(value, (str, int, float, bool)) else None


def _trip_is_active(trip: Mapping[str, Any], now: datetime) -> bool:
    """Compare a trip's local pickup/dropoff schedule with the current time."""
    timezone = now.tzinfo
    if timezone is None:
        return False
    pickup_value = _nested_field(trip, ("pickup", "time"))
    dropoff_value = _nested_field(trip, ("dropoff", "time"))
    pickup = _parse_schedule_value(pickup_value, timezone, now)
    dropoff = _parse_schedule_value(dropoff_value, timezone, now)
    if pickup is None or dropoff is None:
        return False

    if isinstance(pickup, datetime) and isinstance(dropoff, datetime):
        return pickup <= now < dropoff

    if isinstance(pickup, datetime):
        dropoff_datetime = _combine_schedule_time(pickup.date(), dropoff, timezone)
        if dropoff_datetime <= pickup:
            dropoff_datetime += timedelta(days=1)
        return pickup <= now < dropoff_datetime

    if isinstance(dropoff, datetime):
        pickup_datetime = _combine_schedule_time(dropoff.date(), pickup, timezone)
        if pickup_datetime >= dropoff:
            pickup_datetime -= timedelta(days=1)
        return pickup_datetime <= now < dropoff

    for day_offset in (-1, 0):
        pickup_datetime = _combine_schedule_time(
            now.date() + timedelta(days=day_offset), pickup, timezone
        )
        dropoff_datetime = _combine_schedule_time(
            pickup_datetime.date(), dropoff, timezone
        )
        if dropoff_datetime <= pickup_datetime:
            dropoff_datetime += timedelta(days=1)
        if pickup_datetime <= now < dropoff_datetime:
            return True
    return False


def _parse_schedule_value(
    value: Any, timezone: tzinfo, now: datetime
) -> datetime | time | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_datetime = None
    if parsed_datetime is not None:
        if parsed_datetime.tzinfo is None:
            return parsed_datetime.replace(tzinfo=timezone)
        return parsed_datetime.astimezone(timezone)

    try:
        parsed_time = time.fromisoformat(text)
    except ValueError:
        for time_format in ("%I:%M %p", "%I:%M:%S %p"):
            try:
                parsed_time = datetime.strptime(text, time_format).time()
                break
            except ValueError:
                continue
        else:
            return None

    if parsed_time.tzinfo is not None:
        return datetime.combine(now.date(), parsed_time).astimezone(timezone)
    return parsed_time


def _combine_schedule_time(
    day: date, schedule_time: datetime | time, timezone: tzinfo
) -> datetime:
    if isinstance(schedule_time, datetime):
        return datetime.combine(day, schedule_time.timetz()).astimezone(timezone)
    return datetime.combine(day, schedule_time, tzinfo=timezone)
