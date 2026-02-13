__all__ = ["app"]

import datetime
import pprint
import time

import exchangelib.errors
from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response
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


last_callback_time: datetime.datetime | None = None


@app.post("/ews-callback")
async def ews_callback(request: Request):
    """
    EWS callback endpoint for push subscription.
    https://ecederstrand.github.io/exchangelib/#synchronization-subscriptions-and-notifications
    """
    from collections.abc import Iterable
    from typing import cast

    from exchangelib.properties import (
        ModifiedEvent,
        Notification,
        TimestampEvent,
    )
    from exchangelib.services import SendNotification

    from src.modules.bookings.exchange_repository import exchange_booking_repository
    from src.modules.bookings.service import get_emails_to_attendees_index

    ws = SendNotification(protocol=None)
    for notification in ws.parse(await request.body()):
        # ws.parse() returns Notification objects

        logger.info("Notification from Exchange")
        if not isinstance(notification, Notification):
            logger.warning("Notification from Exchange is not a Notification object")
            continue

        if notification.subscription_id != exchange_booking_repository.subscription_id:
            logger.warning("Notification from Exchange with wrong subscription ID")
            continue

        exchange_booking_repository.last_callback_time = time.monotonic()  # used for subscription restart

        for event in cast(Iterable[TimestampEvent], notification.events):
            logger.info(f"Event: {type(event)}\n{pprint.pformat(event, sort_dicts=False, compact=True)}")

            if isinstance(event, ModifiedEvent):
                if event.item_id is not None:
                    booking = await exchange_booking_repository.get_booking(event.item_id.id)
                    if booking is None:
                        logger.warning("Booking not found")
                        continue
                    email_index = get_emails_to_attendees_index(booking)

                    for email, attendee in email_index.items():
                        if attendee.response_type == "Decline":
                            logger.warning(f"Attendee ({email}) declined the booking, so we will delete this booking")
                            await exchange_booking_repository.delete_booking(event.item_id.id, email)
                            logger.info(f"Booking deleted: {event.item_id.id}")
                            break

    data = ws.ok_payload()
    # # Or, if you want to end the subscription:
    # data = ws.unsubscribe_payload()

    return Response(content=data, status_code=201, media_type="text/xml; charset=utf-8")
