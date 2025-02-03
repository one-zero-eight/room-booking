"""
Routes for the rooms information.
"""

__all__ = ["router"]

import datetime

from fastapi import APIRouter, HTTPException

from src.api.dependencies import VerifiedDep
from src.config_schema import Room
from src.modules.bookings.exchange_repository import Booking, exchange_booking_repository
from src.modules.rooms.repository import room_repository

router = APIRouter(tags=["Rooms"])


@router.get("/rooms/")
async def rooms(_: VerifiedDep) -> list[Room]:
    return room_repository.get_all()


@router.get(
    "/room/{id}",
    responses={
        200: {"description": "Room info"},
        404: {"description": "Room not found"},
    },
)
async def room_route(id: str, _: VerifiedDep) -> Room:
    obj = room_repository.get_by_id(id)
    if obj is None:
        raise HTTPException(404, "Room not found")
    return obj


@router.get(
    "/room/{id}/bookings",
    responses={
        200: {"description": "Room bookings"},
        404: {"description": "Room not found"},
    },
)
async def room_bookings_route(
    id: str, _: VerifiedDep, start: datetime.datetime, end: datetime.datetime
) -> list[Booking]:
    obj = room_repository.get_by_id(id)
    if obj is None:
        raise HTTPException(404, "Room not found")
    return exchange_booking_repository.get_booking_for_room(room_id=id, from_dt=start, to_dt=end)
