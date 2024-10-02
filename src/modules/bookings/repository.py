import asyncio
import datetime
import itertools
import re
from time import perf_counter
from traceback import format_exc

import httpx
import pytz
from dateutil.rrule import rrulestr
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


# noinspection PyMethodMayBeStatic
class BookingRepository:
    bookings_cache: dict[str, tuple[list[Booking], datetime.datetime]] = {}
    _async_events: dict[str, asyncio.Event | None] = {}
    _async_semaphore = asyncio.Semaphore(5)
    _client = httpx.AsyncClient()

    def get_cached_bookings(self, room_id: str, use_ttl: bool = False) -> list[Booking] | None:
        cached_bookings, cached_dt = self.bookings_cache.get(room_id, (None, None))
        if cached_bookings is not None and cached_dt is not None:
            if not use_ttl:
                return cached_bookings
            if datetime.datetime.now() - cached_dt < datetime.timedelta(seconds=settings.ics_cache_ttl_seconds):
                return cached_bookings

    async def fetch_bookings(self, room_id: str) -> list[Booking] | None:
        room = await room_repository.get_by_id(room_id)
        if not room:
            return None

        if cached_bookings := self.get_cached_bookings(room_id, use_ttl=True):
            logger.debug(f"Using cached bookings for room {room_id} with ttl")
            return cached_bookings

        if event := self._async_events.get(room_id):
            logger.debug(f"Already fetching bookings for room {room_id}")
            await event.wait()
            logger.debug(f"Using cached bookings from concurrent job for room {room_id}")
            return self.get_cached_bookings(room_id, use_ttl=False)

        # no event setted
        event = asyncio.Event()
        self._async_events[room_id] = event
        logger.debug(f"Job started to fetch bookings for room {room_id}")

        try:
            async with self._async_semaphore:
                logger.debug(f"[{self._async_semaphore._value}] Fetching ics for room {room_id}...")
                response = await self._client.get(room.ics_url, timeout=30)
            response.raise_for_status()

            ics = response.text
            _t1 = perf_counter()
            bookings = self.extract_bookings_from_ics(room_id, ics)
            _t2 = perf_counter()
            logger.debug(f"Parsed ics for room {room_id} in {_t2 - _t1:.2f}s")
            self.bookings_cache[room_id] = (bookings, datetime.datetime.now())
            return bookings
        except httpx.ReadTimeout:
            logger.warning("Failed to fetch ics: Timeout")
            return self.get_cached_bookings(room_id, use_ttl=False)
        except:  # noqa: E722
            logger.warning(f"Failed to fetch ics: {format_exc()}")
            return self.get_cached_bookings(room_id, use_ttl=False)
        finally:
            logger.debug(f"Notifying other tasks that bookings for room {room_id} are ready")
            event.set()
            self._async_events[room_id] = None

    dt_pattern = re.compile(r"((\d{8}T\d{6})|(\d{8}))")

    def extract_bookings_from_ics(self, room_id: str, ics: str) -> list[Booking]:
        vevents = ics.split("BEGIN:VEVENT")
        if not vevents:
            return []

        bookings = []

        for event in vevents[1:]:
            try:
                event = event.replace("\r\n ", "")
                busy = title = start = end = allday = rrule = None
                splitted = event.split("\r\n")

                for line in splitted:
                    if line.startswith("SUMMARY:"):
                        title = line[8:]
                    elif line.startswith("DTSTART"):  # ...20240710T110000 or ... 20240811
                        dt = self.dt_pattern.search(line[8:])
                        if dt:
                            as_string = dt.group()
                            if len(as_string) == 15:
                                start = datetime.datetime(
                                    year=int(as_string[:4]),
                                    month=int(as_string[4:6]),
                                    day=int(as_string[6:8]),
                                    hour=int(as_string[9:11]),
                                    minute=int(as_string[11:13]),
                                    second=int(as_string[13:15]),
                                )
                            else:
                                start = datetime.datetime(
                                    year=int(as_string[:4]),
                                    month=int(as_string[4:6]),
                                    day=int(as_string[6:8]),
                                )
                    elif line.startswith("DTEND"):
                        dt = self.dt_pattern.search(line[6:])
                        if dt:
                            as_string = dt.group()
                            if len(as_string) == 15:
                                end = datetime.datetime(
                                    year=int(as_string[:4]),
                                    month=int(as_string[4:6]),
                                    day=int(as_string[6:8]),
                                    hour=int(as_string[9:11]),
                                    minute=int(as_string[11:13]),
                                    second=int(as_string[13:15]),
                                )
                            else:
                                end = datetime.datetime(
                                    year=int(as_string[:4]), month=int(as_string[4:6]), day=int(as_string[6:8])
                                )
                    elif line.startswith("X-MICROSOFT-CDO-BUSYSTATUS:"):
                        busy = line[27:]
                    elif line.startswith("X-MICROSOFT-CDO-ALLDAYEVENT:"):
                        allday = line[28:]
                    elif line.startswith("RRULE:"):
                        rrule = line[6:]
                if busy and busy.upper() == "FREE":
                    logger.debug(f"Skipping event: {title}, {start}, {end}")
                    continue  # The event is cancelled
                if not start or not end:
                    logger.warning(f"Failed to parse event: {event}: {title}, {start}, {end}")
                    continue
                end = to_msk(end)
                start = to_msk(start)
                title = title or ""
                if allday and allday.upper() == "TRUE":
                    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                    end = start.replace(hour=23, minute=59, second=59, microsecond=0)

                    if rrule:
                        for dt in rrulestr(rrule, dtstart=start):
                            bookings.append(
                                Booking(
                                    room_id=room_id,
                                    title=title,
                                    start=dt,
                                    end=dt + (end - start),
                                )
                            )
                    else:
                        bookings.append(Booking(room_id=room_id, title=title, start=start, end=end))
                else:
                    if rrule:
                        for dt in rrulestr(rrule, dtstart=start):
                            bookings.append(
                                Booking(
                                    room_id=room_id,
                                    title=title,
                                    start=dt,
                                    end=dt + (end - start),
                                )
                            )
                    else:
                        bookings.append(Booking(room_id=room_id, title=title, start=start, end=end))
            except Exception as error:
                logger.warning(f"Failed to parse event: {error}, {event}")
        return bookings

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


_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(_timezone)


booking_repository: BookingRepository = BookingRepository()
