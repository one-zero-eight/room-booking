__all__ = ["ApiKeyDep", "VerifiedDep"]

from typing import Annotated

from fastapi import Depends

from src.modules.auth.dependencies import api_key_dep, verify_user
from src.modules.tokens.repository import UserTokenData

VerifiedDep = Annotated[UserTokenData, Depends(verify_user)]

ApiKeyDep = Annotated[str, Depends(api_key_dep)]
"""
Dependency for checking if the request is coming from an authorized external service.
"""
