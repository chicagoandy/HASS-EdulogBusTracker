"""Shared fixtures for the Edulog Bus Tracker tests."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.edulog_bus_tracker.const import API_BASE_URL, DOMAIN

USER_ID = "user-1"
LOGIN_URL = f"{API_BASE_URL}/api/v2/pp/auth/login"
STUDENTS_URL = f"{API_BASE_URL}/api/v2/pp/users/{USER_ID}/studentAccessRequests"
TRIPS_URL = f"{API_BASE_URL}/api/v2/pp/students/trips"
VEHICLE_URL = f"{API_BASE_URL}/api/v2/pp/vehicleliveevents"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


def make_token(subject: str = USER_ID, expires: int = 4_102_444_800) -> str:
    """Build an unsigned JWT with the claims the integration reads."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": subject, "exp": expires}).encode()
    ).decode()
    return f"header.{payload.rstrip('=')}.signature"


def student(student_id: str, tenant_id: str) -> dict[str, Any]:
    """Return an approved student access request."""
    return {
        "status": "approved",
        "studentId": student_id,
        "tenantId": tenant_id,
        "firstName": "Kid",
        "lastName": student_id.upper(),
    }


def trip_lookup(student_id: str, vehicle_id: str) -> dict[str, Any]:
    """Return a trip lookup whose polling window covers the whole day."""
    return {
        "student": {"studentId": student_id},
        "timezone": "America/New_York",
        "trips": [
            {
                "tripId": f"{student_id}-am",
                "tripName": "Morning",
                "vehicleId": vehicle_id,
                "pickup": {"time": "00:30", "stopName": "Stop"},
                "dropoff": {"time": "23:50", "stopName": "School"},
            }
        ],
    }


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a current-version config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=4,
        unique_id=USER_ID,
        title="parent@example.com",
        data={"auth_token": make_token(), "refresh_token": "refresh"},
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Mock one student in one district riding one bus."""
    aioclient_mock.get(STUDENTS_URL, json=[student("s1", "tenant-a")])
    aioclient_mock.post(TRIPS_URL, json=[trip_lookup("s1", "bus-7")])
    aioclient_mock.get(
        VEHICLE_URL, json=[{"latitude": 41.88, "longitude": -87.63, "speed": 12}]
    )
    return aioclient_mock
