"""CORS policy for the local Tauri shell and local development previews."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


DESKTOP_ORIGIN_PATTERN = (
    r"^(?:tauri://localhost|https?://tauri\.localhost|"
    r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?)$"
)


def install_desktop_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=DESKTOP_ORIGIN_PATTERN,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
