from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class SettingBaseModel(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid")


class Room(SettingBaseModel):
    """Room description."""

    id: str
    "Room slug"
    title: str
    "Room title"
    short_name: str
    "Shorter version of room title"
    resource_email: str = Field(exclude=True)
    "Email of the room resource"
    capacity: int | None = None
    "Room capacity, amount of people"
    access_level: Literal["yellow", "red", "special"] | None = None
    "Access level to the room. Yellow = for students. Red = for employees. Special = special rules apply."
    restrict_daytime: bool = False
    "Prohibit to book during working hours. True = this room is available only at night 19:00-8:00, or full day on weekends."


class AccessToRoom(SettingBaseModel):
    email: str
    "Email of the user"
    reason: str = ""
    "Reason for access (f.e. 'Leader of 'one-zero-eight' club', or `Academic Tutorship`)"


class Accounts(SettingBaseModel):
    """InNoHassle Accounts integration settings"""

    api_url: str = "https://api.innohassle.ru/accounts/v0"
    "URL of the Accounts API"
    api_jwt_token: SecretStr
    "JWT token for accessing the Accounts API as a service"


class Exchange(SettingBaseModel):
    """Exchange (Outlook) integration settings"""

    ews_endpoint: str = "https://mail.innopolis.ru/EWS/Exchange.asmx"
    "URL of the EWS endpoint"
    username: str
    "Username for accessing the EWS endpoint (email)"
    password: SecretStr
    "Password for accessing the EWS endpoint"
    ews_callback_url: str | None = None
    "URL of the EWS callback for push subscription"


class Settings(SettingBaseModel):
    """Settings for the application."""

    schema_: str = Field(None, alias="$schema")
    environment: Environment = Environment.DEVELOPMENT
    "App environment flag"
    app_root_path: str = ""
    'Prefix for the API path (e.g. "/api/v0")'
    api_key: SecretStr
    "Secret key for accessing API by external services"
    rooms: list[Room] = []
    "List of rooms"
    access_lists: dict[str, list[AccessToRoom]] = {}
    "Dictionary of access lists (room id -> access list)"
    ttl_bookings_from_account_calendar: int = 60
    "TTL for the bookings from account calendar cache in seconds"
    ttl_bookings_from_busy_info: int = 60
    "TTL for the bookings from busy info cache in seconds"
    recently_canceled_booking_ttl_sec: int = 300
    "TTL for the recently-canceled booking IDs cache in seconds (skip cancel if already canceled within this window)"
    cors_allow_origin_regex: str = ".*"
    "Allowed origins for CORS: from which domains requests to the API are allowed. Specify as a regex: `https://.*.innohassle.ru`"
    accounts: Accounts
    "InNoHassle Accounts integration settings"
    exchange: Exchange
    "Exchange (Outlook) integration settings"

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        with open(path, encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)

        return cls.model_validate(yaml_config)

    @classmethod
    def save_schema(cls, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            schema = {"$schema": "https://json-schema.org/draft-07/schema", **cls.model_json_schema()}
            yaml.dump(schema, f, sort_keys=False)
