import asyncio
import datetime
import re
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
from src.modules.bookings.caching import CacheForBookings
from src.modules.bookings.recently import RecentBookings
from src.modules.bookings.schemas import Attendee, Booking
from src.modules.bookings.service import (
    calendar_item_to_booking,
    get_emails_to_attendees_index,
    get_first_room_attendee_from_emails,
    get_first_room_from_emails,
)
from src.modules.bookings.single_flight import SingleFlight
from src.modules.bookings.tz_utils import to_msk
from src.modules.inh_accounts_sdk import UserSchema
from src.modules.rooms.repository import room_repository

# Location format: "Room title (user@innopolis.university)" or "... (user@innopolis.ru)"
EMAIL_IN_LOCATION_RE = re.compile(r"\(([^)]+@(?:innopolis\.university|innopolis\.ru))\)")


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
    _recently: RecentBookings

    def __init__(self, ews_endpoint: str, account_email: str, password: str):
        self.ews_endpoint = ews_endpoint
        self.account_email = account_email

        config = exchangelib.Configuration(
            auth_type=exchangelib.transport.BASIC,
            service_endpoint=self.ews_endpoint,
            version=exchangelib.Version(exchangelib.version.EXCHANGE_2016),
            max_connections=5,
            credentials=exchangelib.Credentials(
                username=account_email,
                password=password,
            ),
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
        self._recently = RecentBookings(settings.recently_canceled_booking_ttl_sec)

        self._cache_from_busy_info = CacheForBookings(settings.ttl_bookings_from_busy_info)
        self._cache_from_account_calendar = CacheForBookings(settings.ttl_bookings_from_account_calendar)
        self._free_busy_single_flight = SingleFlight[dict[str, list[CalendarEvent]], AccountGetFreeBusyInfoArgs]()
        self._calendar_view_single_flight = SingleFlight[list[exchangelib.CalendarItem], AccountCalendarViewArgs]()
        self._cancel_single_flight = SingleFlight[bool, str]()

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

    async def _fetch_bookings_from_busy_info(
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
            room_x_cached_bookings, cache_misses = await self._cache_from_busy_info.get_cached_bookings(
                rooms_ids, start, end
            )

            if not cache_misses:  # full cache hit
                logger.info(f"Cache hit for bookings from busy info for rooms {rooms_ids}")
                return room_x_cached_bookings
            else:  # TODO: request only subset of rooms that are not in cache
                logger.info(f"Cache miss for bookings from busy info for rooms: {cache_misses}")
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

        async def free_busy_task(args: AccountGetFreeBusyInfoArgs) -> dict[str, list[CalendarEvent]]:
            account_free_busy_info = await asyncio.to_thread(
                lambda: list(
                    self.account.protocol.get_free_busy_info(
                        accounts=args["accounts"],
                        start=exchangelib.EWSDateTime.from_datetime(args["start"]),
                        end=exchangelib.EWSDateTime.from_datetime(args["end"]),
                        requested_view="Detailed",
                    )
                ),
            )
            account_free_busy_info = cast(Iterable[FreeBusyView], account_free_busy_info)

            room_id_x_calendar_events: dict[str, list[CalendarEvent]] = {}

            for i, busy_info in enumerate(account_free_busy_info):
                room_id = args["rooms_ids"][i]
                if busy_info is not None and busy_info.calendar_events is not None:
                    room_id_x_calendar_events[room_id] = list(cast(Iterable[CalendarEvent], busy_info.calendar_events))
                else:
                    room_id_x_calendar_events[room_id] = []

            return room_id_x_calendar_events

        room_id_x_calendar_events = await self._free_busy_single_flight.run(
            args, lambda: asyncio.create_task(free_busy_task(args)), use_dedup=use_dedup
        )
        # ^^^^^

        # ---- Convert EWS CalendarEvents to ours Bookings ----
        room_id_x_bookings: dict[str, list[Booking]] = {k: [] for k in rooms_ids}
        for room_id, room_calendar_events in room_id_x_calendar_events.items():
            room = room_repository.get_by_id(room_id)

            for calendar_event in room_calendar_events:
                title = "Busy"
                email_in_location = None
                attendee = []

                if room is not None:
                    attendee.append(Attendee(email=room.resource_email, status=None, assosiated_room_id=room.id))

                if calendar_event.details is not None:
                    if calendar_event.details.subject is not None:
                        title = calendar_event.details.subject
                    if calendar_event.details.location is not None:
                        location = calendar_event.details.location
                        match = EMAIL_IN_LOCATION_RE.search(location)
                        if match is not None:
                            email_in_location = match.group(1)
                            attendee.append(Attendee(email=email_in_location, status=None, assosiated_room_id=None))

                room_id_x_bookings[room_id].append(
                    Booking(
                        room_id=room_id,
                        title=title,
                        start=to_msk(cast(datetime.datetime, calendar_event.start)),
                        end=to_msk(cast(datetime.datetime, calendar_event.end)),
                        outlook_booking_id=None,
                        attendees=attendee or None,
                        # busy info doesn't contain attendees info, we can fetch it from account calendar if needed. Although, we know that room is always in the attendees list, and we can parse organizer email from location.
                    )
                )
        # ^^^^^

        # ---- Cache bookings ----
        await self._cache_from_busy_info.update_cache_from_mapping(
            room_id_x_bookings=room_id_x_bookings, start=start, end=end
        )
        # ^^^^^

        logger.info(f"_fetch_bookings_from_busy_info took {time.monotonic() - t_start:.2f}s")
        return room_id_x_bookings

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
            room_x_cached_bookings, cache_misses = await self._cache_from_account_calendar.get_cached_bookings(
                rooms_ids, start, end
            )

            if not cache_misses:  # full cache hit
                logger.info(f"Cache hit for bookings from account calendar for rooms: {rooms_ids}")
                return room_x_cached_bookings
            else:  # TODO: request only subset of rooms that are not in cache
                logger.info(f"Cache miss for bookings from account calendar for rooms: {cache_misses}")
        # ^^^^^

        # ---- Fetch account calendar view with only one request at a time ----
        args = AccountCalendarViewArgs(start=to_msk(start), end=to_msk(end))

        async def calendar_view_task(args: AccountCalendarViewArgs) -> list[exchangelib.CalendarItem]:
            return await asyncio.to_thread(
                lambda: list(
                    self.account.calendar.view(
                        exchangelib.EWSDateTime.from_datetime(args["start"]),
                        exchangelib.EWSDateTime.from_datetime(args["end"]),
                    ).only("required_attendees", "subject", "start", "end", "id")
                )
            )

        calendar_items = await self._calendar_view_single_flight.run(
            args,
            lambda: asyncio.create_task(calendar_view_task(args)),
            use_dedup=use_dedup,
        )
        # ^^^^^

        # ---- Convert EWS CalendarItems to ours Bookings ----
        room_x_bookings: dict[str, list[Booking]] = {k: [] for k in rooms_ids}
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
        await self._cache_from_account_calendar.update_cache_from_mapping(
            room_id_x_bookings=room_x_bookings, start=start, end=end
        )
        # ^^^^^

        logger.info(f"_fetch_bookings_from_account_calendar took {time.monotonic() - t_start:.2f}s")
        return room_x_bookings

    async def fetch_user_bookings(
        self, attendee_email: str, start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        """
        Fetch bookings from account calendar for all rooms for the given time range and filter out bookings that are not related to the user.
        """
        bookings_from_account_calendar = await self._fetch_bookings_from_account_calendar(
            rooms=room_repository.get_all(include_red=True),
            start=start,
            end=end,
            use_cache=False,  # we dont use cache, so there is no need to check recently created and updated bookings
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
        user_bookings.sort(key=lambda x: x.start, reverse=True)
        return user_bookings

    async def _fetch_bookings_both_from_account_calendar_and_busy_info(
        self, room_ids: list[str], start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        rooms = room_repository.get_by_ids(room_ids)
        rooms = list(filter(None, rooms))
        if not rooms:
            return []

        t_start = time.monotonic()
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
            for updated in bookings:
                account_calendar_registry[key(updated)].append(updated)
        for _room_id, bookings in bookings_from_busy_info.items():
            for updated in bookings:
                busy_info_registry[key(updated)].append(updated)

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

        # ---- Use cache for recently created, updated and canceled bookings ----
        recently_created_bookings = await self._recently.get_created()
        recently_canceled_bookings = await self._recently.get_canceled()
        recently_updated_bookings = await self._recently.get_updated_with_ts()

        for canceled_booking_id in recently_canceled_bookings:
            recently_created_bookings.pop(canceled_booking_id, None)
            recently_updated_bookings.pop(canceled_booking_id, None)

        for recently_updated_booking_id in recently_updated_bookings:
            # we will prioritize recently updated bookings over recently created bookings
            recently_created_bookings.pop(recently_updated_booking_id, None)

        bookings_with_recently = []
        for b in bookings:
            oid = b.outlook_booking_id
            if oid is not None:
                if oid in recently_canceled_bookings:
                    # we will not add recently canceled bookings to the list
                    logger.info("Booking %s skipped: recently canceled", oid)

                elif oid in recently_updated_bookings:
                    # add updated booking to the list and remove it from the recently updated bookings, prioritizing updated bookings
                    updated_ts, updated = recently_updated_bookings.pop(oid)
                    if (t_start - settings.ttl_bookings_from_account_calendar) < updated_ts:
                        bookings_with_recently.append(updated)
                        logger.info("Booking %s: using recently updated version", oid)
                    else:
                        bookings_with_recently.append(b)
                        logger.info("Booking %s: using fetched version", oid)

                elif oid in recently_created_bookings:
                    # add booking to the list and remove it from the recently created bookings, prioritizing fetched bookings
                    bookings_with_recently.append(b)
                    recently_created_bookings.pop(oid)
                    logger.info("Booking %s: in fetch and recently created, using fetched", oid)

                else:
                    # just booking from somewhere else, add the booking to the list
                    bookings_with_recently.append(b)
            else:
                bookings_with_recently.append(b)

        for created_booking in recently_created_bookings.values():
            # add remaining recently created bookings to the list, that were not updated or canceled
            if created_booking.room_id in room_ids:
                bookings_with_recently.append(created_booking)
                logger.info("Booking %s: remaining recently created (not in fetch), appended", created_booking)

        # ^^^^^

        bookings_with_recently.sort(key=lambda x: x.start, reverse=True)

        return bookings_with_recently

    async def get_bookings_for_room(
        self,
        room_id: str,
        from_dt: datetime.datetime,
        to_dt: datetime.datetime,
    ) -> list[Booking]:
        """
        Get bookings for a specific room for the given time range.
        """
        return await self._fetch_bookings_both_from_account_calendar_and_busy_info([room_id], from_dt, to_dt)

    async def get_bookings_for_certain_rooms(
        self,
        room_ids: list[str],
        from_dt: datetime.datetime,
        to_dt: datetime.datetime,
    ) -> list[Booking]:
        """
        Get bookings for certain rooms for the given time range.
        """
        return await self._fetch_bookings_both_from_account_calendar_and_busy_info(room_ids, from_dt, to_dt)

    async def create_booking(
        self,
        room: Room,
        start: datetime.datetime,
        end: datetime.datetime,
        title: str,
        organizer: UserSchema,
        participant_emails: list[str],
    ) -> Booking:
        """
        Create a booking for a specific room.
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
                if await self.is_recently_canceled(item_id):
                    raise HTTPException(403, "Booking was declined by the room")
                raise HTTPException(404, "Booking was removed during booking")

            booking = calendar_item_to_booking(fetched, room_id=room.id)
            email_index = get_emails_to_attendees_index(fetched)
            room_attendee = email_index.get(room.resource_email)

            if room_attendee is None or room_attendee.response_type == "Decline":
                await self.cancel_booking(fetched, email=room.resource_email)
                raise HTTPException(403, "Booking was declined by the room")

            if room_attendee.last_response_time is not None:
                if booking is None:
                    raise HTTPException(404, "Room attendee not found in booking attendees")
                await self._recently.mark_created(item_id, booking)
                return booking

            await asyncio.sleep(1)

        if booking is None:
            raise HTTPException(404, "Room attendee not found in booking attendees")
        await self._recently.mark_created(item_id, booking)
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
    ) -> Booking | None:
        item = await self.get_booking(item_id)
        if item is None:
            return None

        email_index = get_emails_to_attendees_index(item)
        old_room_attendee = get_first_room_attendee_from_emails(email_index)

        update_fields = []
        if new_start is not None:
            new_start = to_msk(new_start)
            item.start = exchangelib.EWSDateTime.from_datetime(new_start)
            update_fields.append("start")
        if new_end is not None:
            new_end = to_msk(new_end)
            item.end = exchangelib.EWSDateTime.from_datetime(new_end)
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
                booking = calendar_item_to_booking(new_item)
                if booking is not None:
                    await self._recently.mark_updated(item_id, booking)
                return booking

        return None

    async def cancel_booking(self, item: exchangelib.CalendarItem, email: str | None) -> bool:
        item_id = str(item.id)

        if await self._recently.is_canceled(item_id):
            return True

        async def cancel_task() -> bool:
            await asyncio.to_thread(
                item.cancel, new_body=f"Canceled by {email}\nProvider: https://innohassle.ru/room-booking"
            )
            await self._recently.mark_canceled(item_id)
            return True

        return await self._cancel_single_flight.run(item_id, lambda: asyncio.create_task(cancel_task()))

    async def is_recently_canceled(self, item_id: str) -> bool:
        return await self._recently.is_canceled(item_id)


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
    password=settings.exchange.password.get_secret_value(),
)
