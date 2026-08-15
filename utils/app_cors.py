"""CORS policy for the local Tauri shell and local development previews."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


PACKAGED_DESKTOP_ORIGIN_PATTERN = r"^(?:tauri://localhost|https?://tauri\.localhost)$"
DEVELOPMENT_ORIGIN_PATTERN = r"^http://(?:localhost|127\.0\.0\.1):5173$"


def install_desktop_cors(
    app: FastAPI,
    *,
    allow_development_origins: bool = False,
) -> None:
    origin_pattern = PACKAGED_DESKTOP_ORIGIN_PATTERN
    if allow_development_origins:
        origin_pattern = (
            rf"(?:{PACKAGED_DESKTOP_ORIGIN_PATTERN}|{DEVELOPMENT_ORIGIN_PATTERN})"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=origin_pattern,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Javis-Runtime-Capability",
        ],
    )
