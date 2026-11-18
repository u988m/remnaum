from datetime import datetime, timezone
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

    async def get_user_by_username(self, username: str) -> Any:
        return await self.request("GET", f"/api/users/by-username/{username}")

    async def update_user(
        self,
        user_id: int | str,
        *,
        traffic_limit_bytes: int,
        hwid_device_limit: int,
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


    async def get_hwid_devices(self, user_id: int | str) -> Any:
        """List hardware-bound devices for a user (Panel 3.x)."""
        return await self.request("GET", f"/api/hwid/devices/{user_id}")
