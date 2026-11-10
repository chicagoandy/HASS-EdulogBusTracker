"""Tests for setup, polling failures and migration."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.edulog_bus_tracker.const import DOMAIN, SCAN_INTERVAL

from .conftest import (
    STUDENTS_URL,
    TRIPS_URL,
    VEHICLE_URL,
    make_token,
    student,
    trip_lookup,
)

TRACKER = "device_tracker.edulog_user_1_s1_vehicle_1"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_tracker(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Setup stores the coordinator on the entry and adds the bus tracker."""
    freezer.move_to("2026-11-10 12:00:00-05:00")
    await setup_entry(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.last_update_success
    state = hass.states.get(TRACKER)
    assert state is not None
    assert state.attributes["latitude"] == 41.88


async def test_user_entity_id_survives_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The integration never rewrites an entity ID the user changed."""
    freezer.move_to("2026-11-10 12:00:00-05:00")
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    registry.async_update_entity(TRACKER, new_entity_id="device_tracker.school_bus")

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("device_tracker.school_bus") is not None
    assert hass.states.get(TRACKER) is None
    entry = registry.async_get("device_tracker.school_bus")
    assert entry.original_name == "Vehicle 1"


async def test_vehicle_failure_keeps_last_position(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A failed location request keeps the last position and marks it stale."""
    freezer.move_to("2026-11-10 12:00:00-05:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    mock_api.clear_requests()
    mock_api.get(VEHICLE_URL, status=500)
    freezer.tick(SCAN_INTERVAL + timedelta(seconds=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(TRACKER)
    assert state.attributes["latitude"] == 41.88
    tracker = next(iter(v for v in coordinator.data.values() if v["vehicle_id"]))
    assert tracker["telemetry_health"] == "stale"


async def test_api_failure_backs_off_and_recovers(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Trip endpoint failures slow the coordinator down until it recovers."""
    freezer.move_to("2026-11-10 12:00:00-05:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    mock_api.clear_requests()
    mock_api.get(STUDENTS_URL, json=[student("s1", "tenant-a")])
    mock_api.post(TRIPS_URL, status=503)
    freezer.tick(timedelta(hours=2))
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.update_interval == timedelta(seconds=30)

    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=60)

    mock_api.clear_requests()
    mock_api.get(STUDENTS_URL, json=[student("s1", "tenant-a")])
    mock_api.post(TRIPS_URL, json=[trip_lookup("s1", "bus-7")])
    mock_api.get(VEHICLE_URL, json=[{"latitude": 41.9, "longitude": -87.6}])
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.update_interval == SCAN_INTERVAL


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected token with no refresh token starts the reauth flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        unique_id="user-1",
        data={"auth_token": make_token()},
    )
    aioclient_mock.get(STUDENTS_URL, status=401)
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_migration_runs_every_step(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
) -> None:
    """A version 2 entry is migrated all the way to version 4."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="user-1",
        data={"auth_token": make_token(), "legacy": "dropped"},
    )
    await setup_entry(hass, entry)

    assert entry.version == 4
    assert entry.data == {"auth_token": make_token()}
