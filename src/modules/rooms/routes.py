"""
Routes for the rooms information.
"""

__all__ = ["router"]

from fastapi import APIRouter

from src.api.dependencies import VerifiedDep
from src.config_schema import Room
from src.modules.rooms.repository import room_repository

router = APIRouter(tags=["Rooms"])


@router.get("/rooms/")
async def rooms(_: VerifiedDep) -> list[Room]:
    return room_repository.get_all()
