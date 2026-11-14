"""Admin CLI: python -m app.cli create-admin <username> [--reset]. Password via stdin only."""
import argparse
import asyncio
import getpass
import sys

from app import auth, db


def _read_password() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Admin password: ")
    return sys.stdin.readline().rstrip("\r\n")


async def create_admin(dsn: str, username: str, password: str, reset: bool) -> str:
    conn = await db.connect(dsn)
    try:
        exists = await conn.fetchval(
            f"select 1 from {db.SCHEMA}.admins where username = $1", username
        )
        if exists and not reset:
            return "exists"
        async with conn.transaction():
            await conn.execute(
                f"insert into {db.SCHEMA}.admins (username, password_hash) values ($1, $2) "
                "on conflict (username) do update set password_hash = excluded.password_hash",
                username,
                auth.hash_password(password),
            )
            if exists:  # a reset signs that admin out everywhere
                await conn.execute(
                    f"delete from {db.SCHEMA}.auth_sessions where kind = 'admin' and subject = $1",
                    username,
                )
        return "reset" if exists else "created"
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create an admin (password read from stdin)")
    p.add_argument("username")
    p.add_argument("--reset", action="store_true", help="change the password of an existing admin")
    args = parser.parse_args(argv)

    dsn = db.database_url_from_env()
    if dsn is None:
        print("LF_DATABASE_URL is not set", file=sys.stderr)
        return 1
    name = args.username
    if not name or len(name) > auth.MAX_USERNAME_LEN or not name.isprintable():
        print("username must be 1-64 printable characters", file=sys.stderr)
        return 1
    password = _read_password()
    if len(password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(create_admin(dsn, args.username, password, args.reset))
    except Exception as exc:  # never print the message: it can contain the DSN
        print(f"database error ({type(exc).__name__})", file=sys.stderr)
        return 1
    if result == "exists":
        print(f"admin {args.username!r} already exists (use --reset to change the password)")
    else:
        print(f"admin {args.username!r}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
