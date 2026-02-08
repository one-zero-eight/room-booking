import datetime

import exchangelib
import pytz
from exchangelib.errors import ErrorMailRecipientNotFound
from pydantic import BaseModel

import src.modules.bookings.patch_exchangelib  # noqa
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

    def fetch_bookings(self, room_ids: list[str], start: datetime.datetime, end: datetime.datetime) -> list[Booking]:
        rooms = room_repository.get_by_ids(room_ids)
        rooms = list(filter(None, rooms))
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
            if isinstance(busy_info, ErrorMailRecipientNotFound) or busy_info.calendar_events is None:
                continue
            for calendar_event in busy_info.calendar_events:
                bookings.append(
                    Booking(
                        room_id=rooms[i].id,
                        title=(calendar_event.details.subject or "Busy") if calendar_event.details else "Busy",
                        start=calendar_event.start,
                        end=calendar_event.end,
                    )
                )
        return bookings

    def get_bookings_for_all_rooms(
        self, from_dt: datetime.datetime, to_dt: datetime.datetime, include_red: bool = False
    ) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        room_ids = [
            room.id for room in room_repository.get_all(include_red) if room.access_level != "red" or include_red
        ]
        return self.fetch_bookings(room_ids, from_dt, to_dt)

    def get_booking_for_room(self, room_id: str, from_dt: datetime.datetime, to_dt: datetime.datetime) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        return self.fetch_bookings([room_id], from_dt, to_dt)

    def get_bookings_for_certain_rooms(
        self, room_ids: list[str], from_dt: datetime.datetime, to_dt: datetime.datetime
    ) -> list[Booking]:
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        return self.fetch_bookings(room_ids, from_dt, to_dt)

    def create_booking(
        self,
        room_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
        title: str,
        organizer_email: str,
        participant_emails: list[str],
    ):
        room = room_repository.get_by_id(room_id)
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
        print(item)
        return item.id

    def get_booking(self, item_id: str):
        item: exchangelib.CalendarItem = self.account.calendar.get(
            id=item_id
        )  # may raise exchangelib.errors.ErrorItemNotFound
        print(item)
        return item

    def update_booking(
        self,
        item_id: str,
        new_start: datetime.datetime | None = None,
        new_end: datetime.datetime | None = None,
        new_title: str | None = None,
    ):
        item: exchangelib.CalendarItem = self.account.calendar.get(
            id=item_id
        )  # may raise exchangelib.errors.ErrorItemNotFound
        print(item)

        update_fields = []
        if new_start is not None:
            item.start = new_start
            update_fields.append("start")
        if new_end is not None:
            item.end = new_end
            update_fields.append("end")
        if new_title is not None:
            item.subject = new_title
            update_fields.append("subject")

        if update_fields:
            item.save(update_fields=update_fields, send_meeting_invitations=exchangelib.items.SEND_TO_ALL_AND_SAVE_COPY)
            print(item)

    def delete_booking(self, item_id: str) -> bool:
        try:
            item: exchangelib.CalendarItem = self.account.calendar.get(id=item_id)
        except exchangelib.errors.ErrorItemNotFound:
            return True
        item.cancel(new_body="Canceled from https://innohassle.ru/room-booking")
        return True


_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(_timezone)


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
)
