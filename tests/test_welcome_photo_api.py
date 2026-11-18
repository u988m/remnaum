"""Safe upload and access control for the owner's welcome image."""
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.web.routes import router


class WelcomePhotoApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_upload_and_delete_require_admin_auth(self):
        response = await self.client.post(
            "/admin/api/welcome-photo", content=b"\xff\xd8\xffdata",
            headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(response.status_code, 401)
        response = await self.client.delete("/admin/api/welcome-photo")
        self.assertEqual(response.status_code, 401)
        response = await self.client.get("/admin/api/welcome-photo")
        self.assertEqual(response.status_code, 401)

    async def test_admin_can_upload_and_remove_photo(self):
        sent = types.SimpleNamespace(
            message_id=1, photo=[types.SimpleNamespace(file_id="AgAC_123-X")],
        )
        bot = Mock(
            send_photo=AsyncMock(return_value=sent),
            delete_message=AsyncMock(),
        )
        authorized = AsyncMock(return_value={"id": 123456})
        updated = AsyncMock(return_value={})
        with patch("app.web.routes._require_telegram_admin", authorized), \
             patch("app.web.routes.bot", bot), \
             patch("app.web.routes.update_store_profile", updated):
            response = await self.client.post(
                "/admin/api/welcome-photo", content=b"\xff\xd8\xffsample",
                headers={"Content-Type": "image/jpeg"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            updated.assert_awaited_with({"welcome_photo_file_id": "AgAC_123-X"})
            response = await self.client.delete("/admin/api/welcome-photo")
            self.assertEqual(response.status_code, 200)
            updated.assert_awaited_with({"welcome_photo_file_id": ""})

    async def test_rejects_invalid_image_and_large_payload(self):
        authorized = AsyncMock(return_value={"id": 123456})
        with patch("app.web.routes._require_telegram_admin", authorized):
            bad = await self.client.post(
                "/admin/api/welcome-photo", content=b"not an image",
                headers={"Content-Type": "image/png"},
            )
            self.assertEqual(bad.status_code, 422)
            huge = await self.client.post(
                "/admin/api/welcome-photo", content=b"\xff\xd8\xff" + b"A"*(5*1024*1024),
                headers={"Content-Type": "image/jpeg"},
            )
            self.assertEqual(huge.status_code, 413)


if __name__ == "__main__":
    unittest.main()
