from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ConfigDict


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
    ics_url: str = Field(exclude=True)
    "URL of the ICS calendar"


class Accounts(SettingBaseModel):
    """InNoHassle-Accounts integration settings"""

    api_url: str = "https://api.innohassle.ru/accounts/v0"
    "URL of the Accounts API"
    well_known_url: str = "https://api.innohassle.ru/accounts/v0/.well-known"
    "URL of the well-known endpoint for the Accounts API"


class Settings(SettingBaseModel):
    """
    Settings for the application.
    """

    schema_: str = Field(None, alias="$schema")
    environment: Environment = Environment.DEVELOPMENT
    "App environment flag"
    app_root_path: str = ""
    'Prefix for the API path (e.g. "/api/v0")'
    rooms: list[Room] = []
    "List of rooms"
    ics_cache_ttl_seconds: int = 60
    "TTL for the ICS cache in seconds"
    cors_allow_origins: list[str] = ["https://innohassle.ru", "http://localhost:3000"]
    "CORS origins, used by FastAPI CORSMiddleware"
    accounts: Accounts
    "InNoHassle-Accounts integration settings"

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        with open(path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)

        return cls.model_validate(yaml_config)

    @classmethod
    def save_schema(cls, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            schema = {"$schema": "http://json-schema.org/draft-07/schema#", **cls.model_json_schema()}
            yaml.dump(schema, f, sort_keys=False)
