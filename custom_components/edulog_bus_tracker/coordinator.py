"""Data update coordinator for Edulog student bus locations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance as location_distance

from .api import EdulogApi, EdulogApiError, EdulogAuthError
from .const import (
    API_ERROR_BACKOFF_INITIAL,
    API_ERROR_BACKOFF_MAX,
    CONF_DISPLAY_FENCE_RADIUS,
    CONF_FENCE_CENTERS,
    CONF_POLLING_FENCE_RADIUS,
    DEFAULT_DISPLAY_FENCE_RADIUS,
    DEFAULT_FENCE_CENTER,
    DEFAULT_POLLING_FENCE_RADIUS,
    DOMAIN,
    FENCE_CENTER_DROPOFF,
    FENCE_CENTER_OPTIONS,
    FROM_SCHOOL_TRIP_TYPES,
    LOCATION_POLL_BACKOFF_INITIAL,
    LOCATION_POLL_BACKOFF_MAX,
    LOCATION_POLL_LEAD_TIME,
    METERS_PER_MILE,
    OUTSIDE_POLLING_FENCE_INTERVAL,
    SCAN_INTERVAL,
    STUDENT_LIST_REFRESH_INTERVAL,
    TRIP_SCHEDULE_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class EdulogCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch student assignments and their associated live vehicle locations."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, api: EdulogApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self._student_records: list[dict[str, Any]] | None = None
        self._last_student_refresh: datetime | None = None
        self._trip_lookups: list[dict[str, Any]] | None = None
        self._last_trip_refresh: datetime | None = None
        self._last_trip_requests: list[dict[str, str]] | None = None
        self._vehicle_locations: dict[
            tuple[str, str], dict[str, Any] | None
        ] = {}
        self._vehicle_telemetry_health: dict[tuple[str, str], str] = {}
        self._last_vehicle_refresh: dict[tuple[str, str], datetime] = {}
        self._vehicle_poll_failures: dict[tuple[str, str], int] = {}
        self._vehicle_retry_after: dict[tuple[str, str], datetime] = {}
        self._last_trip_polling_status: dict[str, bool] = {}
        self._unknown_trip_timezones: set[str] = set()
        self._initial_location_refresh_complete = False
        self._api_refresh_failures = 0

    def fence_center(self, trip_key: str, trip_type: str | None = None) -> str:
        """Return which stop ("pickup" or "dropoff") centres a trip's fences.

        Until the user picks one, use the student's own stop: the pickup on
        the way to school and the drop-off on the way home.
        """
        centers = self.config_entry.options.get(CONF_FENCE_CENTERS) or {}
        center = centers.get(trip_key)
        if center in FENCE_CENTER_OPTIONS:
            return center
        return _default_fence_center(trip_type)

    def async_set_fence_center(self, trip_key: str, center: str) -> None:
        """Store a trip's fence centre; the next update applies it."""
        options = dict(self.config_entry.options)
        options[CONF_FENCE_CENTERS] = {
            **(options.get(CONF_FENCE_CENTERS) or {}),
            trip_key: center,
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=options
        )

    def _fence_radius_meters(self, option: str, default_miles: float) -> float:
        """Return a configured fence radius, entered in miles, in metres."""
        return (
            float(self.config_entry.options.get(option, default_miles))
            * METERS_PER_MILE
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Retrieve approved students, their trips, and latest bus positions."""
        try:
            data = await self._async_fetch_data()
        except EdulogAuthError as err:
            # Home Assistant stops polling and starts the reauth flow.
            raise ConfigEntryAuthFailed(
                "Edulog access token expired; sign in again."
            ) from err
        except (EdulogApiError, aiohttp.ClientError, TimeoutError) as err:
            # Back off between failed refreshes so an outage of the student or
            # trip endpoints is not retried at the 5-second location cadence.
            # The coordinator logs the UpdateFailed itself.
            self._api_refresh_failures += 1
            backoff_exponent = min(self._api_refresh_failures - 1, 5)
            self.update_interval = min(
                API_ERROR_BACKOFF_INITIAL * 2**backoff_exponent,
                API_ERROR_BACKOFF_MAX,
            )
            raise UpdateFailed(
                f"Edulog API request failed ({type(err).__name__}); retrying in "
                f"{int(self.update_interval.total_seconds())} seconds"
            ) from err
        self._api_refresh_failures = 0
        self.update_interval = SCAN_INTERVAL
        return data

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
        approved_record_count = 0
        incomplete_identifier_count = 0
        for record in student_records:
            if str(_field(record, "status") or "").strip().lower() != "approved":
                continue
            approved_record_count += 1
            student_id = _string(_field(record, "studentId"))
            tenant_id = _string(_field(record, "tenantId"))
            if not student_id or not tenant_id:
                incomplete_identifier_count += 1
                continue
            first_name = _string(_field(record, "firstName"))
            last_name = _string(_field(record, "lastName"))
            full_name = " ".join(part for part in (first_name, last_name) if part)
            approved_students[student_id] = {
                "name": full_name
                or _string(_field(record, "name"))
                or "Edulog student",
                "tenant_id": tenant_id,
                "student_first_name": first_name,
                "student_last_name": last_name,
            }
            trip_requests.append({"studentId": student_id, "tenantId": tenant_id})

        # The API fetches trips one tenant at a time. Group the requests the
        # same way, so the response order still matches the request order for
        # records that omit their student ID.
        tenant_order = list(dict.fromkeys(r["tenantId"] for r in trip_requests))
        trip_requests.sort(key=lambda r: tenant_order.index(r["tenantId"]))

        if student_list_refreshed and incomplete_identifier_count:
            _LOGGER.warning(
                "%d approved student access request(s) lacked a student ID or "
                "tenant ID and were skipped",
                incomplete_identifier_count,
            )

        if not trip_requests:
            if student_list_refreshed:
                if approved_record_count:
                    _LOGGER.warning(
                        "%d approved student access request(s) were found, but "
                        "none had complete identifiers; skipping the trip "
                        "schedule request",
                        approved_record_count,
                    )
                else:
                    _LOGGER.warning(
                        "Fetched %d student access request(s), but none had "
                        "approved status; skipping the trip schedule request",
                        len(student_records),
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
        students_with_vehicles: set[str] = set()
        assigned_trip_count = 0
        active_trip_count = 0

        def add_unassigned_tracker(student_id: str) -> None:
            student = approved_students[student_id]
            tracker_id = f"{student_id}_unassigned"
            if tracker_id in trackers:
                return
            trackers[tracker_id] = {
                "student_id": student_id,
                "name": student["name"],
                "trip_name": "No assigned trip",
                "tenant_id": student["tenant_id"],
                "vehicle_id": None,
                "location": None,
                "polling_active": False,
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
            trip_timezone_name = _string(_field(lookup, "timezone"))
            trip_timezone = now.tzinfo
            if trip_timezone_name:
                trip_timezone = await dt_util.async_get_time_zone(
                    trip_timezone_name
                )
                if trip_timezone is None:
                    trip_timezone = now.tzinfo
                    if trip_timezone_name not in self._unknown_trip_timezones:
                        _LOGGER.warning(
                            "Trip schedule has unrecognized timezone %r; "
                            "using Home Assistant's timezone",
                            trip_timezone_name,
                        )
                        self._unknown_trip_timezones.add(trip_timezone_name)

            trips = lookup.get("trips")
            if not isinstance(trips, list):
                add_unassigned_tracker(student_id)
                continue
            if not trips:
                add_unassigned_tracker(student_id)
                continue

            valid_trip_count = 0
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
                vehicle_id = _string(trip.get("vehicleId"))
                if not vehicle_id:
                    continue
                tenant_id = approved_students[student_id]["tenant_id"]
                tracker_id = f"{student_id}_vehicle_{tenant_id}_{vehicle_id}"
                polling_status_key = f"{tracker_id}_{trip_id}_{trip_index}"
                trip_name = next(
                    (
                        value
                        for key in ("tripName", "name", "routeName", "direction")
                        if (value := _string(trip.get(key)))
                    ),
                    f"Trip {trip_index + 1}",
                )
                route_start_time = _first_route_stop_time(trip)
                pickup_time = _nested_field(trip, ("pickup", "time"))
                dropoff_time = _nested_field(trip, ("dropoff", "time"))
                polling_active = bool(vehicle_id) and _trip_is_active(
                    trip, now, trip_timezone
                )
                active_trip_count += polling_active
                next_poll_start = (
                    _next_polling_start_time(trip, now, trip_timezone)
                    if vehicle_id and not polling_active
                    else None
                )
                trip_key = f"{student_id}_{tenant_id}_{trip_id}"
                fence_center = self.fence_center(
                    trip_key, _string(_field(trip, "type"))
                )
                fence_coordinates = _stop_coordinates(trip, fence_center)
                fence_trip = {
                    "key": trip_key,
                    "name": trip_name,
                    "center": fence_coordinates,
                    "active": polling_active,
                }
                trip_details = {
                    "name": trip_name,
                    **_trip_attributes(trip),
                    "fence_center": fence_center,
                    "fence_center_located": fence_coordinates is not None,
                }
                existing_tracker = trackers.get(tracker_id)
                if existing_tracker is None:
                    trackers[tracker_id] = {
                        "student_id": student_id,
                        "name": approved_students[student_id]["name"],
                        "trip_names": [trip_name],
                        "trips": [trip_details],
                        "fence_trips": [fence_trip],
                        "tenant_id": tenant_id,
                        "vehicle_id": vehicle_id,
                        "location": None,
                        "polling_active": polling_active,
                        "next_location_poll": next_poll_start,
                        **_trip_attributes(trip),
                        "student_first_name": approved_students[student_id][
                            "student_first_name"
                        ],
                        "student_last_name": approved_students[student_id][
                            "student_last_name"
                        ],
                    }
                    requests_by_vehicle.setdefault(
                        (vehicle_id, tenant_id), []
                    ).append(tracker_id)
                else:
                    existing_tracker["trip_names"].append(trip_name)
                    existing_tracker["trips"].append(trip_details)
                    existing_tracker["fence_trips"].append(fence_trip)
                    existing_tracker["polling_active"] |= polling_active
                    if next_poll_start is not None:
                        current_next_poll = existing_tracker["next_location_poll"]
                        if (
                            current_next_poll is None
                            or next_poll_start < current_next_poll
                        ):
                            existing_tracker["next_location_poll"] = next_poll_start
                students_with_vehicles.add(student_id)
                if (
                    refresh_trip_schedule
                    or self._last_trip_polling_status.get(polling_status_key)
                    != polling_active
                ):
                    _LOGGER.debug(
                        "Trip polling evaluation for student request %d, trip %d: "
                        "vehicle_assigned=%s, first_route_stop=%r, pickup=%r, "
                        "dropoff=%r, active=%s, fence_center=%s, "
                        "fence_center_located=%s",
                        index + 1,
                        trip_index + 1,
                        bool(vehicle_id),
                        route_start_time,
                        pickup_time,
                        dropoff_time,
                        polling_active,
                        fence_center,
                        fence_coordinates is not None,
                    )
                self._last_trip_polling_status[polling_status_key] = polling_active
            if not valid_trip_count:
                add_unassigned_tracker(student_id)

        for student_id in approved_students:
            if student_id not in matched_students or student_id not in students_with_vehicles:
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
                    "Prepared %d vehicle trackers for %d approved students",
                    sum(
                        tracker.get("vehicle_id") is not None
                        for tracker in trackers.values()
                    ),
                    len(approved_students),
                )

        semaphore = asyncio.Semaphore(5)

        async def fetch_location(
            vehicle: tuple[str, str],
        ) -> tuple[dict[str, Any] | None, Exception | None]:
            vehicle_id, tenant_id = vehicle
            async with semaphore:
                try:
                    return (
                        await self.api.async_get_vehicle_location(
                            vehicle_id, tenant_id
                        ),
                        None,
                    )
                except EdulogAuthError:
                    raise
                except (EdulogApiError, aiohttp.ClientError, TimeoutError) as err:
                    failures = self._vehicle_poll_failures.get(vehicle, 0) + 1
                    self._vehicle_poll_failures[vehicle] = failures
                    backoff_seconds = min(
                        LOCATION_POLL_BACKOFF_INITIAL.total_seconds()
                        * 2 ** min(failures - 1, 5),
                        LOCATION_POLL_BACKOFF_MAX.total_seconds(),
                    )
                    self._vehicle_retry_after[vehicle] = (
                        dt_util.now() + timedelta(seconds=backoff_seconds)
                    )
                    _LOGGER.warning(
                        "Vehicle location request failed (%s); retrying in %d seconds",
                        type(err).__name__,
                        int(backoff_seconds),
                    )
                    return None, err

        polling_radius = self._fence_radius_meters(
            CONF_POLLING_FENCE_RADIUS, DEFAULT_POLLING_FENCE_RADIUS
        )
        display_radius = self._fence_radius_meters(
            CONF_DISPLAY_FENCE_RADIUS, DEFAULT_DISPLAY_FENCE_RADIUS
        )

        def active_fence_centers(
            vehicle: tuple[str, str],
        ) -> list[tuple[float, float]] | None:
            """Return the fence centres of the vehicle's active trips.

            None means an active trip has no stop coordinates, so no polling
            fence applies and the vehicle is polled at the fast rate.
            """
            centers: list[tuple[float, float]] = []
            for tracker_id in requests_by_vehicle[vehicle]:
                for fence_trip in trackers[tracker_id]["fence_trips"]:
                    if not fence_trip["active"]:
                        continue
                    if fence_trip["center"] is None:
                        return None
                    centers.append(fence_trip["center"])
            return centers

        def polling_interval(vehicle: tuple[str, str]) -> timedelta:
            """Poll fast inside the polling fence and slowly outside it."""
            centers = active_fence_centers(vehicle)
            if centers is None or _within_fence(
                self._vehicle_locations.get(vehicle), centers, polling_radius
            ):
                return SCAN_INTERVAL
            return OUTSIDE_POLLING_FENCE_INTERVAL

        vehicle_intervals = {
            vehicle: polling_interval(vehicle)
            for vehicle, tracker_ids in requests_by_vehicle.items()
            if any(
                trackers[tracker_id]["polling_active"]
                for tracker_id in tracker_ids
            )
        }
        vehicles_to_refresh = [
            vehicle
            for vehicle in requests_by_vehicle
            if not self._initial_location_refresh_complete
            or (
                vehicle in vehicle_intervals
                and _refresh_due(
                    self._last_vehicle_refresh.get(vehicle),
                    now,
                    vehicle_intervals[vehicle],
                )
                and (
                    vehicle not in self._vehicle_retry_after
                    or now >= self._vehicle_retry_after[vehicle]
                )
            )
        ]
        _LOGGER.debug(
            "Polling evaluation: %d trips, %d active trip(s), %d active vehicle(s) "
            "(%d at the fast rate); %d location request(s) due",
            assigned_trip_count,
            active_trip_count,
            len(vehicle_intervals),
            sum(
                interval == SCAN_INTERVAL
                for interval in vehicle_intervals.values()
            ),
            len(vehicles_to_refresh),
        )
        vehicle_results = await asyncio.gather(
            *(
                fetch_location(vehicle) for vehicle in vehicles_to_refresh
            )
        )
        self._initial_location_refresh_complete = True
        for vehicle, (location, error) in zip(
            vehicles_to_refresh, vehicle_results, strict=True
        ):
            if error is None:
                self._vehicle_locations[vehicle] = location
                self._vehicle_telemetry_health[vehicle] = (
                    "healthy" if location is not None else "no_data"
                )
                self._last_vehicle_refresh[vehicle] = dt_util.now()
                self._vehicle_poll_failures.pop(vehicle, None)
                self._vehicle_retry_after.pop(vehicle, None)
            else:
                # Keep the last known position; the telemetry health sensor
                # reports that it is stale until a request succeeds again.
                self._vehicle_telemetry_health[vehicle] = "stale"
        if vehicles_to_refresh:
            locations_received = sum(
                location is not None and error is None
                for location, error in vehicle_results
            )
            _LOGGER.debug(
                "Vehicle location requests completed: %d of %d returned valid "
                "coordinates",
                locations_received,
                len(vehicles_to_refresh),
            )

        # Re-evaluate the polling fence against the positions just received,
        # so the next poll uses the rate for where the bus is now.
        vehicle_intervals = {
            vehicle: polling_interval(vehicle) for vehicle in vehicle_intervals
        }

        for vehicle, tracker_ids in requests_by_vehicle.items():
            location = self._vehicle_locations.get(vehicle)
            interval = vehicle_intervals.get(vehicle)
            centers = (
                active_fence_centers(vehicle) if interval is not None else None
            )
            within_polling_fence = (
                None
                if centers is None
                else _within_fence(location, centers, polling_radius)
            )
            last_refresh = self._last_vehicle_refresh.get(vehicle)
            retry_after = self._vehicle_retry_after.get(vehicle)
            if retry_after is not None:
                next_location_poll = retry_after
            elif interval is not None and last_refresh is not None:
                next_location_poll = last_refresh + interval
            else:
                next_location_poll = None
            for tracker_id in tracker_ids:
                tracker = trackers[tracker_id]
                tracker["location"] = location
                tracker["polling_interval"] = (
                    interval.total_seconds() if interval is not None else None
                )
                tracker["within_polling_fence"] = within_polling_fence
                # The display fence uses all of the tracker's trips, active or
                # not. If any trip has no stop coordinates, it does not apply.
                display_centers = [
                    fence_trip["center"] for fence_trip in tracker["fence_trips"]
                ]
                tracker["within_display_fence"] = (
                    None
                    if None in display_centers
                    else _within_fence(location, display_centers, display_radius)
                )
                tracker["telemetry_health"] = (
                    self._vehicle_telemetry_health.get(vehicle, "unknown")
                )
                if next_location_poll is not None:
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


def _default_fence_center(trip_type: str | None) -> str:
    """Centre on the student's own stop: drop-off for trips home from school."""
    if trip_type and trip_type.strip().casefold() in FROM_SCHOOL_TRIP_TYPES:
        return FENCE_CENTER_DROPOFF
    return DEFAULT_FENCE_CENTER


def _stop_coordinates(
    trip: Mapping[str, Any], stop: str
) -> tuple[float, float] | None:
    """Return the (latitude, longitude) of a trip's pickup or drop-off stop."""
    record = _field(trip, stop)
    if not isinstance(record, Mapping):
        return None
    latitude = _coordinate(_field(record, "latitude"))
    longitude = _coordinate(_field(record, "longitude"))
    if (
        latitude is None
        or longitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None
    return latitude, longitude


def _coordinate(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _location_is_fresh(location: Mapping[str, Any] | None) -> bool:
    """Return whether a vehicle fix is current; "stale" fixes are old."""
    return location is not None and (
        str(location.get("position_status") or "").strip().casefold() != "stale"
    )


def _within_fence(
    location: Mapping[str, Any] | None,
    centers: list[tuple[float, float]],
    radius_meters: float,
) -> bool:
    """Return whether a fresh vehicle fix is within the radius of any centre."""
    if location is None or not _location_is_fresh(location):
        return False
    for latitude, longitude in centers:
        meters = location_distance(
            location["latitude"], location["longitude"], latitude, longitude
        )
        if meters is not None and meters <= radius_meters:
            return True
    return False


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


def _first_route_stop_time(trip: Mapping[str, Any]) -> Any:
    """Return the time for the first route stop, if the API provides it."""
    route_stops = _field(trip, "routeStops")
    if not isinstance(route_stops, list):
        return None

    stops = [
        stop
        for stop in route_stops
        if isinstance(stop, Mapping) and _string(_field(stop, "time"))
    ]
    if not stops:
        return None

    sequenced_stops = [
        (sequence, stop)
        for stop in stops
        if isinstance(
            sequence := _field(stop, "stopSeqNumber"),
            (int, float),
        )
        and not isinstance(sequence, bool)
    ]
    first_stop = (
        min(sequenced_stops, key=lambda item: item[0])[1]
        if sequenced_stops
        else stops[0]
    )
    return _field(first_stop, "time")


def _trip_is_active(
    trip: Mapping[str, Any], now: datetime, timezone: tzinfo | None = None
) -> bool:
    """Check the polling window from 30 minutes before route start to drop-off."""
    timezone = timezone or now.tzinfo
    if timezone is None:
        return False
    now = now.astimezone(timezone)
    pickup_value = _first_route_stop_time(trip) or _nested_field(
        trip, ("pickup", "time")
    )
    dropoff_value = _nested_field(trip, ("dropoff", "time"))
    pickup = _parse_schedule_value(pickup_value, timezone, now)
    dropoff = _parse_schedule_value(dropoff_value, timezone, now)
    if pickup is None or dropoff is None:
        return False

    if isinstance(pickup, datetime) and isinstance(dropoff, datetime):
        return pickup - LOCATION_POLL_LEAD_TIME <= now < dropoff

    if isinstance(pickup, datetime):
        dropoff_datetime = _combine_schedule_time(pickup.date(), dropoff, timezone)
        if dropoff_datetime <= pickup:
            dropoff_datetime += timedelta(days=1)
        return pickup - LOCATION_POLL_LEAD_TIME <= now < dropoff_datetime

    if isinstance(dropoff, datetime):
        pickup_datetime = _combine_schedule_time(dropoff.date(), pickup, timezone)
        if pickup_datetime >= dropoff:
            pickup_datetime -= timedelta(days=1)
        return pickup_datetime - LOCATION_POLL_LEAD_TIME <= now < dropoff

    for day_offset in (-1, 0):
        pickup_datetime = _combine_schedule_time(
            now.date() + timedelta(days=day_offset), pickup, timezone
        )
        dropoff_datetime = _combine_schedule_time(
            pickup_datetime.date(), dropoff, timezone
        )
        if dropoff_datetime <= pickup_datetime:
            dropoff_datetime += timedelta(days=1)
        if (
            pickup_datetime - LOCATION_POLL_LEAD_TIME
            <= now
            < dropoff_datetime
        ):
            return True
    return False


def _next_polling_start_time(
    trip: Mapping[str, Any], now: datetime, timezone: tzinfo | None = None
) -> datetime | None:
    """Return the next scheduled start of this trip's location polling window."""
    timezone = timezone or now.tzinfo
    if timezone is None:
        return None
    now = now.astimezone(timezone)
    start_value = _first_route_stop_time(trip) or _nested_field(
        trip, ("pickup", "time")
    )
    start = _parse_schedule_value(start_value, timezone, now)
    if start is None:
        return None

    if isinstance(start, datetime):
        polling_start = start - LOCATION_POLL_LEAD_TIME
        return polling_start if polling_start > now else None

    polling_start = (
        _combine_schedule_time(now.date(), start, timezone)
        - LOCATION_POLL_LEAD_TIME
    )
    if polling_start <= now:
        polling_start += timedelta(days=1)
    return polling_start


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
