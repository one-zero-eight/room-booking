import asyncio
import datetime
from typing import cast

from src.api.logging_ import logger
from src.modules.bookings.exchange_repository import exchange_booking_repository
from src.modules.bookings.schemas import BookingStatus, BookingStatusModel
from src.modules.bookings.utils import get_emails_to_attendees_index

daemons_cache: dict[str, asyncio.Task[BookingStatusModel | None]] = {}


def create_or_get_booking_status_daemon(
    item_id: str, room_email_address: str
) -> asyncio.Task[BookingStatusModel | None]:
    if item_id not in daemons_cache:
        daemons_cache[item_id] = asyncio.create_task(
            daemon_waiting_for_booking_status(item_id=item_id, room_email_address=room_email_address)
        )
    return daemons_cache[item_id]


async def daemon_waiting_for_booking_status(item_id: str, room_email_address: str) -> BookingStatusModel | None:
    tries = 10
    await asyncio.sleep(3)
    while tries > 0:
        tries -= 1

        calendar_item = await asyncio.to_thread(exchange_booking_repository.get_booking, item_id)
        if calendar_item is None:
            return None

        email_index = get_emails_to_attendees_index(calendar_item)
        room_attendee = email_index.get(room_email_address)

        if room_attendee is None:
            logger.warning(f"Room attendee not found for {room_email_address=}: {room_attendee=} in {item_id=}")
            return None

        if room_attendee.last_response_time is not None:
            logger.info(f"Booking status: {room_attendee.response_type} at {room_attendee.last_response_time}")

            conversation_history = await asyncio.to_thread(
                exchange_booking_repository.get_conversation_history, calendar_item.conversation_id
            )

            if room_attendee.response_type == "Decline":
                await asyncio.to_thread(
                    exchange_booking_repository.delete_booking,
                    item_id=item_id,
                    email=room_email_address,
                )

            return BookingStatusModel(
                room_id=room_email_address,
                status=cast(BookingStatus, room_attendee.response_type),
                last_response_time=cast(datetime.datetime | None, room_attendee.last_response_time),
                conversation_history=conversation_history,
            )
        else:
            logger.info("Waiting for booking status...")
            await asyncio.sleep(1)
    return BookingStatusModel(
        room_id=room_email_address, status="Unknown", last_response_time=None, conversation_history=None
    )
