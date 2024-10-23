"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import datetime
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import VerifiedDep
from src.api.exceptions import ObjectNotFound
from src.modules.bookings.exchange_repository import Booking, exchange_booking_repository
from src.modules.bookings.my_uni_repository import MyUniBooking, my_uni_booking_repository
from src.modules.rooms.repository import room_repository

router = APIRouter(tags=["Bookings"])

_now = datetime.datetime.now(datetime.UTC)


@router.get("/bookings/")
async def bookings(
    start: datetime.datetime = Query(example=_now.isoformat(timespec="minutes")),
    end: datetime.datetime = Query(example=(_now + timedelta(hours=9)).isoformat(timespec="minutes")),
) -> list[Booking]:
    # Fetch the bookings from Outlook
    return exchange_booking_repository.get_bookings_for_all_rooms(start, end)


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
