from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from kos.api.deps import DbDep, SettingsDep, UserDep
from kos.auth import service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class MeOut(BaseModel):
    username: str


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, request: Request, response: Response, db: DbDep, s: SettingsDep):
    ip = request.client.host if request.client else None
    try:
        new = await service.login(
            db, s, body.username, body.password, ip, request.headers.get("user-agent")
        )
    except service.TooManyAttempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts; try again later"
        ) from None
    except service.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from None
    response.set_cookie(
        s.session_cookie_name,
        new.token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    return MeOut(username=body.username.strip().lower())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbDep, s: SettingsDep):
    token = request.cookies.get(s.session_cookie_name)
    if token:
        await service.logout(db, token)
    response.delete_cookie(s.session_cookie_name, path="/")


@router.get("/me", response_model=MeOut)
async def me(user: UserDep):
    return MeOut(username=user.username)
