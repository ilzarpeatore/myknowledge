"""Administrative commands.

kos migrate                 apply database migrations
kos create-user <username>  create the single account (password asked interactively)
kos reset-password <user>   change the password and close all sessions
kos verify-quotes           check every stored quote against its source fragment
kos download-whisper        download the speech-to-text model (worker, needs internet)
kos create-token <name> [--propose]  token for an MCP client (shown once)
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from kos.auth import service
from kos.db import get_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def _ask_password() -> str:
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    return pw


async def _create_user(username: str) -> None:
    async with get_sessionmaker()() as db:
        await service.create_single_user(db, username, _ask_password())
    print(f"user {username!r} created")


async def _reset_password(username: str) -> None:
    async with get_sessionmaker()() as db:
        await service.reset_password(db, username, _ask_password())
    print("password changed; all sessions closed")


async def _ai_check() -> None:
    from pydantic import BaseModel

    from kos.ai import service

    class Pong(BaseModel):
        ok: bool
        language: str

    async with get_sessionmaker()() as db:
        try:
            out = await service.run_structured(
                db,
                task="check",
                prompt_key="connection_check",
                schema=Pong,
                instructions=(
                    "Return ok=true and the ISO code of the language of: «Hola, ¿qué tal?»"
                ),
            )
        except service.AIError as exc:
            sys.exit(f"AI check failed: {type(exc).__name__}: {exc}")
        run = await db.scalar(select_last_run())
    print(f"AI check ok: {out.model_dump()} | model {run.model_served} | cost {run.cost_usd} USD")


def select_last_run():
    from sqlalchemy import select

    from kos.models import AIRun

    return select(AIRun).order_by(AIRun.created_at.desc()).limit(1)


async def _reembed() -> None:
    from sqlalchemy import select

    from kos.ingestion.pipeline import embed_job
    from kos.models import SourceVersion

    async with get_sessionmaker()() as db:
        ids = (await db.scalars(select(SourceVersion.id))).all()
        db.add_all(embed_job(i) for i in ids)
        await db.commit()
    print(f"queued {len(ids)} embedding jobs")


async def _verify_quotes() -> None:
    from kos.evidence.service import audit_quotes

    async with get_sessionmaker()() as db:
        r = await audit_quotes(db)
    print(f"quotes checked: {r['checked']}; mismatches: {len(r['mismatches'])}")
    for m in r["mismatches"]:
        print(f"  evidence {m['evidence_id']} (claim {m['claim_id']})")
    if not r["ok"]:
        sys.exit(1)


async def _create_token(name: str, propose: bool) -> None:
    from kos.mcp_server import tokens

    async with get_sessionmaker()() as db:
        tok, raw = await tokens.create(db, name, propose)
    print(f"token «{tok.name}» ({tok.scopes}); copy it now, it is not shown again:\n{raw}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kos")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("worker")
    sub.add_parser("download-models")
    sub.add_parser("download-whisper")
    tok = sub.add_parser("create-token")
    tok.add_argument("name")
    tok.add_argument("--propose", action="store_true", help="also allow proposing changes")
    sub.add_parser("reembed")
    sub.add_parser("ai-check")
    sub.add_parser("verify-quotes")
    sub.add_parser("create-user").add_argument("username")
    sub.add_parser("reset-password").add_argument("username")
    args = parser.parse_args()

    try:
        if args.cmd == "migrate":
            command.upgrade(alembic_config(), "head")
        elif args.cmd == "download-models":
            from kos.config import get_settings
            from kos.embeddings import download_model

            s = get_settings()
            download_model(Path(s.embedding_model_dir), s.embedding_onnx_file)
            print(f"embedding model ready in {s.embedding_model_dir}")
        elif args.cmd == "create-token":
            asyncio.run(_create_token(args.name, args.propose))
        elif args.cmd == "download-whisper":
            from kos.media.transcribe import download_model as whisper_download

            print(f"speech-to-text model ready in {whisper_download()}")
        elif args.cmd == "ai-check":
            asyncio.run(_ai_check())
        elif args.cmd == "verify-quotes":
            asyncio.run(_verify_quotes())
        elif args.cmd == "reembed":
            asyncio.run(_reembed())
        elif args.cmd == "worker":
            from kos.jobs.worker import main as worker_main

            worker_main()
        elif args.cmd == "create-user":
            asyncio.run(_create_user(args.username))
        elif args.cmd == "reset-password":
            asyncio.run(_reset_password(args.username))
    except (service.AuthError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
