import datetime
from json import JSONDecodeError

import httpx
from httpx import HTTPStatusError, Response
from pydantic import BaseModel

from src.config import settings
from src.api.logging_ import logger
from src.modules.bookings.outlook_ics_repository import booking_repository
from src.modules.rooms.repository import room_repository


class MyUniBooking(BaseModel):
    id: int
    "ID of the booking on My University. You can use it to delete the booking."
    room_id: str
    "ID of the room in InNoHassle"
    title: str
    "Title of the booking"
    start: datetime.datetime
    "Start time of booking"
    end: datetime.datetime
    "End time of booking"


class MyUniBookingRepository:
    api_url: str
    api_token: str

    def __init__(self, api_url: str, api_token: str):
        self.api_url = api_url
        self.api_token = api_token

    def get_authorized_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers={"X-Booking-Token": f"{self.api_token}"}, base_url=self.api_url)

    def extract_error(self, response: Response) -> str | None:
        try:
            response.raise_for_status()
        except HTTPStatusError as e:
            logger.warning(e)
            try:
                error_msg = response.json()["error"]
            except (JSONDecodeError, KeyError):
                error_msg = "Unexpected error"

            return error_msg

    async def list_user_bookings(self, email: str) -> tuple[list[MyUniBooking] | None, str | None]:
        async with self.get_authorized_client() as client:
            response = await client.get(
                "/room-booking/list",
                params={
                    "email": email,
                },
            )
            error = self.extract_error(response)
            if error is not None:
                return None, error

            data = response.json()

            if not data["bookings"]:
                # No bookings (data["bookings"] is empty list)
                return [], None

            # Validate the response (data["bookings"] is a dict)
            bookings = data["bookings"].values()
            return [
                MyUniBooking.model_validate(
                    {
                        **booking,
                        "room_id": (await room_repository.get_by_my_uni_id(booking["room_id"])).id,
                        # start_time is "2024-10-17 03:00:00" in MSK time
                        "start": datetime.datetime.strptime(booking["start_time"], "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=datetime.timezone(datetime.timedelta(hours=3))
                        ),
                        "end": datetime.datetime.strptime(booking["end_time"], "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=datetime.timezone(datetime.timedelta(hours=3))
                        ),
                    }
                )
                for booking in bookings
            ], None

    async def create_booking(
        self, email: str, my_uni_room_id: int, title: str, start: datetime.datetime, end: datetime.datetime
    ) -> tuple[bool, str | None]:
        async with self.get_authorized_client() as client:
            print(start.astimezone(datetime.timezone(datetime.timedelta(hours=3))).isoformat(timespec="minutes")[0:16])
            response = await client.post(
                "/room-booking/create",
                params={
                    "email": email,
                    "room": my_uni_room_id,
                    "title": title,
                    "start": start.astimezone(datetime.timezone(datetime.timedelta(hours=3))).isoformat(
                        timespec="minutes"
                    )[0:16],  # "2024-10-17T03:00", msk time
                    "end": end.astimezone(datetime.timezone(datetime.timedelta(hours=3))).isoformat(timespec="minutes")[
                        0:16
                    ],  # "2024-10-17T04:00", msk time
                },
            )
            error = self.extract_error(response)
            if error is not None:
                return False, error

            room = await room_repository.get_by_my_uni_id(my_uni_room_id)
            booking_repository.expire_cache_for_room(room.id)
            return True, None

    async def delete_booking(self, booking_id: int) -> tuple[bool, str | None]:
        async with self.get_authorized_client() as client:
            response = await client.delete(
                "/room-booking/delete",
                params={
                    "id": booking_id,
                },
            )
            error = self.extract_error(response)
            if error is not None:
                return False, error

            return True, None


my_uni_booking_repository = MyUniBookingRepository(
    api_url=settings.my_uni.api_url,
    api_token=settings.my_uni.secret_token.get_secret_value(),
)
