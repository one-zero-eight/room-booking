import datetime
from typing import Annotated

import exchangelib
import pytz
from pydantic import AfterValidator

from src.api.logging_ import logger

msk_timezone = pytz.timezone("Europe/Moscow")


def to_msk(dt: datetime.datetime | datetime.date) -> datetime.datetime:
    if isinstance(dt, exchangelib.EWSDateTime):
        return dt.astimezone(exchangelib.EWSTimeZone.from_pytz(msk_timezone))
    if isinstance(dt, datetime.datetime):
        return dt.astimezone(msk_timezone)
    # All-day events use EWSDate (date-only, no timezone).
    return msk_timezone.localize(datetime.datetime.combine(dt, datetime.time.min))


def _check_msk(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        logger.warning("Datetime must be timezone-aware: %s, setting to MSK", dt)
    return to_msk(dt)


MSKDatetime = Annotated[datetime.datetime, AfterValidator(_check_msk)]
