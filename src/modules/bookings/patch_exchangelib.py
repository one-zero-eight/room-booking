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
            pycurl.POSTFIELDS: curl_options[pycurl.READFUNCTION]().decode(),
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


exchangelib.protocol.BaseProtocol.raw_session = raw_session
