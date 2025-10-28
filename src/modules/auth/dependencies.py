__all__ = ["verify_user"]

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.exceptions import IncorrectCredentialsException
from src.modules.tokens.repository import TokenRepository, UserTokenData

bearer_scheme = HTTPBearer(
    scheme_name="Bearer",
    description="Token from [InNoHassle Accounts](https://innohassle.ru/account/token)",
    bearerFormat="JWT",
    auto_error=False,  # We'll handle error manually
)


async def verify_user(
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserTokenData:
    token = bearer and bearer.credentials
    if not token:
        raise IncorrectCredentialsException(no_credentials=True)

    token_data = await TokenRepository.verify_user_token(token, IncorrectCredentialsException())
    return token_data
