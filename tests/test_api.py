"""Tests for the Edulog API client."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.edulog_bus_tracker.api import EdulogApi, EdulogApiError

from .conftest import STUDENTS_URL, TRIPS_URL, USER_ID, make_token, trip_lookup


async def test_trips_requested_once_per_tenant(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Students in different districts are sent with their own tenant header."""
    aioclient_mock.post(TRIPS_URL, json=[trip_lookup("s1", "bus-7")])
    api = EdulogApi(async_get_clientsession(hass), make_token(), user_id=USER_ID)

    trips = await api.async_get_trips(
        [
            {"studentId": "s1", "tenantId": "tenant-a"},
            {"studentId": "s2", "tenantId": "tenant-b"},
            {"studentId": "s3", "tenantId": "tenant-a"},
        ]
    )

    assert len(trips) == 2
    calls = [
        (body, headers["x-v2-tenant-id"])
        for _method, _url, body, headers in aioclient_mock.mock_calls
    ]
    assert calls == [
        (
            [
                {"studentId": "s1", "tenantId": "tenant-a"},
                {"studentId": "s3", "tenantId": "tenant-a"},
            ],
            "tenant-a",
        ),
        ([{"studentId": "s2", "tenantId": "tenant-b"}], "tenant-b"),
    ]


async def test_invalid_json_raises_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A malformed body is reported as an API error, not a ValueError."""
    aioclient_mock.get(STUDENTS_URL, text="<html>maintenance</html>")
    api = EdulogApi(async_get_clientsession(hass), make_token(), user_id=USER_ID)

    with pytest.raises(EdulogApiError):
        await api.async_get_students()
