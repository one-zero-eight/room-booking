import datetime
from typing import Literal

from pydantic import BaseModel, computed_field, model_validator

from src.api.logging_ import logger

type BookingStatus = Literal["Accept", "Tentative", "Decline", "Unknown"]


class Attendee(BaseModel):
    email: str
    "Email of the attendee"
    status: BookingStatus | None
    "Response status of the attendee"
    assosiated_room_id: str | None
    "If attendee is a room, ID of the room they are associated with, otherwise None"


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
    attendees: list[Attendee] | None
    "List of attendees of the booking"

    @computed_field
    @property
    def id(self) -> str:
        "ID of the booking, computed from room_id, start and end"
        return f"{self.room_id}-{round(self.start.timestamp())}-{round(self.end.timestamp())}"


class CreateBookingRequest(BaseModel):
    room_id: str
    "ID of the room to book"
    title: str
    "Title of the booking"
    start: datetime.datetime
    "Start time of the booking"
    end: datetime.datetime
    "End time of the booking"
    participant_emails: list[str] | None
    "List of participant emails to invite to the booking"


class PatchBookingRequest(BaseModel):
    title: str | None
    "New title of the booking"
    start: datetime.datetime | None
    "New start time of the booking"
    end: datetime.datetime | None
    "New end time of the booking"


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
