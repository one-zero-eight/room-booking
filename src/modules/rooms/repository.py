from src.config import settings
from src.config_schema import Room


class RoomsRepository:
    async def get_all(self) -> list[Room]:
        return settings.rooms

    async def get_by_id(self, room_id: str) -> Room | None:
        for room in settings.rooms:
            if room.id == room_id:
                return room
        return None

    async def get_by_my_uni_id(self, my_uni_room_id: int) -> Room | None:
        for room in settings.rooms:
            if room.my_uni_id == my_uni_room_id:
                return room
        return None


room_repository: RoomsRepository = RoomsRepository()
