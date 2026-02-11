"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import asyncio
import datetime
from typing import cast

import httpx
from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import ApiKeyDep, VerifiedDep
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
        404: {"description": "Booking not found"},
        403: {"description": "Unauthorized"},
    },
)


@router.get("/bookings/", responses={404: {"description": "Room not found"}})
async def bookings(
    _: VerifiedDep,
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
        )
    if room_id:
        obj = room_repository.get_by_id(room_id)
        if obj is None:
            raise HTTPException(404, "Room not found")
        return await exchange_booking_repository.get_booking_for_room(
            room_id=room_id,
            from_dt=start,
            to_dt=end,
        )
    else:
        return await exchange_booking_repository.get_bookings_for_all_rooms(
            from_dt=start,
            to_dt=end,
            include_red=include_red,
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
    return await exchange_booking_repository._fetch_user_bookings(attendee_email=user.email, start=start, end=end)


@router.post("/bookings/")
async def create_booking(
    user: VerifiedDep,
    request: CreateBookingRequest,
) -> Booking:
    room = room_repository.get_by_id(room_id=request.room_id)
    if room is None:
        raise HTTPException(404, "Room not found")

    try:
        innohassle_user = await inh_accounts.get_user(innohassle_id=user.innohassle_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.json())
    if innohassle_user is None or innohassle_user.innopolis_sso is None:
        raise HTTPException(401, "Invalid user")

    can, why = can_book(user=innohassle_user.innopolis_sso, room=room, start=request.start, end=request.end)
    if not can:
        raise HTTPException(403, why)

    item_id = await exchange_booking_repository.create_booking(
        room=room,
        start=request.start,
        end=request.end,
        title=request.title,
        organizer_email=user.email,
        participant_emails=request.participant_emails or [],
    )

    await asyncio.sleep(2)

    tries = 10
    booking = None
    for _ in range(tries):  # TODO: Rooms, that don't answer automatically, should be handled individually
        item = await exchange_booking_repository.get_booking(item_id=item_id)

        if item is None:
            raise HTTPException(404, "Booking was removed during booking")

        booking = calendar_item_to_booking(item, room_id=room.id)
        email_index = get_emails_to_attendees_index(item)
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


@router.get("/bookings/{outlook_booking_id}")
async def get_booking(outlook_booking_id: str, _: VerifiedDep) -> Booking:
    calendar_item = await exchange_booking_repository.get_booking(outlook_booking_id)
    if calendar_item is None:
        raise HTTPException(404, "Booking not found")

    if (booking := calendar_item_to_booking(calendar_item)) is None:
        raise HTTPException(404, "Room attendee not found in booking attendees")

    return booking


@router.patch("/bookings/{outlook_booking_id}")
async def update_booking(
    outlook_booking_id: str,
    user: VerifiedDep,
    request: PatchBookingRequest,
):
    booking = await exchange_booking_repository.get_booking(item_id=outlook_booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    email_index = get_emails_to_attendees_index(booking)
    room = get_first_room_from_emails(email_index.keys())

    if user.email not in email_index:
        raise HTTPException(403, "You are not the participant of the booking")

    if room is None:
        raise HTTPException(400, "Invalid booking")

    try:
        innohassle_user = await inh_accounts.get_user(innohassle_id=user.innohassle_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.json())
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

    return await exchange_booking_repository.update_booking(
        item_id=outlook_booking_id,
        new_start=request.start,
        new_end=request.end,
        new_title=request.title,
    )


@router.delete(
    "/bookings/{outlook_booking_id}",
    status_code=200,
    responses={
        200: {"description": "Deleted successfully"},
    },
)
async def delete_booking(user: VerifiedDep, outlook_booking_id: str):
    booking = await exchange_booking_repository.get_booking(item_id=outlook_booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if user.email not in get_emails_to_attendees_index(booking):
        raise HTTPException(403, "You are not the participant of the booking")

    await exchange_booking_repository.delete_booking(item_id=outlook_booking_id, email=user.email)


@router.get("/user/{user_id}/bookings")
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

    return await exchange_booking_repository._fetch_user_bookings(
        attendee_email=innohassle_user.innopolis_sso.email,
        start=start,
        end=end,
    )
