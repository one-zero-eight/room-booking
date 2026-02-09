import datetime
from collections.abc import Iterable
from typing import cast

import exchangelib
from exchangelib import CalendarItem

from src.config_schema import Room
from src.modules.bookings.exchange_repository import Booking
from src.modules.rooms.repository import room_repository


def get_emails_to_attendees_index(calendar_item: CalendarItem) -> dict[str, exchangelib.Attendee]:
    if not calendar_item.required_attendees:
        return {}

    emails = {}
    for attendee in cast(Iterable[exchangelib.Attendee], calendar_item.required_attendees):
        if attendee.mailbox and cast(exchangelib.Mailbox, attendee.mailbox).email_address:
            emails[cast(str, cast(exchangelib.Mailbox, attendee.mailbox).email_address)] = attendee

    return emails


def get_fisrt_room_from_emails(emails: Iterable[str]) -> Room | None:
    for email in emails:
        if room := room_repository.get_by_email(email):
            return room
    return None


def get_first_room_attendee_from_emails(email_index: dict[str, exchangelib.Attendee]) -> exchangelib.Attendee | None:
    for email, attendee in email_index.items():
        if room_repository.get_by_email(email):
            return attendee
    return None


def calendar_item_to_booking(calendar_item: CalendarItem, room_id: str | None = None) -> Booking | None:
    email_index = get_emails_to_attendees_index(calendar_item=calendar_item)
    if room_id is None:
        room = get_fisrt_room_from_emails(emails=email_index.keys())

        if room is None:
            return None

        room_id = room.id
    else:
        room = room_repository.get_by_id(room_id)

        if room is None:
            return None

        if room.resource_email not in email_index:
            return None

    return Booking(
        room_id=room.id,
        title=cast(str, calendar_item.subject) or "Busy",
        start=cast(datetime.datetime, calendar_item.start),
        end=cast(datetime.datetime, calendar_item.end),
        outlook_booking_id=str(calendar_item.id),
        emails=[email for email in email_index.keys() if not room_repository.get_by_email(email)],
    )
