import asyncio
import datetime
import itertools
from typing import Generator

import httpx
import icalendar
from pydantic import BaseModel

from src.api.logging_ import logger
from src.config import settings
from src.modules.rooms.repository import room_repository


class Booking(BaseModel):
    room_id: str
    "ID of the room"
    title: str
    "Title of the booking"
    start: datetime.datetime
    "Start time of booking"
    end: datetime.datetime
    "End time of booking"


class BookingRepository:
    bookings_cache: dict[str, tuple[list[Booking], datetime.datetime]] = {}

    async def fetch_bookings(self, room_id: str) -> list[Booking] | None:
        room = await room_repository.get_by_id(room_id)
        if not room:
            return None

        cached_ics, cached_dt = self.bookings_cache.get(room_id, (None, None))
        if cached_ics and cached_dt:
            if datetime.datetime.now() - cached_dt < datetime.timedelta(seconds=settings.ics_cache_ttl_seconds):
                return cached_ics

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(room.ics_url, timeout=30)
                response.raise_for_status()

                ics = response.text
                bookings = list(self.extract_bookings_from_ics(room_id, ics))
                self.bookings_cache[room_id] = (bookings, datetime.datetime.now())
                return bookings
            except Exception as error:
                logger.warning(f"Failed to fetch ics: {error}")
                return cached_ics

    def extract_bookings_from_ics(self, room_id: str, ics: str) -> Generator[Booking, None, None]:
        calendar = icalendar.Calendar.from_ical(ics)
        vevents = calendar.walk(name="VEVENT")
        for event in vevents:
            try:
                busy: icalendar.vText | None = event["X-MICROSOFT-CDO-BUSYSTATUS"]
                if busy and busy.lower() == "free":
                    continue  # The event is cancelled

                yield Booking(
                    room_id=room_id,
                    title=event["SUMMARY"] or "",
                    start=to_msk(to_datetime(event["DTSTART"].dt)),
                    end=to_msk(to_datetime(event["DTEND"].dt)),
                )
            except Exception as error:
                logger.warning(f"Failed to parse event: {error}, {event}")

    async def get_bookings_for_all_rooms(self, from_dt: datetime.datetime, to_dt: datetime.datetime):
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)

        async def task(room_id: str) -> list[Booking]:
            bookings = await self.fetch_bookings(room_id)
            if not bookings:
                return []

            return [booking for booking in bookings if booking.start < to_dt and booking.end > from_dt]

        lists = await asyncio.gather(*[task(room.id) for room in await room_repository.get_all()])
        return itertools.chain(*lists)


def to_datetime(dt: datetime.datetime | datetime.date) -> datetime.datetime:
    if isinstance(dt, datetime.datetime):
        return dt
    return datetime.datetime.combine(dt, datetime.time.min)


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=3)))


booking_repository: BookingRepository = BookingRepository()
