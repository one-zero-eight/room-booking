import exchangelib
import httpx


def raw_session(cls, prefix, oauth2_client=None, oauth2_session_params=None, oauth2_token_endpoint=None):
    # Use httpx with http2. Return a session that is somewhat compatible with requests.Session
    session = httpx.Client(http2=True)
    session.headers.update(exchangelib.protocol.DEFAULT_HEADERS)
    session.headers["User-Agent"] = cls.USERAGENT
    session.get_adapter = lambda _: None
    _post = session.post

    def post(url, data, *args, **kwargs):
        kwargs.pop("allow_redirects", None)
        kwargs.pop("stream", None)
        response = _post(url, content=data, *args, **kwargs)
        response.iter_content = response.iter_bytes
        return response

    session.post = post
    return session


# Patch exchangelib to use httpx with http2 instead of requests
exchangelib.protocol.BaseProtocol.raw_session = raw_session
