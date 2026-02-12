import asyncio
import datetime
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import TypedDict, cast

import exchangelib
import exchangelib.errors
from exchangelib.properties import CalendarEvent
from exchangelib.services.get_user_availability import FreeBusyView

import src.modules.bookings.patch_exchangelib  # noqa
from src.api.logging_ import logger
from src.config import settings
from src.config_schema import Room
from src.modules.bookings.schemas import Booking
from src.modules.bookings.service import (
    calendar_item_to_booking,
    get_emails_to_attendees_index,
    get_first_room_attendee_from_emails,
    get_first_room_from_emails,
    to_msk,
)
from src.modules.rooms.repository import room_repository


class AccountCalendarViewArgs(TypedDict):
    start: datetime.datetime
    end: datetime.datetime


class AccountGetFreeBusyInfoArgs(TypedDict):
    rooms_ids: tuple[str, ...]
    accounts: tuple[tuple[str, str, bool], ...]
    start: datetime.datetime
    end: datetime.datetime


class ExchangeBookingRepository:
    ews_endpoint: str
    account_email: str
    account: exchangelib.Account

    def __init__(self, ews_endpoint: str, account_email: str):
        self.ews_endpoint = ews_endpoint
        self.account_email = account_email

        config = exchangelib.Configuration(
            auth_type=exchangelib.transport.NOAUTH,
            service_endpoint=self.ews_endpoint,
            version=exchangelib.Version(exchangelib.version.EXCHANGE_2016),
            max_connections=1,
        )
        self.account = exchangelib.Account(
            self.account_email,
            access_type=exchangelib.DELEGATE,
            config=config,
            autodiscover=False,
        )

    async def get_server_status(self) -> dict | None:
        try:
            t1 = time.monotonic()
            status = {}
            status["version"] = str(self.account.version)
            calendar_folder_info = await asyncio.to_thread(lambda: self.account.calendar)
            status["folder"] = str(calendar_folder_info)
            t2 = time.monotonic()
            status["time_taken"] = f"{t2 - t1:.2f} seconds"
            return status
        except Exception as e:
            logger.error(f"Error getting calendar folder info: {e}")
            return None

    _cache_bookings_from_busy_info: dict[
        str,
        tuple[list[Booking], datetime.datetime],
    ] = {}

    def _get_cached_bookings_from_busy_info(self, room_id: str, use_ttl: bool = True) -> list[Booking] | None:
        cached_bookings, cached_dt = self._cache_bookings_from_busy_info.get(room_id, (None, None))
        if cached_bookings is not None and cached_dt is not None:
            if not use_ttl:
                return cached_bookings
            if datetime.datetime.now() - cached_dt < datetime.timedelta(seconds=settings.ttl_bookings_from_busy_info):
                return cached_bookings

    _account_protocol_get_free_busy_info_task: (
        tuple[
            AccountGetFreeBusyInfoArgs,
            asyncio.Task[dict[str, list[CalendarEvent]]],
        ]
        | None
    ) = None

    async def _fetch_bookings_from_busy_info(
        self,
        rooms: list[Room],
        start: datetime.datetime,
        end: datetime.datetime,
        use_cache: bool = True,
        use_dedup: bool = True,
    ) -> dict[str, list[Booking]]:
        room_ids = {room.id for room in rooms}

        # ---- Get cached bookings ----
        if use_cache:
            room_x_cache: dict[str, list[Booking]] = {}
            for room_id in room_ids:
                if _cached_bookings := self._get_cached_bookings_from_busy_info(room_id, use_ttl=False):
                    room_x_cache[room_id] = _cached_bookings

            if room_ids.issubset(room_x_cache.keys()):  # cache hit
                logger.info(f"Cache hit for bookings from busy info for rooms: {room_ids}")
                return room_x_cache
            else:
                logger.info(f"Cache miss for bookings from busy info for rooms: {room_ids}")
        # ^^^^^

        # ---- Fetch account free busy info with only one request at a time ----
        rooms_ids = tuple(room.id for room in rooms)
        accounts = tuple((room.resource_email, "Resource", False) for room in rooms)
        args = AccountGetFreeBusyInfoArgs(
            rooms_ids=rooms_ids,
            accounts=accounts,
            start=to_msk(start),
            end=to_msk(end),
        )

        def task_account_get_free_busy_info(args: AccountGetFreeBusyInfoArgs) -> dict[str, list[CalendarEvent]]:
            account_free_busy_info = cast(
                Iterable[FreeBusyView],
                self.account.protocol.get_free_busy_info(
                    accounts=args["accounts"],
                    start=exchangelib.EWSDateTime.from_datetime(args["start"]),
                    end=exchangelib.EWSDateTime.from_datetime(args["end"]),
                    merged_free_busy_interval=5,
                ),
            )

            room_id_x_calendar_events: dict[str, list[CalendarEvent]] = {}

            for i, busy_info in enumerate(account_free_busy_info):
                room_id = args["rooms_ids"][i]
                if busy_info is not None and busy_info.calendar_events is not None:
                    room_id_x_calendar_events[room_id] = list(cast(Iterable[CalendarEvent], busy_info.calendar_events))
                else:
                    room_id_x_calendar_events[room_id] = []

            return room_id_x_calendar_events

        try:
            if not use_dedup or self._account_protocol_get_free_busy_info_task is None:
                logger.info(
                    f"Either dedup is disabled or no task is running, creating new task for time range: {args=}, {use_dedup=}"
                )
                task = asyncio.create_task(asyncio.to_thread(task_account_get_free_busy_info, args))
                self._account_protocol_get_free_busy_info_task = (args, task)
                room_id_x_calendar_events = await task
            else:
                existing_args, existing_task = self._account_protocol_get_free_busy_info_task

                if existing_args == args and not existing_task.done():
                    logger.info(
                        f"Deduplicate calendar items for same time range, so we will use existing task: {args=}"
                    )
                    room_id_x_calendar_events = await existing_task
                else:
                    logger.info(f"New time range for calendar items, so we will create new task: {args=}")
                    task = asyncio.create_task(asyncio.to_thread(task_account_get_free_busy_info, args))
                    self._account_protocol_get_free_busy_info_task = (args, task)
                    room_id_x_calendar_events = await task
        except Exception:
            self._account_protocol_get_free_busy_info_task = None
            raise
        # ^^^^^

        # ---- Convert EWS CalendarEvents to ours Bookings ----
        room_id_x_bookings: dict[str, list[Booking]] = defaultdict(list)
        for room_id, room_calendar_events in room_id_x_calendar_events.items():
            for calendar_event in room_calendar_events:
                room_id_x_bookings[room_id].append(
                    Booking(
                        room_id=room_id,
                        title=calendar_event.details.subject if calendar_event.details else "Busy",
                        start=calendar_event.start,
                        end=calendar_event.end,
                        outlook_booking_id=None,
                        attendees=None,  # busy info doesn't contain attendees info, we can fetch it from account calendar if needed
                    )
                )
        # ^^^^^

        # ---- Cache bookings ----
        for room_id, room_bookings in room_id_x_bookings.items():
            self._cache_bookings_from_busy_info[room_id] = (room_bookings, datetime.datetime.now())
        # ^^^^^

        return room_id_x_bookings

    _cache_bookings_from_account_calendar: dict[
        str,
        tuple[list[Booking], datetime.datetime],
    ] = {}

    def _get_cached_bookings_from_account_calendar(self, room_id: str, use_ttl: bool = True) -> list[Booking] | None:
        cached_bookings, cached_dt = self._cache_bookings_from_account_calendar.get(room_id, (None, None))
        if cached_bookings is not None and cached_dt is not None:
            if not use_ttl:
                return cached_bookings
            if datetime.datetime.now() - cached_dt < datetime.timedelta(
                seconds=settings.ttl_bookings_from_account_calendar
            ):
                return cached_bookings

    _account_calendar_view_task: (
        tuple[
            AccountCalendarViewArgs,
            asyncio.Task[list[exchangelib.CalendarItem]],
        ]
        | None
    ) = None

    async def _fetch_bookings_from_account_calendar(
        self,
        rooms: list[Room],
        start: datetime.datetime,
        end: datetime.datetime,
        use_cache: bool = True,
        use_dedup: bool = True,
    ) -> dict[str, list[Booking]]:
        rooms_ids = {room.id for room in rooms}

        # ---- Get cached bookings ----
        if use_cache:
            room_x_cache: dict[str, list[Booking]] = {}
            for room_id in rooms_ids:
                if _cached_bookings := self._get_cached_bookings_from_account_calendar(room_id, use_ttl=False):
                    room_x_cache[room_id] = _cached_bookings

            if rooms_ids.issubset(room_x_cache.keys()):  # cache hit
                logger.info(f"Cache hit for bookings from account calendar for rooms: {rooms_ids}")
                return room_x_cache
            else:
                logger.info(f"Cache miss for bookings from account calendar for rooms: {rooms_ids}")
        # ^^^^^

        # ---- Fetch account calendar view with only one request at a time ----
        args = AccountCalendarViewArgs(start=to_msk(start), end=to_msk(end))

        def task_account_calendar_view(args: AccountCalendarViewArgs) -> list[exchangelib.CalendarItem]:
            return list(
                self.account.calendar.view(
                    exchangelib.EWSDateTime.from_datetime(args["start"]),
                    exchangelib.EWSDateTime.from_datetime(args["end"]),
                )
            )

        try:
            if not use_dedup or self._account_calendar_view_task is None:
                logger.info(
                    f"Either dedup is disabled or no task is running, creating new task for time range: {args=}, {use_dedup=}"
                )
                task = asyncio.create_task(asyncio.to_thread(task_account_calendar_view, args))
                self._account_calendar_view_task = (args, task)
                calendar_items = await task
            else:
                existing_args, existing_task = self._account_calendar_view_task

                if existing_args == args and not existing_task.done():
                    logger.info(
                        f"Deduplicate calendar items for same time range, so we will use existing task: {args=}"
                    )
                    calendar_items = await existing_task
                else:
                    logger.info(f"New time range for calendar items, so we will create new task: {args=}")
                    task = asyncio.create_task(asyncio.to_thread(task_account_calendar_view, args))
                    self._account_calendar_view_task = (args, task)
                    calendar_items = await task
        except Exception:
            self._account_calendar_view_task = None
            raise
        # ^^^^^

        # ---- Convert EWS CalendarItems to ours Bookings ----
        room_x_bookings: dict[str, list[Booking]] = defaultdict(list)
        for item in calendar_items:
            email_index = get_emails_to_attendees_index(item)
            room = get_first_room_from_emails(email_index)

            if room is None:
                continue

            if room.id not in rooms_ids:
                continue

            room_attendee = email_index.get(room.resource_email)
            if room_attendee is None or room_attendee.response_type == "Decline":
                continue

            booking = calendar_item_to_booking(item, room_id=room.id)
            if booking is not None:
                room_x_bookings[room.id].append(booking)
        # ^^^^^

        # ---- Cache bookings ----
        for room_id, room_bookings in room_x_bookings.items():
            self._cache_bookings_from_account_calendar[room_id] = (room_bookings, datetime.datetime.now())
        # ^^^^^

        return room_x_bookings

    async def _fetch_user_bookings(
        self, attendee_email: str, start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        bookings_from_account_calendar = await self._fetch_bookings_from_account_calendar(
            rooms=room_repository.get_all(include_red=False),
            start=start,
            end=end,
            use_cache=False,
            use_dedup=True,
        )

        user_bookings = []
        for _room_id, bookings in bookings_from_account_calendar.items():
            for booking in bookings:
                if not booking.attendees:
                    continue

                accepted_by_rooms = all(
                    attendee.status != "Decline" for attendee in booking.attendees if attendee.assosiated_room_id
                )

                if not accepted_by_rooms:
                    continue

                user_attendee = next(
                    (attendee for attendee in booking.attendees if attendee.email == attendee_email), None
                )

                if user_attendee is None or user_attendee.status == "Decline":
                    continue

                user_bookings.append(booking)

        return user_bookings

    async def _fetch_bookings_all(
        self, room_ids: list[str], start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        rooms = room_repository.get_by_ids(room_ids)
        rooms = list(filter(None, rooms))
        if not rooms:
            return []

        bookings_from_account_calendar = await self._fetch_bookings_from_account_calendar(
            rooms=rooms,
            start=start,
            end=end,
            use_cache=True,
            use_dedup=True,
        )
        bookings_from_busy_info = await self._fetch_bookings_from_busy_info(
            rooms=rooms,
            start=start,
            end=end,
            use_cache=True,
            use_dedup=True,
        )

        account_calendar_registry = defaultdict(list)
        busy_info_registry = defaultdict(list)

        def key(booking: Booking) -> tuple[str, datetime.datetime, datetime.datetime]:
            return booking.room_id, booking.start, booking.end

        for _room_id, bookings in bookings_from_account_calendar.items():
            for booking in bookings:
                account_calendar_registry[key(booking)].append(booking)
        for _room_id, bookings in bookings_from_busy_info.items():
            for booking in bookings:
                busy_info_registry[key(booking)].append(booking)

        bookings = []

        for key in set(account_calendar_registry.keys()) | set(busy_info_registry.keys()):
            ac_bookings: list = account_calendar_registry[key]
            bi_bookings: list = busy_info_registry[key]
            conflicting_bookings_len = len(ac_bookings) + len(bi_bookings)

            # TODO: properly handle two sources of truth
            # We can return busy_info bookings and account bookings separately
            # with an intention that account booking info would be duplicated in busy_info.
            # busy_info would be used as source of truth for busyness of room
            # account bookings would be used as source of bookings made by service account

            if conflicting_bookings_len == 1:
                bookings.extend(ac_bookings + bi_bookings)
            elif ac_bookings:
                bookings.append(ac_bookings[0])
            else:
                bookings.extend(bi_bookings)

        bookings.sort(key=lambda x: x.start)

        return bookings

    async def get_bookings_for_all_rooms(
        self, from_dt: datetime.datetime, to_dt: datetime.datetime, include_red: bool = False
    ) -> list[Booking]:
        room_ids = [
            room.id for room in room_repository.get_all(include_red) if room.access_level != "red" or include_red
        ]
        return await self._fetch_bookings_all(room_ids, from_dt, to_dt)

    async def get_booking_for_room(
        self, room_id: str, from_dt: datetime.datetime, to_dt: datetime.datetime
    ) -> list[Booking]:
        return await self._fetch_bookings_all([room_id], from_dt, to_dt)

    async def get_bookings_for_certain_rooms(
        self, room_ids: list[str], from_dt: datetime.datetime, to_dt: datetime.datetime
    ) -> list[Booking]:
        return await self._fetch_bookings_all(room_ids, from_dt, to_dt)

    async def create_booking(
        self,
        room: Room,
        start: datetime.datetime,
        end: datetime.datetime,
        title: str,
        organizer_email: str,
        participant_emails: list[str],
    ) -> str:
        start = to_msk(start)
        end = to_msk(end)
        item = exchangelib.CalendarItem(
            account=self.account,
            folder=self.account.calendar,
            start=exchangelib.EWSDateTime.from_datetime(start),
            end=exchangelib.EWSDateTime.from_datetime(end),
            subject=title,
            body=f"Booking on request from {organizer_email}\nProvider: https://innohassle.ru/room-booking",
            location=f"{room.title}",
            resources=[
                room.resource_email,
            ],
            required_attendees=[
                room.resource_email,
                organizer_email,
                *participant_emails,
            ],
        )
        await asyncio.to_thread(item.save, send_meeting_invitations=exchangelib.items.SEND_TO_ALL_AND_SAVE_COPY)
        return item.id

    async def get_booking(self, item_id: str) -> exchangelib.CalendarItem | None:
        try:
            item = await asyncio.to_thread(self.account.calendar.get, id=item_id)
            return item
        except exchangelib.errors.ErrorItemNotFound:
            return None

    async def update_booking(
        self,
        item_id: str,
        new_start: datetime.datetime | None = None,
        new_end: datetime.datetime | None = None,
        new_title: str | None = None,
    ) -> Booking | None:
        item = await self.get_booking(item_id)
        if item is None:
            return None

        email_index = get_emails_to_attendees_index(item)
        old_room_attendee = get_first_room_attendee_from_emails(email_index)

        update_fields = []
        if new_start is not None:
            new_start = to_msk(new_start)
            item.start = new_start
            update_fields.append("start")
        if new_end is not None:
            new_end = to_msk(new_end)
            item.end = new_end
            update_fields.append("end")
        if new_title is not None:
            item.subject = new_title
            update_fields.append("subject")

        if update_fields:
            await asyncio.to_thread(
                item.save,
                update_fields=update_fields,
                send_meeting_invitations=exchangelib.items.SEND_TO_ALL_AND_SAVE_COPY,
            )

        await asyncio.sleep(3)

        tries = 10
        for _ in range(tries):
            await asyncio.sleep(1)

            new_item = await self.get_booking(item_id)
            if new_item is None:
                continue
            new_email_index = get_emails_to_attendees_index(new_item)
            new_room_attendee = get_first_room_attendee_from_emails(new_email_index)

            if new_room_attendee.last_response_time != old_room_attendee.last_response_time:
                return calendar_item_to_booking(new_item)
        return None

    async def delete_booking(self, item_id: str, email: str | None) -> bool:
        item = await self.get_booking(item_id)
        if item is None:
            return True
        await asyncio.to_thread(
            item.cancel, new_body=f"Canceled by {email}\nProvider: https://innohassle.ru/room-booking"
        )
        return True


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
)
