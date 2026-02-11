__all__ = ["lifespan"]

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.modules.inh_accounts_sdk import inh_accounts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Application startup
    await inh_accounts.update_key_set()
    yield
