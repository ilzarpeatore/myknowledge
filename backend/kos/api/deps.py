from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from kos.auth import service
from kos.config import Settings, get_settings
from kos.db import get_db
from kos.models import User

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def current_user(request: Request, db: DbDep, settings: SettingsDep) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    user = await service.resolve_session(db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return user


UserDep = Annotated[User, Depends(current_user)]
