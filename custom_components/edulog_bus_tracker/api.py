"""Client for the Edulog Parent Portal API."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Mapping
import json
import logging
from math import isfinite
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE_URL

_TOKEN_REFRESH_PATH = "/api/v2/pp/auth/refresh"
_TOKEN_REFRESH_MARGIN_SECONDS = 60
_LOGGER = logging.getLogger(__name__)


class EdulogApiError(Exception):
    """Raised when the Edulog API returns an unusable response."""


class EdulogHttpError(EdulogApiError):
    """Raised for an unsuccessful Edulog API response without exposing IDs."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Edulog API returned HTTP {status}.")
        self.status = status


class EdulogAuthError(EdulogApiError):
    """Raised when Edulog authentication fails."""


class EdulogApi:
    """Make authenticated requests to the Edulog Parent Portal API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str | None = None,
        user_id: str | None = None,
        refresh_token: str | None = None,
        token_update_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self.user_id = user_id or _jwt_subject(access_token)
        self._token_update_callback = token_update_callback
        self._access_token_expires_at = _jwt_expiration(access_token)
        self._refresh_lock = asyncio.Lock()

    @property
    def access_token(self) -> str | None:
        """Return the current access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """Return the current refresh token."""
        return self._refresh_token

    async def async_login(self, email: str, password: str) -> None:
        """Sign in with transient credentials and retain the returned tokens."""
        data = await self._request_json(
            "POST",
            "/api/v2/pp/auth/login",
            json_body={"email": email, "password": password},
            authenticated=False,
        )
        self._store_tokens(data)
        if self.user_id is None:
            raise EdulogAuthError("The login response did not include a user ID.")

    def _store_tokens(self, data: Any) -> None:
        if not isinstance(data, Mapping):
            raise EdulogAuthError("The login response was not a JSON object.")

        access_token = data.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise EdulogAuthError("The login response did not include an access token.")

        self._access_token = access_token
        refresh_token = data.get("refreshToken")
        self._refresh_token = (
            refresh_token
            if isinstance(refresh_token, str) and refresh_token
            else None
        )
        self._access_token_expires_at = _jwt_expiration(access_token)
        person_id = data.get("personId")
        if isinstance(person_id, str) and person_id:
            self.user_id = person_id
        elif self.user_id is None:
            self.user_id = _jwt_subject(access_token)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        authenticated: bool = True,
        _retry_on_unauthorized: bool = True,
    ) -> Any:
        request_headers = {"Accept": "application/json"}
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        if authenticated and self._is_access_token_expiring():
            await self.async_refresh_token(force=False)
        request_access_token = self._access_token
        if authenticated:
            if not self._access_token:
                raise EdulogAuthError("Edulog access token is missing.")
            request_headers["Authorization"] = f"Bearer {self._access_token}"

        async with self._session.request(
            method,
            f"{API_BASE_URL}{path}",
            params=params,
            json=json_body,
            headers=request_headers,
        ) as response:
            if response.status == 401 and authenticated:
                await response.read()
                if self._refresh_token and _retry_on_unauthorized:
                    await self.async_refresh_token(
                        force=True,
                        expected_access_token=request_access_token,
                    )
                    return await self._request_json(
                        method,
                        path,
                        params=params,
                        json_body=json_body,
                        headers=headers,
                        authenticated=True,
                        _retry_on_unauthorized=False,
                    )
                raise EdulogAuthError("Edulog access token expired or was rejected.")
            if response.status >= 400:
                await response.read()
                raise EdulogHttpError(response.status)
            return await response.json(content_type=None)

    def _is_access_token_expiring(self) -> bool:
        """Return whether the access token expires within the refresh margin."""
        return (
            self._access_token_expires_at is not None
            and self._access_token_expires_at
            <= time.time() + _TOKEN_REFRESH_MARGIN_SECONDS
        )

    async def async_refresh_token(
        self,
        *,
        force: bool = True,
        expected_access_token: str | None = None,
    ) -> None:
        """Refresh access credentials and persist any rotated refresh token."""
        async with self._refresh_lock:
            if (
                expected_access_token is not None
                and self._access_token != expected_access_token
            ):
                return
            if not force and not self._is_access_token_expiring():
                return
            if not self._refresh_token:
                raise EdulogAuthError("Edulog refresh token is missing.")

            async with self._session.request(
                "POST",
                f"{API_BASE_URL}{_TOKEN_REFRESH_PATH}",
                json={"refreshToken": self._refresh_token},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status in (400, 401, 403):
                    await response.read()
                    raise EdulogAuthError(
                        "Edulog refresh token expired or was rejected."
                    )
                if response.status >= 400:
                    await response.read()
                    raise EdulogHttpError(response.status)
                data = await response.json(content_type=None)

            if not isinstance(data, Mapping):
                raise EdulogApiError(
                    "The token refresh response was not a JSON object."
                )

            access_token = data.get("accessToken")
            if not isinstance(access_token, str) or not access_token:
                raise EdulogApiError(
                    "The token refresh response did not include an access token."
                )
            refresh_token = data.get("refreshToken")
            self._access_token = access_token
            if isinstance(refresh_token, str) and refresh_token:
                self._refresh_token = refresh_token
            self._access_token_expires_at = _jwt_expiration(access_token)
            if self._token_update_callback and self._refresh_token:
                self._token_update_callback(self._access_token, self._refresh_token)
            _LOGGER.info("Successfully refreshed Edulog access token")

    async def async_get_students(self) -> list[dict[str, Any]]:
        """Return the account's approved and pending student access requests."""
        if self.user_id is None:
            raise EdulogAuthError("No account ID is available after login.")
        data = await self._request_json(
            "GET",
            f"/api/v2/pp/users/{quote(self.user_id, safe='')}/studentAccessRequests",
            params={"status": "approved,pending,rejected"},
        )
        records = _extract_records(
            data,
            ("studentAccessRequests", "result", "content", "data"),
        )
        if records is None:
            raise EdulogApiError("The student list response had an unexpected format.")
        return [record for record in records if isinstance(record, dict)]

    async def async_get_trips(
        self, students: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Return trip records for the provided student and tenant IDs."""
        if not students:
            return []
        tenant_id = students[0]["tenantId"]
        headers = {"x-v2-tenant-id": tenant_id} if tenant_id else {}
        data = await self._request_json(
            "POST",
            "/api/v2/pp/students/trips",
            json_body=students,
            headers=headers,
        )
        records = _extract_records(data, ("transportationLookups", "result", "content", "data"))
        if records is None:
            raise EdulogApiError("The student trips response had an unexpected format.")
        return [record for record in records if isinstance(record, dict)]

    async def async_get_vehicle_location(
        self, vehicle_id: str, tenant_id: str
    ) -> dict[str, Any] | None:
        """Fetch the latest live location event for a vehicle."""
        headers = {"x-v2-tenant-id": tenant_id} if tenant_id else {}
        data = await self._request_json(
            "GET",
            "/api/v2/pp/vehicleliveevents",
            params={
                "sourceApp": "PP_v2",
                "vehicleId": vehicle_id,
                "tenantId": tenant_id,
            },
            headers=headers,
        )
        records = _extract_records(data, ("result", "content", "data", "events"))
        if records is None:
            records = [data] if isinstance(data, dict) else []
        for event in reversed(records):
            if not isinstance(event, dict):
                continue
            latitude = _number(event.get("latitude"))
            longitude = _number(event.get("longitude"))
            if (
                latitude is not None
                and -90 <= latitude <= 90
                and longitude is not None
                and -180 <= longitude <= 180
            ):
                return {
                    "latitude": latitude,
                    "longitude": longitude,
                    "heading": _number(event.get("heading")),
                    "speed": _number(event.get("speed")),
                    "vehicleId": vehicle_id,
                }
        return None


def _extract_records(
    payload: Any, wrapper_keys: tuple[str, ...]
) -> list[Any] | None:
    """Unwrap common Edulog response envelopes to their record list."""
    current = payload
    for _ in range(4):
        if isinstance(current, list):
            return current
        if not isinstance(current, Mapping):
            return None
        next_value = next(
            (current[key] for key in wrapper_keys if key in current), None
        )
        if next_value is None:
            return None
        current = next_value
    return current if isinstance(current, list) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _jwt_expiration(token: str | None) -> float | None:
    """Read the access token expiration claim without validating the token."""
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    expiration = claims.get("exp") if isinstance(claims, Mapping) else None
    if isinstance(expiration, bool) or not isinstance(expiration, (int, float)):
        return None
    return float(expiration) if isfinite(expiration) else None


def _jwt_subject(token: str | None) -> str | None:
    """Read the account subject claim without validating the token."""
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    subject = claims.get("sub") if isinstance(claims, Mapping) else None
    return subject if isinstance(subject, str) and subject else None
