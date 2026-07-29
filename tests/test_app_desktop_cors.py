import unittest

import httpx
from fastapi import FastAPI


class AppDesktopCorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_tauri_desktop_origin_can_preflight_post_requests(self):
        from utils.app_cors import install_desktop_cors

        app = FastAPI()
        install_desktop_cors(app)

        @app.post("/action")
        async def action():
            return {"ok": True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.options(
                "/action",
                headers={
                    "Origin": "http://tauri.localhost",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://tauri.localhost")
        self.assertIn("POST", response.headers["access-control-allow-methods"])

    async def test_unrelated_remote_origin_is_not_allowed(self):
        from utils.app_cors import install_desktop_cors

        app = FastAPI()
        install_desktop_cors(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.options(
                "/action",
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )

        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
