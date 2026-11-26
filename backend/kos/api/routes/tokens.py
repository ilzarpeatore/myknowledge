"""Personal access tokens for MCP clients (managed from the app, with the session)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.api.deps import DbDep, UserDep
from kos.mcp_server import tokens
from kos.models import ApiToken

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    propose: bool = False


@router.get("", response_model=list[TokenOut])
async def list_tokens(db: DbDep, _: UserDep):
    return (await db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc()))).all()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_token(body: TokenIn, db: DbDep, _: UserDep):
    tok, raw = await tokens.create(db, body.name, body.propose)
    # The only time the token is shown.
    return {"token": raw, **TokenOut.model_validate(tok, from_attributes=True).model_dump()}


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: uuid.UUID, db: DbDep, _: UserDep):
    tok = await db.get(ApiToken, token_id)
    if tok is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token no encontrado")
    await tokens.revoke(db, tok)
