from datetime import datetime, timezone
import json
from typing import Any

import httpx

from app.config import settings


class RemnawaveClient:
    """HTTP adapter for the Remnawave Panel users and subscriptions API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.remnawave_base_url).rstrip("/")
        self.token = token if token is not None else settings.remnawave_api_token
        self.transport = transport

    @staticmethod
    def _iso_datetime(value: datetime | str) -> str:
        if isinstance(value, str):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.base_url or not self.token:
            raise RuntimeError("Remnawave API is not configured")

        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token}"

        async with httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            headers=headers,
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path.lstrip("/"), **kwargs)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None
            return response.json()

    async def create_user(
        self,
        username: str,
        expire_at: datetime | str,
        *,
        telegram_id: int | None = None,
        traffic_limit_bytes: int = 0,
        traffic_limit_strategy: str = "NO_RESET",
        hwid_device_limit: int | None = None,
        active_internal_squads: list[str] | None = None,
        description: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "username": username,
            "expireAt": self._iso_datetime(expire_at),
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": traffic_limit_strategy,
        }
        optional = {
            "telegramId": telegram_id,
            "hwidDeviceLimit": hwid_device_limit,
            "activeInternalSquads": active_internal_squads,
            "description": description,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return await self.request("POST", "/api/users", json=payload)

    async def get_user(self, user_id: int | str) -> Any:
        return await self.request("GET", f"/api/users/{user_id}")

    async def list_users_page(self, *, start: int, size: int) -> Any:
        return await self.request("GET", "/api/users", params={"start": start, "size": size})

    async def get_user_by_username(self, username: str) -> Any:
        return await self.request("GET", f"/api/users/by-username/{username}")

    async def get_users_by_telegram_id(self, telegram_id: int) -> Any:
        try:
            return await self.request("GET", f"/api/users/by-telegram-id/{telegram_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        return await self.request("GET", "/api/users", params={
            "filters": json.dumps([{"id": "telegramId", "value": str(telegram_id)}]), "size": 50,
        })

    async def update_user(
        self,
        user_id: int | str,
        *,
        traffic_limit_bytes: int,
        hwid_device_limit: int,
        active_internal_squads: list[str] | None = None,
        expire_at: datetime | str | None = None,
        description: str | None = None,
    ) -> Any:
        identity: dict[str, Any] = ({"id": int(user_id)} if str(user_id).isdigit()
                                     else {"uuid": str(user_id)})
        payload: dict[str, Any] = {
            **identity,
            "status": "ACTIVE",
            "trafficLimitBytes": traffic_limit_bytes,
            "hwidDeviceLimit": hwid_device_limit,
        }
        if active_internal_squads is not None:
            payload["activeInternalSquads"] = active_internal_squads
        if expire_at is not None:
            payload["expireAt"] = self._iso_datetime(expire_at)
        if description is not None:
            payload["description"] = description
        return await self.request("PATCH", "/api/users", json=payload)

    async def enable_user(self, user_id: int | str) -> Any:
        return await self.request("POST", f"/api/users/{user_id}/actions/enable")

    async def disable_user(self, user_id: int | str) -> Any:
        return await self.request("POST", f"/api/users/{user_id}/actions/disable")

    async def extend_user(self, user_id: int | str, days: int) -> Any:
        if days < 1:
            raise ValueError("days must be greater than or equal to 1")
        return await self.request(
            "POST",
            f"/api/users/{user_id}/actions/extend",
            json={"days": days},
        )

    async def get_subscription(self, user_id: int | str) -> Any:
        endpoint = ("by-id" if str(user_id).isdigit() else "by-uuid")
        return await self.request("GET", f"/api/subscriptions/{endpoint}/{user_id}")


    async def resolve_user_id(
        self, stored_id: int | str, *, username: str | None = None
    ) -> int | str:
        """Resolve legacy UUID subscriptions to numeric IDs used by Panel 3.x.

        Remnawave 2.x accepts UUID for older APIs. Username lookup has remained
        stable in both 2.x and 3.x; never look up another customer's identity.
        """
        if str(stored_id).isdigit():
            return int(stored_id)
        if not username:
            return stored_id
        result = await self.get_user_by_username(username)
        user = result.get("response", result) if isinstance(result, dict) else {}
        if isinstance(user, dict):
            value = user.get("id")
            if isinstance(value, int) and value > 0:
                return value
        return stored_id


    async def get_hwid_devices(self, user_id: int | str) -> Any:
        """List hardware-bound devices for a user (Panel 3.x)."""
        return await self.request("GET", f"/api/hwid/devices/{user_id}")

    async def delete_hwid_device(self, user_id: int | str, hwid: str) -> Any:
        identity = {"userId": int(user_id)} if str(user_id).isdigit() else {"userUuid": str(user_id)}
        return await self.request("POST", "/api/hwid/devices/delete", json={**identity, "hwid": hwid})
