import datetime
from collections.abc import Iterable
from typing import cast

import exchangelib
import pytz
from exchangelib import CalendarItem

from src.config_schema import Room
from src.modules.bookings.schemas import Attendee, Booking, BookingStatus
from src.modules.rooms.repository import room_repository

_msk_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime) -> datetime.datetime:
    if isinstance(dt, exchangelib.EWSDateTime):
        return dt.astimezone(exchangelib.EWSTimeZone.from_pytz(_msk_timezone))
    return dt.astimezone(_msk_timezone)


def get_emails_to_attendees_index(calendar_item: CalendarItem) -> dict[str, exchangelib.Attendee]:
    if not calendar_item.required_attendees:
        return {}

    emails = {}
    for attendee in cast(Iterable[exchangelib.Attendee], calendar_item.required_attendees):
        if attendee.mailbox and cast(exchangelib.Mailbox, attendee.mailbox).email_address:
            emails[cast(str, cast(exchangelib.Mailbox, attendee.mailbox).email_address)] = attendee

    return emails


def get_first_room_from_emails(emails: Iterable[str]) -> Room | None:
    for email in emails:
        if room := room_repository.get_by_email(email):
            return room
    return None


def get_first_room_attendee_from_emails(email_index: dict[str, exchangelib.Attendee]) -> exchangelib.Attendee | None:
    for email, attendee in email_index.items():
        if room_repository.get_by_email(email):
            return attendee
    return None


def calendar_item_to_booking(
    calendar_item: CalendarItem,
    room_id: str | None = None,
    user_email: str | None = None,
) -> Booking | None:
    email_index = get_emails_to_attendees_index(calendar_item=calendar_item)
    if room_id is None:
        room = get_first_room_from_emails(emails=email_index.keys())

        if room is None:
            return None

        room_id = room.id
    else:
        room = room_repository.get_by_id(room_id)

        if room is None:
            return None

        if room.resource_email not in email_index:
            return None

    related_to_me = None
    if user_email is not None:
        for email, _attendee in email_index.items():
            if email == user_email:
                related_to_me = True
                break
        else:
            related_to_me = False

    return Booking(
        room_id=room.id,
        title=cast(str, calendar_item.subject) or "Busy",
        start=to_msk(cast(datetime.datetime, calendar_item.start)),
        end=to_msk(cast(datetime.datetime, calendar_item.end)),
        outlook_booking_id=str(calendar_item.id),
        attendees=[
            Attendee(
                email=email,
                status=cast(BookingStatus | None, attendee.response_type),
                assosiated_room_id=room.id if (room := room_repository.get_by_email(email)) is not None else None,
            )
            for email, attendee in email_index.items()
        ],
        related_to_me=related_to_me,
    )


def set_related_to_me_for_bookings(bookings: list[Booking] | Booking, user_email: str) -> None:
    if isinstance(bookings, Booking):
        bookings_to_set = [bookings]
    else:
        bookings_to_set = bookings

    for booking in bookings_to_set:
        if booking.attendees is None:  # We don't know whether the booking is related to the user, so we set it to None
            booking.related_to_me = None
        else:
            for attendee in booking.attendees:
                if attendee.email == user_email:
                    booking.related_to_me = True
                    break
            else:
                booking.related_to_me = False
