"""Administrative commands.

kos migrate                 apply database migrations
kos create-user <username>  create the single account (password asked interactively)
kos reset-password <user>   change the password and close all sessions
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="kos")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("worker")
    sub.add_parser("create-user").add_argument("username")
    sub.add_parser("reset-password").add_argument("username")
    args = parser.parse_args()

    try:
        if args.cmd == "migrate":
            command.upgrade(alembic_config(), "head")
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
