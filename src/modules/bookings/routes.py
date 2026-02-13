"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import datetime
from typing import cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr

from src.api.dependencies import ApiKeyDep, VerifiedDep
from src.api.logging_ import logger
from src.modules.bookings.exchange_repository import exchange_booking_repository
from src.modules.bookings.schemas import (
    Booking,
    CreateBookingRequest,
    PatchBookingRequest,
)
from src.modules.bookings.service import (
    calendar_item_to_booking,
    get_emails_to_attendees_index,
    get_first_room_from_emails,
)
from src.modules.inh_accounts_sdk import inh_accounts
from src.modules.rooms.repository import room_repository
from src.modules.rules.service import can_book


def _default_date_range(
    start: datetime.datetime | None,
    end: datetime.datetime | None,
) -> tuple[datetime.datetime, datetime.datetime]:
    today = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
    if start is None:
        start = today - datetime.timedelta(days=7)
    if end is None:
        end = today + datetime.timedelta(days=14)
    return start, end


router = APIRouter(
    tags=["Bookings"],
    responses={
        403: {"description": "Unauthorized"},
        429: {"description": "EWS error, probably Outlook is down"},
    },
)


@router.get(
    "/bookings/",
    responses={404: {"description": "Room not found"}},
)
async def bookings(
    user: VerifiedDep,
    room_id: str | None = Query(None, title="ID for getting single room bookings"),
    room_ids: list[str] | None = Query(None, title="IDs for multiple rooms bookings"),
    start: datetime.datetime | None = Query(
        None, description="Start date, if not provided, will be set to 7 days before current date"
    ),
    end: datetime.datetime | None = Query(
        None, description="End date, if not provided, will be set to 14 days after current date"
    ),
    include_red: bool = Query(False, description="Include red-access rooms bookings when getting all"),
) -> list[Booking]:
    """
    Get bookings for all or for specific rooms.

    - If `room_id` is provided, get bookings for that room.
    - If `room_ids` is provided, get bookings for specified rooms.
    - If neither `room_id` nor `room_ids` is provided, get bookings for all rooms.
    - If both `room_id` and `room_ids` are provided, get bookings for all specified rooms.

    `include_red` only applies when getting all rooms and is `False` be default.
    """

    start, end = _default_date_range(start, end)

    if room_ids and room_id and room_id not in room_ids:
        room_ids.append(room_id)

    if room_ids:
        return await exchange_booking_repository.get_bookings_for_certain_rooms(
            room_ids=room_ids,
            from_dt=start,
            to_dt=end,
            user_email=user.email,
        )
    if room_id:
        obj = room_repository.get_by_id(room_id)
        if obj is None:
            raise HTTPException(404, "Room not found")
        return await exchange_booking_repository.get_booking_for_room(
            room_id=room_id,
            from_dt=start,
            to_dt=end,
            user_email=user.email,
        )
    else:
        room_ids = [
            room.id for room in room_repository.get_all(include_red) if room.access_level != "red" or include_red
        ]

        return await exchange_booking_repository.get_bookings_for_certain_rooms(
            room_ids=room_ids,
            from_dt=start,
            to_dt=end,
            user_email=user.email,
        )


@router.get("/bookings/my")
async def my_bookings(
    user: VerifiedDep,
    start: datetime.datetime | None = Query(
        None, description="Start date, if not provided, will be set to 7 days before current date"
    ),
    end: datetime.datetime | None = Query(
        None, description="End date, if not provided, will be set to 14 days after current date"
    ),
) -> list[Booking]:
    start, end = _default_date_range(start, end)
    return await exchange_booking_repository.fetch_user_bookings(attendee_email=user.email, start=start, end=end)


@router.post(
    "/bookings/",
    responses={
        401: {"description": "Invalid user"},
        403: {"description": "Room declined the booking"},
        404: {
            "description": "Room not found OR Booking was removed during booking OR Room attendee not found in booking attendees"
        },
    },
)
async def create_booking(user: VerifiedDep, request: CreateBookingRequest) -> Booking:
    room = room_repository.get_by_id(room_id=request.room_id)
    if room is None:
        raise HTTPException(404, "Room not found")

    innohassle_user = await inh_accounts.get_user(innohassle_id=user.innohassle_id)

    if innohassle_user is None or innohassle_user.innopolis_sso is None:
        raise HTTPException(401, "Invalid user")

    can, why = can_book(user=innohassle_user.innopolis_sso, room=room, start=request.start, end=request.end)
    if not can:
        raise HTTPException(403, why)

    return await exchange_booking_repository.create_booking(
        room=room,
        start=request.start,
        end=request.end,
        title=request.title,
        organizer=innohassle_user,
        participant_emails=request.participant_emails or [],
        user_email=user.email,
    )


class AttendeeDetails(BaseModel):
    name: str | None
    email: str | None
    telegram_username: str | None
    is_staff: bool
    is_student: bool
    is_college: bool


