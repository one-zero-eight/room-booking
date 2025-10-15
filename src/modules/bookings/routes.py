"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import datetime

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import VerifiedDep
from src.api.exceptions import ObjectNotFound
from src.modules.bookings.exchange_repository import Booking, exchange_booking_repository
from src.modules.bookings.my_uni_repository import MyUniBooking, my_uni_booking_repository
from src.modules.rooms.repository import room_repository

router = APIRouter(tags=["Bookings"])


@router.get("/bookings/")
async def bookings(
    _: VerifiedDep,
    room_id: str | None = Query(None, title="ID for getting single room bookings"),
    room_ids: list[str] | None = Query(None, title="IDs for multiple rooms bookings"),
    start: datetime.datetime = Query(..., description="Start date"),
    end: datetime.datetime = Query(..., description="End date"),
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
    if room_ids and room_id and room_id not in room_ids:
        room_ids.append(room_id)

    if room_ids:
        return exchange_booking_repository.get_bookings_for_certain_rooms(room_ids=room_ids, from_dt=start, to_dt=end)
    if room_id:
        obj = room_repository.get_by_id(room_id)
        if obj is None:
            raise HTTPException(404, "Room not found")
        return exchange_booking_repository.get_booking_for_room(room_id=room_id, from_dt=start, to_dt=end)
    else:
        return exchange_booking_repository.get_bookings_for_all_rooms(from_dt=start, to_dt=end, include_red=include_red)


@router.get("/bookings/my")
async def my_bookings(user: VerifiedDep) -> list[MyUniBooking]:
    # Get the bookings from My University
    bookings, error_message = await my_uni_booking_repository.list_user_bookings(user.email)
    if bookings is None:
        raise ValueError(error_message)

    # Return the bookings
    return bookings


@router.post("/bookings/")
async def create_booking(
    user: VerifiedDep, room_id: str, title: str, start: datetime.datetime, end: datetime.datetime
) -> bool:
    # Check that the room exists
    room = room_repository.get_by_id(room_id)
    if room is None:
        raise ObjectNotFound()
    if not title:
        raise HTTPException(400, "Title is required")

    # Create the booking on My University
    success, error_message = await my_uni_booking_repository.create_booking(
        user.email,
        room.my_uni_id,
        title,
        start,
        end,
    )
    if not success:
        raise HTTPException(409, error_message)

    # Success
    return True


@router.delete("/bookings/{booking_id}")
async def delete_booking(user: VerifiedDep, booking_id: int) -> bool:
    # Check that the owner of the booking is the current user
    bookings, error_message = await my_uni_booking_repository.list_user_bookings(user.email)
    if bookings is None:
        raise ValueError(error_message)
    if booking_id not in [booking.id for booking in bookings]:
        raise ObjectNotFound()

    # Delete the booking from My University
    success, error_message = await my_uni_booking_repository.delete_booking(booking_id)
    if not success:
        raise HTTPException(404, error_message)

    # Success
    return True
