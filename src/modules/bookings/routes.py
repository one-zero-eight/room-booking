"""
Lists of bookings for rooms.
"""

__all__ = ["router"]

import datetime
from datetime import timedelta

from fastapi import APIRouter, Query

from src.modules.bookings.repository import booking_repository, Booking

router = APIRouter(tags=["Bookings"])

_now = datetime.datetime.now(datetime.UTC)


@router.get("/bookings/")
async def bookings(
    start: datetime.datetime = Query(example=_now.isoformat(timespec="minutes")),
    end: datetime.datetime = Query(example=(_now + timedelta(hours=9)).isoformat(timespec="minutes")),
) -> list[Booking]:
    return await booking_repository.get_bookings_for_all_rooms(start, end)
