import httpx
from app.config import settings

class RemnawaveClient:
    """Isolated Remnawave API adapter. Add endpoint methods against the installed panel API version."""

    def __init__(self) -> None:
        self.base_url = settings.remnawave_base_url.rstrip("/")
        self.token = settings.remnawave_api_token

    async def request(self, method: str, path: str, **kwargs):
        if not self.base_url or not self.token:
            raise RuntimeError("Remnawave API is not configured")
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=20) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None
