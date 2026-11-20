"""Tests for the polling and map-display geofences."""

from __future__ import annotations

from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_NOT_HOME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.edulog_bus_tracker.const import (
    CONF_DISPLAY_FENCE_RADIUS,
    CONF_FENCE_CENTERS,
    CONF_POLLING_FENCE_RADIUS,
    DOMAIN,
)

from .conftest import STUDENTS_URL, TRIPS_URL, VEHICLE_URL, student

TRACKER = "device_tracker.edulog_user_1_s1_vehicle_1"
TRIP_KEY = "s1_tenant-a_s1-trip"

# Made-up stops about 11 km apart.
HOME_STOP = (41.90, -87.65)
SCHOOL_STOP = (41.80, -87.60)
# Bus positions relative to HOME_STOP.
INSIDE_POLLING_FENCE = (41.901, -87.651)  # about 0.1 mi
BETWEEN_FENCES = (41.88, -87.63)  # about 1.7 mi
OUTSIDE_DISPLAY_FENCE = (42.00, -87.65)  # about 6.9 mi


def trip_lookup(trip_type: str) -> dict[str, Any]:
    """Return one all-day trip whose stops have coordinates."""
    home = {"latitude": HOME_STOP[0], "longitude": HOME_STOP[1]}
    school = {"latitude": SCHOOL_STOP[0], "longitude": SCHOOL_STOP[1]}
    to_school = trip_type == "ToSchool"
    return {
        "student": {"studentId": "s1"},
        "timezone": "America/New_York",
        "trips": [
            {
                "tripId": "s1-trip",
                "tripName": "Bus",
                "type": trip_type,
                "vehicleId": "bus-7",
                # The API spells these pickUp/dropOff.
                "pickUp": {
                    "time": "00:30",
                    "stopName": "Home" if to_school else "School",
                    **(home if to_school else school),
                },
                "dropOff": {
                    "time": "23:50",
                    "stopName": "School" if to_school else "Home",
                    **(school if to_school else home),
                },
            }
        ],
    }


def vehicle_event(
    position: tuple[float, float], status: str = "live"
) -> list[dict[str, Any]]:
    return [
        {
            "latitude": position[0],
            "longitude": position[1],
            "positionStatus": status,
        }
    ]


async def setup(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    position: tuple[float, float],
    *,
    trip_type: str = "ToSchool",
    status: str = "live",
) -> None:
    freezer.move_to("2026-11-10 12:00:00-05:00")
    aioclient_mock.get(STUDENTS_URL, json=[student("s1", "tenant-a")])
    aioclient_mock.post(TRIPS_URL, json=[trip_lookup(trip_type)])
    aioclient_mock.get(VEHICLE_URL, json=vehicle_event(position, status))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity_id(hass: HomeAssistant, platform: Platform, unique_id: str) -> str:
    registry = er.async_get(hass)
    found = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert found is not None
    return found


def polling_interval(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    sensor = entity_id(
        hass,
        Platform.SENSOR,
        f"{entry.entry_id}_s1_tenant-a_bus-7_polling_interval",
    )
    return hass.states.get(sensor).state


async def test_inside_polling_fence_polls_fast(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Near the stop the bus is shown and polled every 5 seconds."""
    await setup(hass, freezer, aioclient_mock, config_entry, INSIDE_POLLING_FENCE)

    state = hass.states.get(TRACKER)
    assert state.attributes["latitude"] == INSIDE_POLLING_FENCE[0]
    assert state.attributes["within_polling_fence"] is True
    assert state.attributes["within_display_fence"] is True
    assert float(polling_interval(hass, config_entry)) == 5


async def test_between_fences_polls_slowly(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Between 1 and 3 miles the bus is shown but polled every 30 seconds."""
    await setup(hass, freezer, aioclient_mock, config_entry, BETWEEN_FENCES)

    state = hass.states.get(TRACKER)
    assert state.attributes["latitude"] == BETWEEN_FENCES[0]
    assert state.attributes["within_polling_fence"] is False
    assert state.attributes["within_display_fence"] is True
    assert float(polling_interval(hass, config_entry)) == 30


async def test_outside_display_fence_is_hidden(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Beyond the display fence the tracker is Away and has no coordinates."""
    await setup(hass, freezer, aioclient_mock, config_entry, OUTSIDE_DISPLAY_FENCE)

    state = hass.states.get(TRACKER)
    assert state.state == STATE_NOT_HOME
    assert "latitude" not in state.attributes
    assert "longitude" not in state.attributes
    assert state.attributes["within_display_fence"] is False


async def test_stale_fix_is_hidden(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A stale fix, even at the stop, is neither shown nor fast-polled."""
    await setup(
        hass,
        freezer,
        aioclient_mock,
        config_entry,
        INSIDE_POLLING_FENCE,
        status="stale",
    )

    state = hass.states.get(TRACKER)
    assert state.state == STATE_NOT_HOME
    assert state.attributes["position_status"] == "stale"
    assert float(polling_interval(hass, config_entry)) == 30


async def test_radius_options_apply(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The options flow sets both radii and keeps the fence centres."""
    await setup(hass, freezer, aioclient_mock, config_entry, OUTSIDE_DISPLAY_FENCE)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_FENCE_CENTERS: {TRIP_KEY: "pickup"}}
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLLING_FENCE_RADIUS: 0.5, CONF_DISPLAY_FENCE_RADIUS: 10.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_FENCE_CENTERS: {TRIP_KEY: "pickup"},
        CONF_POLLING_FENCE_RADIUS: 0.5,
        CONF_DISPLAY_FENCE_RADIUS: 10.0,
    }

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(TRACKER)
    assert state.attributes["latitude"] == OUTSIDE_DISPLAY_FENCE[0]


@pytest.mark.parametrize(
    ("trip_type", "expected"),
    [("ToSchool", "pickup"), ("FromSchool", "dropoff")],
)
async def test_fence_center_defaults_to_students_stop(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    trip_type: str,
    expected: str,
) -> None:
    """Trips to school centre on the pickup; trips home on the drop-off."""
    await setup(
        hass,
        freezer,
        aioclient_mock,
        config_entry,
        INSIDE_POLLING_FENCE,
        trip_type=trip_type,
    )

    select = entity_id(
        hass, Platform.SELECT, f"{config_entry.entry_id}_{TRIP_KEY}_fence_center"
    )
    state = hass.states.get(select)
    assert state.state == expected
    assert state.attributes["stop_located"] is True
    assert hass.states.get(TRACKER).attributes["within_polling_fence"] is True


async def test_changing_fence_center_moves_fences(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Choosing the school stop hides a bus that is at the home stop."""
    await setup(hass, freezer, aioclient_mock, config_entry, INSIDE_POLLING_FENCE)
    select = entity_id(
        hass, Platform.SELECT, f"{config_entry.entry_id}_{TRIP_KEY}_fence_center"
    )

    await hass.services.async_call(
        Platform.SELECT,
        "select_option",
        {"entity_id": select, "option": "dropoff"},
        blocking=True,
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(select).state == "dropoff"
    assert config_entry.options[CONF_FENCE_CENTERS] == {TRIP_KEY: "dropoff"}
    assert hass.states.get(TRACKER).state == STATE_NOT_HOME
