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
from fastapi import HTTPException

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
    set_related_to_me_for_bookings,
    to_msk,
)
from src.modules.inh_accounts_sdk import UserSchema
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
    subscription_id: str | None
    watermark: str | None
    last_callback_time: float | None
    pending_bookings_to_cancel: set[str] = set()

    def __init__(self, ews_endpoint: str, account_email: str):
        self.ews_endpoint = ews_endpoint
        self.account_email = account_email

        config = exchangelib.Configuration(
            auth_type=exchangelib.transport.NOAUTH,
            service_endpoint=self.ews_endpoint,
            version=exchangelib.Version(exchangelib.version.EXCHANGE_2016),
            max_connections=5,
        )
        self.account = exchangelib.Account(
            self.account_email,
            access_type=exchangelib.DELEGATE,
            config=config,
            autodiscover=False,
        )

        self.subscription_id = None
        self.watermark = None
        self.last_callback_time = None

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

    async def push_subscription(self, callback_url: str) -> tuple[str, str]:
        with self.account.calendar.push_subscription(
            callback_url=callback_url,
            event_types=("ModifiedEvent",),
        ) as (
            subscription_id,
            watermark,
        ):
            self.subscription_id = subscription_id
            self.watermark = watermark
            self.last_callback_time = time.monotonic()

            return (subscription_id, watermark)

    _cache_bookings_from_busy_info: dict[
        str,
        tuple[list[Booking], datetime.datetime],
    ] = {}

    def _get_cached_bookings_from_busy_info(self, room_id: str, use_ttl: bool = True) -> list[Booking] | None:
        cached_bookings, cached_dt = self._cache_bookings_from_busy_info.get(room_id, (None, None))
        if cached_bookings is not None and cached_dt is not None:
            if not use_ttl:
                return [c.model_copy() for c in cached_bookings]
            if datetime.datetime.now() - cached_dt < datetime.timedelta(seconds=settings.ttl_bookings_from_busy_info):
                return [c.model_copy() for c in cached_bookings]

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
        t_start = time.monotonic()
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
                    requested_view="Detailed",
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
                title = "Busy"
                if calendar_event.details is not None and calendar_event.details.subject is not None:
                    title = calendar_event.details.subject
                room_id_x_bookings[room_id].append(
                    Booking(
                        room_id=room_id,
                        title=title,
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

        logger.info(f"_fetch_bookings_from_busy_info took {time.monotonic() - t_start:.2f}s")
        return room_id_x_bookings

    _cache_bookings_from_account_calendar: dict[
        str,
        tuple[list[Booking], datetime.datetime],
    ] = {}

    def _get_cached_bookings_from_account_calendar(self, room_id: str, use_ttl: bool = True) -> list[Booking] | None:
        cached_bookings, cached_dt = self._cache_bookings_from_account_calendar.get(room_id, (None, None))
        if cached_bookings is not None and cached_dt is not None:
            if not use_ttl:
                return [c.model_copy() for c in cached_bookings]
            if datetime.datetime.now() - cached_dt < datetime.timedelta(
                seconds=settings.ttl_bookings_from_account_calendar
            ):
                return [c.model_copy() for c in cached_bookings]

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
        t_start = time.monotonic()
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
                ).only("required_attendees", "subject", "start", "end", "id")
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

        logger.info(f"_fetch_bookings_from_account_calendar took {time.monotonic() - t_start:.2f}s")
        return room_x_bookings

    async def fetch_user_bookings(
        self, attendee_email: str, start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        """
        Fetch bookings from account calendar for all rooms for the given time range and filter out bookings that are not related to the user. Sets .related_to_me=True attribute for each booking.
        """
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

                booking.related_to_me = True
                user_bookings.append(booking)

        return user_bookings

    async def _fetch_bookings_both_from_account_calendar_and_busy_info(
        self, room_ids: list[str], start: datetime.datetime, end: datetime.datetime, user_email: str | None
    ) -> list[Booking]:
        rooms = room_repository.get_by_ids(room_ids)
        rooms = list(filter(None, rooms))
        if not rooms:
            return []

        bookings_from_account_calendar, bookings_from_busy_info = await asyncio.gather(
            self._fetch_bookings_from_account_calendar(
                rooms=rooms,
                start=start,
                end=end,
                use_cache=True,
                use_dedup=True,
            ),
            self._fetch_bookings_from_busy_info(
                rooms=rooms,
                start=start,
                end=end,
                use_cache=True,
                use_dedup=True,
            ),
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

        if user_email is not None:
            set_related_to_me_for_bookings(bookings, user_email)

        return bookings

    async def get_booking_for_room(
        self,
        room_id: str,
        from_dt: datetime.datetime,
        to_dt: datetime.datetime,
        user_email: str | None = None,
    ) -> list[Booking]:
        """
        Get bookings for a specific room for the given time range. Automatically sets .related_to_me=True for bookings that contain the user's email in the attendees list.
        """
        return await self._fetch_bookings_both_from_account_calendar_and_busy_info(
            [room_id], from_dt, to_dt, user_email
        )

    async def get_bookings_for_certain_rooms(
        self,
        room_ids: list[str],
        from_dt: datetime.datetime,
        to_dt: datetime.datetime,
        user_email: str | None = None,
    ) -> list[Booking]:
        """
        Get bookings for certain rooms for the given time range. Automatically sets .related_to_me=True for bookings that contain the user's email in the attendees list.
        """
        return await self._fetch_bookings_both_from_account_calendar_and_busy_info(room_ids, from_dt, to_dt, user_email)

    async def create_booking(
        self,
        room: Room,
        start: datetime.datetime,
        end: datetime.datetime,
        title: str,
        organizer: UserSchema,
        participant_emails: list[str],
        user_email: str,
    ) -> Booking:
        """
        Create a booking for a specific room. Automatically sets .related_to_me=True for the booking.
        May raise HTTPExceptions.
        """
        start = to_msk(start)
        end = to_msk(end)

        organizer_roles = []
        if organizer.innopolis_info.is_staff:
            organizer_roles.append("staff")
        if organizer.innopolis_info.is_student:
            organizer_roles.append("student")
        if organizer.innopolis_info.is_college:
            organizer_roles.append("college")

        mail_body = (
            f"Booking on request from {organizer.innopolis_info.email} ({', '.join(organizer_roles) or 'no roles'})\n"
            f"Provider: https://innohassle.ru/room-booking\n"
            f"\n"
            f"View full room schedule at https://innohassle.ru/room-booking/rooms/{room.id}"
        )

        item = exchangelib.CalendarItem(
            account=self.account,
            folder=self.account.calendar,
            start=exchangelib.EWSDateTime.from_datetime(start),
            end=exchangelib.EWSDateTime.from_datetime(end),
            subject=title,
            body=mail_body,
            location=f"{room.title} ({organizer.innopolis_info.email})",
            resources=[
                room.resource_email,
            ],
            required_attendees=[
                room.resource_email,
                organizer.innopolis_info.email,
                *participant_emails,
            ],
        )
        await asyncio.to_thread(item.save, send_meeting_invitations=exchangelib.items.SEND_TO_ALL_AND_SAVE_COPY)
        item_id = str(item.id)

        await asyncio.sleep(2)

        tries = 10
        booking = None
        for _ in range(tries):  # TODO: Rooms, that don't answer automatically, should be handled individually
            fetched = await self.get_booking(item_id=item_id)

            if fetched is None:
                raise HTTPException(404, "Booking was removed during booking")

            booking = calendar_item_to_booking(fetched, room_id=room.id, user_email=user_email)
            email_index = get_emails_to_attendees_index(fetched)
            room_attendee = email_index.get(room.resource_email)

            if room_attendee is None or room_attendee.response_type == "Decline":
                raise HTTPException(403, "Booking was declined by the room")

            if room_attendee.last_response_time is not None:
                if booking is None:
                    raise HTTPException(404, "Room attendee not found in booking attendees")
                return booking

            await asyncio.sleep(1)

        if booking is None:
            raise HTTPException(404, "Room attendee not found in booking attendees")
        return booking

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
        user_email: str | None = None,
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
                return calendar_item_to_booking(new_item, user_email=user_email)
        return None

    async def cancel_booking(self, item: exchangelib.CalendarItem, email: str | None) -> bool:
        await asyncio.to_thread(
            item.cancel, new_body=f"Canceled by {email}\nProvider: https://innohassle.ru/room-booking"
        )
        return True


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
)
