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

    async def test_development_origin_requires_the_explicit_flag(self):
        from utils.app_cors import install_desktop_cors

        async def preflight(allow_development_origins):
            app = FastAPI()
            install_desktop_cors(
                app,
                allow_development_origins=allow_development_origins,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                return await client.options(
                    "/action",
                    headers={
                        "Origin": "http://localhost:5173",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "x-javis-runtime-capability",
                    },
                )

        denied = await preflight(False)
        allowed = await preflight(True)
        self.assertNotIn("access-control-allow-origin", denied.headers)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"],
            "http://localhost:5173",
        )


if __name__ == "__main__":
    unittest.main()
