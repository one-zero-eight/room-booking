import datetime

import exchangelib
import pycurl
import requests
from requests_curl import CURLAdapter
from requests_curl.request import CURLRequest

from src.config import settings

USERPWD = f"{settings.exchange.username}:{settings.exchange.password.get_secret_value()}"

original_build_curl_options = CURLRequest._build_curl_options


def build_curl_options(self):
    curl_options = original_build_curl_options(self)

    # Authentication using NTLM
    curl_options.update(
        {
            pycurl.HTTPAUTH: pycurl.HTTPAUTH_NTLM,
            pycurl.USERPWD: USERPWD,
        }
    )

    # Let curl handle the POSTFIELDS instead of using READFUNCTION
    curl_options.update(
        {
            pycurl.POSTFIELDS: curl_options[pycurl.READFUNCTION](),
        }
    )
    curl_options.pop(pycurl.READFUNCTION)
    curl_options.pop(pycurl.UPLOAD)
    return curl_options


CURLRequest._build_curl_options = build_curl_options


def raw_session(cls, prefix, oauth2_client=None, oauth2_session_params=None, oauth2_token_endpoint=None):
    session = requests.Session()
    session.mount("http://", CURLAdapter())
    session.mount("https://", CURLAdapter())
    session.headers.update(exchangelib.protocol.DEFAULT_HEADERS)
    session.headers["User-Agent"] = cls.USERAGENT
    return session


def get_free_busy_info(self, accounts, start, end, merged_free_busy_interval=30, requested_view="DetailedMerged"):
    """Return free/busy information for a list of accounts.

    :param accounts: A list of (account, attendee_type, exclude_conflicts) tuples, where account is either an
        Account object or a string, attendee_type is a MailboxData.attendee_type choice, and exclude_conflicts is a
        boolean.
    :param start: The start datetime of the request
    :param end: The end datetime of the request
    :param merged_free_busy_interval: The interval, in minutes, of merged free/busy information (Default value = 30)
    :param requested_view: The type of information returned. Possible values are defined in the
        FreeBusyViewOptions.requested_view choices. (Default value = 'DetailedMerged')

    :return: A generator of FreeBusyView objects
    """
    from exchangelib.account import Account
    from exchangelib.properties import (
        DaylightTime,
        FreeBusyViewOptions,
        MailboxData,
        StandardTime,
        TimeWindow,
        TimeZone,
    )
    from exchangelib.services.get_user_availability import GetUserAvailability

    timezone = TimeZone(
        bias=-180,
        standard_time=StandardTime(bias=0, time=datetime.time(0, 0), occurrence=1, iso_month=1, weekday="Monday"),
        daylight_time=DaylightTime(bias=0, time=datetime.time(0, 0), occurrence=5, iso_month=12, weekday="Sunday"),
    )

    return GetUserAvailability(self).call(
        tzinfo=start.tzinfo,
        mailbox_data=[
            MailboxData(
                email=account.primary_smtp_address if isinstance(account, Account) else account,
                attendee_type=attendee_type,
                exclude_conflicts=exclude_conflicts,
            )
            for account, attendee_type, exclude_conflicts in accounts
        ],
        timezone=timezone,
        free_busy_view_options=FreeBusyViewOptions(
            time_window=TimeWindow(start=start, end=end),
            merged_free_busy_interval=merged_free_busy_interval,
            requested_view=requested_view,
        ),
    )


exchangelib.protocol.BaseProtocol.raw_session = raw_session
exchangelib.protocol.Protocol.get_free_busy_info = get_free_busy_info
