"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import datetime

from fastapi import APIRouter

from src.modules.bookings.repository import booking_repository, Booking

router = APIRouter(tags=["Bookings"])


@router.get("/bookings/")
async def bookings(start: datetime.datetime, end: datetime.datetime) -> list[Booking]:
    return await booking_repository.get_bookings_for_all_rooms(start, end)
