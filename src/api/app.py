__all__ = ["app"]

import exchangelib.errors
from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi_swagger import patch_fastapi
from starlette.middleware.cors import CORSMiddleware

from src.api import docs
from src.api.lifespan import lifespan
from src.api.logging_ import logger  # noqa: F401
from src.config import settings

# App definition
app = FastAPI(
    title=docs.TITLE,
    summary=docs.SUMMARY,
    description=docs.DESCRIPTION,
    version=docs.VERSION,
    contact=docs.CONTACT_INFO,
    license_info=docs.LICENSE_INFO,
    openapi_tags=docs.TAGS_INFO,
    servers=[
        {"url": settings.app_root_path, "description": "Current"},
        {
            "url": "https://api.innohassle.ru/room-booking/v0",
            "description": "Production environment",
        },
        {
            "url": "https://api.innohassle.ru/room-booking/staging-v0",
            "description": "Staging environment",
        },
    ],
    root_path=settings.app_root_path,
    root_path_in_servers=False,
    generate_unique_id_function=docs.generate_unique_operation_id,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)

patch_fastapi(app)

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from src.modules.bookings.routes import router as router_bookings  # noqa: E402
from src.modules.rooms.routes import router as router_rooms  # noqa: E402

app.include_router(router_rooms)
app.include_router(router_bookings)


@app.exception_handler(exchangelib.errors.EWSError)
async def ews_error_handler(
    request: Request,
    exc: exchangelib.errors.EWSError,
):
    logger.warning(f"EWS error, probably Outlook is down: {exc}", exc_info=True)
    return JSONResponse(status_code=429, content={"detail": f"EWS error, probably Outlook is down: {exc}"})
