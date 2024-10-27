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
    my_uni_id: int
    "ID of room on My University portal"
    capacity: int | None = None
    "Room capacity, amount of people"
    access_level: Literal["yellow", "red", "special"] | None = None
    "Access level to the room. Yellow = for students. Red = for employees. Special = special rules apply."
    restrict_daytime: bool = False
    "Prohibit to book during working hours. True = this room is available only at night 19:00-8:00, or full day on weekends."


class Accounts(SettingBaseModel):
    """InNoHassle Accounts integration settings"""

    api_url: str = "https://api.innohassle.ru/accounts/v0"
    "URL of the Accounts API"
    api_jwt_token: SecretStr
    "JWT token for accessing the Accounts API as a service"


class MyUni(SettingBaseModel):
    """My University integration settings"""

    api_url: str = "https://my.university.innopolis.ru/apiv1"
    "URL of the My University API"
    secret_token: SecretStr | None = None
    "Secret token for My University API"


class Exchange(SettingBaseModel):
    """Exchange (Outlook) integration settings"""

    ews_endpoint: str = "https://mail.innopolis.ru/EWS/Exchange.asmx"
    "URL of the EWS endpoint"
    username: str
    "Username for accessing the EWS endpoint (email)"
    password: SecretStr
    "Password for accessing the EWS endpoint"


class Settings(SettingBaseModel):
    """Settings for the application."""

    schema_: str = Field(None, alias="$schema")
    environment: Environment = Environment.DEVELOPMENT
    "App environment flag"
    app_root_path: str = ""
    'Prefix for the API path (e.g. "/api/v0")'
    rooms: list[Room] = []
    "List of rooms"
    ics_cache_ttl_seconds: int = 60
    "TTL for the ICS cache in seconds"
    cors_allow_origin_regex: str = ".*"
    "Allowed origins for CORS: from which domains requests to the API are allowed. Specify as a regex: `https://.*.innohassle.ru`"
    accounts: Accounts
    "InNoHassle Accounts integration settings"
    my_uni: MyUni = MyUni()
    "My University integration settings"
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
