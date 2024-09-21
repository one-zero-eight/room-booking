__all__ = ["app"]

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from fastapi_swagger import patch_fastapi

import src.api.logging_  # noqa: F401
from src.api.docs import generate_unique_operation_id, custom_openapi
from src.api.lifespan import lifespan
from src.api.routers import routers
from src.config import settings
from src.config_schema import Environment

# App definition
app = FastAPI(
    root_path=settings.app_root_path,
    root_path_in_servers=False,
    generate_unique_id_function=generate_unique_operation_id,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)

patch_fastapi(app)

app.openapi = custom_openapi(app)  # type: ignore

# CORS settings
if settings.cors_allow_origins:
    # noinspection PyTypeChecker
    app.add_middleware(
        middleware_class=CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_origin_regex=".*" if settings.environment == Environment.DEVELOPMENT else None,
    )


for router in routers:
    app.include_router(router)
