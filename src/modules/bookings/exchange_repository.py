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
                        title=calendar_event.details.subject if calendar_event.details else "Busy",
                        start=calendar_event.start,
                        end=calendar_event.end,
                    )
                )
        return bookings

    def get_bookings_for_all_rooms(self, from_dt: datetime.datetime, to_dt: datetime.datetime):
        from_dt = to_msk(from_dt)
        to_dt = to_msk(to_dt)
        room_ids = [room.id for room in room_repository.get_all()]
        return self.fetch_bookings(room_ids, from_dt, to_dt)


_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(_timezone)


exchange_booking_repository = ExchangeBookingRepository(
    ews_endpoint=settings.exchange.ews_endpoint,
    account_email=settings.exchange.username,
)
