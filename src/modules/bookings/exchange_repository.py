import asyncio
import datetime
from collections import defaultdict

import exchangelib
import exchangelib.errors
import pytz

import src.modules.bookings.patch_exchangelib  # noqa
from src.config import settings
from src.config_schema import Room
from src.modules.bookings.schemas import Booking
from src.modules.bookings.service import (
    calendar_item_to_booking,
    get_emails_to_attendees_index,
    get_first_room_attendee_from_emails,
    get_fisrt_room_from_emails,
)
from src.modules.rooms.repository import room_repository


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
        )
        self.account = exchangelib.Account(
            self.account_email,
            access_type=exchangelib.DELEGATE,
            config=config,
            autodiscover=False,
        )

    def fetch_bookings_from_busy_info(
        self, rooms: list[Room], start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        room_emails = [room.resource_email for room in rooms]

        accounts = [(email, "Resource", False) for email in room_emails]
        bookings: list[Booking] = []
        for i, busy_info in enumerate(
            self.account.protocol.get_free_busy_info(
                accounts=accounts,
                start=exchangelib.EWSDateTime.from_datetime(start),
                end=exchangelib.EWSDateTime.from_datetime(end),
                merged_free_busy_interval=5,
            )
        ):
            if busy_info is None or busy_info.calendar_items is None:
                continue

            for calendar_event in busy_info.calendar_events:
                bookings.append(
                    Booking(
                        room_id=rooms[i].id,
                        title=calendar_event.details.subject if calendar_event.details else "Busy",
                        start=calendar_event.start,
                        end=calendar_event.end,
                        outlook_booking_id=None,
                        emails=[],  # if you change it, ensure emails properly merged in fetch_bookings_all()
                    )
                )
        return bookings

    def fetch_user_bookings(
        self, attendee_email: str, start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        calendar_items = self.account.calendar.view(start=to_msk(start), end=to_msk(end))

        bookings: list[Booking] = []

        for item in calendar_items:
            email_index = get_emails_to_attendees_index(item)
            room = get_fisrt_room_from_emails(email_index)

            if room is None:
                continue

            room_attendee = email_index.get(room.resource_email)

            if room_attendee is None or room_attendee.response_type == "Decline":
                continue

            user_attendee = email_index.get(attendee_email)

            if user_attendee is None:
                continue

            booking = calendar_item_to_booking(item, room.id)
            if booking is not None:
                bookings.append(booking)

        return bookings

    def fetch_bookings_from_account_calendar(
        self, rooms: list[Room], start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        calendar_items = self.account.calendar.view(
            start=exchangelib.EWSDateTime.from_datetime(start), end=exchangelib.EWSDateTime.from_datetime(end)
        )

        bookings: list[Booking] = []
        for item in calendar_items:
            email_index = get_emails_to_attendees_index(item)
            room = get_fisrt_room_from_emails(email_index)

            if room is None:
                continue

            if room.id not in rooms:
                continue

            room_attendee = email_index.get(room.resource_email)
            if room_attendee is None or room_attendee.response_type == "Decline":
                continue

            booking = calendar_item_to_booking(item, room_id=room.id)
            if booking is not None:
                bookings.append(booking)

        return bookings

    def fetch_bookings_all(
        self, room_ids: list[str], start: datetime.datetime, end: datetime.datetime
    ) -> list[Booking]:
        rooms = room_repository.get_by_ids(room_ids)
        rooms = list(filter(None, rooms))
        if not rooms:
            return []

        bookings_from_account_calendar = self.fetch_bookings_from_account_calendar(rooms=rooms, start=start, end=end)
        bookings_from_busy_info = self.fetch_bookings_from_busy_info(rooms=rooms, start=start, end=end)

        account_calendar_registry = defaultdict(list)
        busy_info_registry = defaultdict(list)

        def key(booking: Booking) -> tuple[str, datetime.datetime, datetime.datetime]:
            return booking.room_id, booking.start, booking.end

        for booking in bookings_from_account_calendar:
            account_calendar_registry[key(booking)].append(booking)
        for booking in bookings_from_busy_info:
            busy_info_registry[key(booking)].append(booking)

        bookings = []

        for key in set(account_calendar_registry.keys()) | set(busy_info_registry.keys()):
            ac_bookings = account_calendar_registry[key]
            bi_bookings = busy_info_registry[key]
            conflicting_bookings_len = len(ac_bookings) + len(bi_bookings)

            # TODO: properly handle two sources of truth
            # We can return busy_info bookings and account bookings separately
            # with an intention that account booking info would be duplicated in busy_info.
            # busy_info would be used as source of truth for busyness of room
            # account bookings would be used as source of bookings made by servcice account

            if conflicting_bookings_len == 1:
                bookings.extend(ac_bookings + bi_bookings)
            elif ac_bookings:
                bookings.append(ac_bookings[0])
            else:
                bookings.extend(bi_bookings)

        return bookings

    def get_bookings_for_all_rooms(
        self, from_dt: datetime.datetime, to_dt: datetime.datetime, include_red: bool = False
    ) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        room_ids = [
            room.id for room in room_repository.get_all(include_red) if room.access_level != "red" or include_red
        ]
        return self.fetch_bookings_all(room_ids, from_dt, to_dt)

    def get_booking_for_room(self, room_id: str, from_dt: datetime.datetime, to_dt: datetime.datetime) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        return self.fetch_bookings_all([room_id], from_dt, to_dt)

    def get_bookings_for_certain_rooms(
        self, room_ids: list[str], from_dt: datetime.datetime, to_dt: datetime.datetime
    ) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        return self.fetch_bookings_all(room_ids, from_dt, to_dt)

    def create_booking(
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
            start=start,
            end=end,
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
        item.save(send_meeting_invitations=exchangelib.items.SEND_TO_ALL_AND_SAVE_COPY)
        return item.id

    def get_booking(self, item_id: str) -> exchangelib.CalendarItem | None:
        try:
            item: exchangelib.CalendarItem = self.account.calendar.get(id=item_id)
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
        item = await asyncio.to_thread(self.get_booking, item_id)
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

            new_item = await asyncio.to_thread(self.get_booking, item_id)
            if new_item is None:
                continue
            new_email_index = get_emails_to_attendees_index(new_item)
            new_room_attendee = get_first_room_attendee_from_emails(new_email_index)

            if new_room_attendee.last_response_time != old_room_attendee.last_response_time:
                return calendar_item_to_booking(new_item)
        return None

    def delete_booking(self, item_id: str, email: str | None) -> bool:
        try:
            item: exchangelib.CalendarItem = self.account.calendar.get(id=item_id)
        except exchangelib.errors.ErrorItemNotFound:
            return True
        item.cancel(new_body=f"Canceled by {email}\nProvider: https://innohassle.ru/room-booking")
        return True

    def get_conversation_history(self, conversation_id) -> list[str]:
        conversation = [
            item.text_body
            for item in self.account.inbox.filter(conversation_id=conversation_id)
            if hasattr(item, "text_body")
        ]

        return conversation


_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(_timezone)


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
)
