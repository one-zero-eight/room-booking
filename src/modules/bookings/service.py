import datetime
from collections.abc import Iterable
from typing import cast, overload

import exchangelib
from exchangelib import CalendarItem

from src.api.logging_ import logger
from src.config_schema import Room
from src.modules.bookings.recurrence import recurrence_to_xml
from src.modules.bookings.schemas import Attendee, Booking, BookingStatus
from src.modules.bookings.tz_utils import to_msk
from src.modules.rooms.repository import room_repository


def get_emails_to_attendees_index(calendar_item: CalendarItem) -> dict[str, exchangelib.Attendee]:
    emails: dict[str, exchangelib.Attendee] = {}

    def _add_attendee(attendee: exchangelib.Attendee) -> None:
        if attendee.mailbox and attendee.mailbox.email_address:
            emails[attendee.mailbox.email_address] = attendee

    if calendar_item.required_attendees:
        for attendee in cast(Iterable[exchangelib.Attendee], calendar_item.required_attendees):
            _add_attendee(attendee)
    if calendar_item.resources:
        for attendee in cast(Iterable[exchangelib.Attendee], calendar_item.resources):
            _add_attendee(attendee)

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
    was_fetched_from_room_calendar: bool = False,
    room_calendar_entry_id: str | None = None,
    recurrence_xml: str | None = None,
) -> Booking | None:
    email_index = get_emails_to_attendees_index(calendar_item=calendar_item)
    if room_id is None:
        room = get_first_room_from_emails(emails=email_index.keys())

        if room is None:
            logger.warning(f"Room not found for email index: {calendar_item}")
            return None

        room_id = room.id
    else:
        room = room_repository.get_by_id(room_id)

        if room is None:
            logger.warning(f"Room not found for room ID: {room_id}")
            return None

        if not was_fetched_from_room_calendar and room.resource_email not in email_index:
            logger.warning(f"Room email not found in email index: {calendar_item}")
            return None

    attendees = [
        Attendee(
            email=email,
            status=cast(BookingStatus | None, attendee.response_type),
            assosiated_room_id=r.id if (r := room_repository.get_by_email(email)) is not None else None,
        )
        for email, attendee in email_index.items()
    ]
    if was_fetched_from_room_calendar and room.resource_email not in email_index:
        # Should add the room as attendee
        attendees.insert(
            0,
            Attendee(
                email=room.resource_email,
                status=cast(BookingStatus | None, calendar_item.my_response_type),
                assosiated_room_id=room.id,
            ),
        )

    categories = list(calendar_item.categories) if calendar_item.categories else None
    if recurrence_xml is None:
        recurrence_xml = recurrence_to_xml(calendar_item.recurrence, version=calendar_item.account.version)

    return Booking(
        room_id=room.id,
        title=cast(str, calendar_item.subject) or "Busy",
        start=to_msk(cast(datetime.datetime, calendar_item.start)),
        end=to_msk(cast(datetime.datetime, calendar_item.end)),
        outlook_booking_id=str(calendar_item.id) if not was_fetched_from_room_calendar else None,
        outlook_entry_id=room_calendar_entry_id if was_fetched_from_room_calendar else None,
        attendees=attendees,
        categories=categories,
        recurrence=recurrence_xml,
    )


@overload
def set_related_to_me(bookings: list[Booking], user_email: str) -> list[Booking]: ...


@overload
def set_related_to_me(bookings: Booking, user_email: str) -> Booking: ...


def set_related_to_me(bookings: list[Booking] | Booking, user_email: str) -> list[Booking] | Booking:
    """
    Set related_to_me field for bookings to True if the booking is related to the user, otherwise set it to False.
    If the booking is not related to the user, set it to None. Note that original bookings are modified in place.
    """

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

    return bookings
