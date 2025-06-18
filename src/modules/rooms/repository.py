from src.config import settings
from src.config_schema import Room


class RoomsRepository:
    rooms: list[Room]
    room_by_id: dict[str, Room]
    room_by_my_uni_id: dict[int, Room]
    room_by_email: dict[str, Room]

    def __init__(self, rooms: list[Room]):
        self.rooms = rooms
        self.room_by_id = {room.id: room for room in self.rooms}
        self.room_by_my_uni_id = {room.my_uni_id: room for room in self.rooms}
        self.room_by_email = {room.resource_email: room for room in self.rooms}

    def get_all(self, include_red: bool = False) -> list[Room]:
        return [room for room in self.rooms if room.access_level != "red" or include_red]

    def get_by_id(self, room_id: str) -> Room | None:
        return self.room_by_id.get(room_id)

    def get_by_ids(self, room_ids: list[str]) -> list[Room | None]:
        return [self.get_by_id(room_id) for room_id in room_ids]

    def get_by_my_uni_id(self, my_uni_room_id: int) -> Room | None:
        return self.room_by_my_uni_id.get(my_uni_room_id)


room_repository: RoomsRepository = RoomsRepository(settings.rooms)
