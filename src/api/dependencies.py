__all__ = ["VerifiedDep"]

from typing import TypeAlias, Annotated

from fastapi import Depends

from src.modules.auth.dependencies import verify_user
from src.modules.tokens.repository import UserTokenData

VerifiedDep: TypeAlias = Annotated[UserTokenData, Depends(verify_user)]
