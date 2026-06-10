__all__ = ["lifespan"]

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.logging_ import logger
from src.config import settings
from src.modules.bmp.repository import bmp_repository
from src.modules.bookings.exchange_repository import exchange_booking_repository
from src.modules.inh_accounts_sdk import inh_accounts


async def _log_bmp_calendar() -> None:
    try:
        calendar = await asyncio.to_thread(lambda: bmp_repository.selected_calendar)
    except Exception:
        logger.exception(f"Failed to resolve BMP calendar for {bmp_repository.account_email}")
        return
    logger.info(
        f"BMP calendar: name={calendar.name} id={calendar.id} path={calendar.absolute} "
        f"account={bmp_repository.account_email}"
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Application startup
    await inh_accounts.update_key_set()

    await _log_bmp_calendar()

    await bmp_repository.start_inbox_poller()
    await exchange_booking_repository.start_inbox_poller()

    async def print_exchanglelib_status_and_start_subscription():
        status = await exchange_booking_repository.get_server_status()
        if status:
            logger.info(f"Exchange server status: {status}")
        else:
            logger.error("Failed to get exchange server status")

        if settings.exchange.ews_callback_url is not None:
            logger.info(f"Starting exchange subscription to {settings.exchange.ews_callback_url}")

            while True:
                now = time.monotonic()
                # If ews doesn't respond for 2 minutes, we need to restart the subscription
                if (
                    exchange_booking_repository.last_callback_time is None
                    or (now - exchange_booking_repository.last_callback_time) > 60 * 2
                ):
                    subscription = await exchange_booking_repository.push_subscription(
                        callback_url=settings.exchange.ews_callback_url
                    )
                    logger.info(f"Exchange subscription started: {subscription=}")
                await asyncio.sleep(60)  # Wait 1 minute before checking again

    asyncio.create_task(print_exchanglelib_status_and_start_subscription())

    yield

    await bmp_repository.stop_inbox_poller()
    await exchange_booking_repository.stop_inbox_poller()
