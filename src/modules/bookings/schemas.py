import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

from src.api.logging_ import logger


class Booking(BaseModel):
    room_id: str
    "ID of the room"
    start: datetime.datetime
    "Start time of booking"
    end: datetime.datetime
    "End time of booking"
    title: str
    "Title of the booking"
    outlook_booking_id: str | None
    "ID of outlook booking"
    emails: list[str]
    "Emails of attendees"


class CreateBookingResponse(BaseModel):
    outlook_booking_id: str


type BookingStatus = Literal["Accept", "Tentative", "Decline", "Unknown"]


class BookingStatusModel(BaseModel):
    room_id: str
    status: BookingStatus
    last_response_time: datetime.datetime | None = None
    conversation_history: list[str] | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "BookingStatusModel":
        if self.status not in ["Accept", "Tentative", "Decline", "Unknown"]:
            logger.warning(f"Unknown status: {self.status}")
            self.status = "Unknown"
        return self