@router.get(
    "/bookings/{outlook_booking_id:path}/get-attendee-details",
    responses={400: {"description": "Invalid email"}, 404: {"description": "Booking not found OR details not found"}},
)
async def get_attendee_details(
    outlook_booking_id: str,
    user_email: EmailStr,
    user: VerifiedDep,
) -> AttendeeDetails:
    logger.info(f"{user.email=} trying to get attendee details for {user_email} in booking {outlook_booking_id}")
    if not (user_email.endswith("@innopolis.university") or user_email.endswith("@innopolis.ru")):
        logger.warning(
            f"{user.email=} trying to get attendee details for {user_email} in booking {outlook_booking_id} but email is not from Innopolis University"
        )
        raise HTTPException(400, "Invalid email")

    searched_user = await inh_accounts.get_user(email=user_email)

    if searched_user is None:
        raise HTTPException(404, "Details not found")

    name = None
    email = None
    telegram_username = None
    is_staff = False
    is_student = False
    is_college = False

    if searched_user.innopolis_info is not None:
        email = searched_user.innopolis_info.email
        name = searched_user.innopolis_info.name
        is_staff = searched_user.innopolis_info.is_staff
        is_student = searched_user.innopolis_info.is_student
        is_college = searched_user.innopolis_info.is_college
    if searched_user.telegram_info is not None:
        telegram_username = searched_user.telegram_info.username

    return AttendeeDetails(
        name=name,
        email=email,
        telegram_username=telegram_username,
        is_staff=is_staff,
        is_student=is_student,
        is_college=is_college,
    )


@router.get(
    "/bookings/{outlook_booking_id:path}",
    responses={404: {"description": "Booking not found OR Room attendee not found in booking attendees"}},
)
async def get_booking(outlook_booking_id: str, user: VerifiedDep) -> Booking:
    calendar_item = await exchange_booking_repository.get_booking(outlook_booking_id)
    if calendar_item is None:
        raise HTTPException(404, "Booking not found")

    if (booking := calendar_item_to_booking(calendar_item, user_email=user.email)) is None:
        raise HTTPException(404, "Room attendee not found in booking attendees")

    return booking


@router.patch(
    "/bookings/{outlook_booking_id:path}",
    responses={
        403: {"description": "You are not the participant of the booking"},
        404: {"description": "Booking not found OR Room attendee not found in booking attendees"},
    },
)
async def update_booking(
    outlook_booking_id: str,
    user: VerifiedDep,
    request: PatchBookingRequest,
) -> Booking:
    booking = await exchange_booking_repository.get_booking(item_id=outlook_booking_id)

    if booking is None:
        raise HTTPException(404, "Booking not found")

    email_index = get_emails_to_attendees_index(booking)
    room = get_first_room_from_emails(email_index.keys())

    if user.email not in email_index:
        raise HTTPException(403, "You are not the participant of the booking")

    if room is None:
        raise HTTPException(400, "Invalid booking")

    innohassle_user = await inh_accounts.get_user(innohassle_id=user.innohassle_id)

    if innohassle_user is None or innohassle_user.innopolis_sso is None:
        raise HTTPException(401, "Invalid user")

    can, why = can_book(
        user=innohassle_user.innopolis_sso,
        room=room,
        start=cast(datetime.datetime, request.start or booking.start),
        end=cast(datetime.datetime, request.end or booking.end),
    )
    if not can:
        raise HTTPException(403, why)

    booking = await exchange_booking_repository.update_booking(
        item_id=outlook_booking_id,
        new_start=request.start,
        new_end=request.end,
        new_title=request.title,
    )

    if booking is None:
        raise HTTPException(404, "Booking not found after update")

    booking.related_to_me = True

    return booking


@router.delete(
    "/bookings/{outlook_booking_id:path}",
    status_code=200,
    responses={
        200: {"description": "Deleted successfully"},
        403: {"description": "You are not the participant of the booking"},
        404: {"description": "Booking not found"},
    },
)
async def delete_booking(user: VerifiedDep, outlook_booking_id: str):
    booking = await exchange_booking_repository.get_booking(item_id=outlook_booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if user.email not in get_emails_to_attendees_index(booking):
        raise HTTPException(403, "You are not the participant of the booking")

    await exchange_booking_repository.delete_booking(item_id=outlook_booking_id, email=user.email)


@router.get(
    "/user/{user_id}/bookings",
    responses={404: {"description": "User not found"}},
)
async def get_user_bookings(
    user_id: str,
    _: ApiKeyDep,
    start: datetime.datetime | None = Query(
        None, description="Start date, if not provided, will be set to 7 days before current date"
    ),
    end: datetime.datetime | None = Query(
        None, description="End date, if not provided, will be set to 14 days after current date"
    ),
) -> list[Booking]:
    start, end = _default_date_range(start, end)

    try:
        innohassle_user = await inh_accounts.get_user(innohassle_id=user_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.json())

    if innohassle_user is None or innohassle_user.innopolis_sso is None:
        raise HTTPException(404, "User not found")

    return await exchange_booking_repository.fetch_user_bookings(
        attendee_email=innohassle_user.innopolis_sso.email,
        start=start,
        end=end,
    )
