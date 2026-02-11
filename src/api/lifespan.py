__all__ = ["lifespan"]

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.logging_ import logger
from src.modules.bookings.exchange_repository import exchange_booking_repository
from src.modules.inh_accounts_sdk import inh_accounts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Application startup
    await inh_accounts.update_key_set()

    async def print_exchanglelib_statu():
        status = await exchange_booking_repository.get_server_status()
        if status:
            logger.info(f"Exchange server status: {status}")
        else:
            logger.error("Failed to get exchange server status")

    asyncio.create_task(print_exchanglelib_statu())
    yield
