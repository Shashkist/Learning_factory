"""Admin CLI: python -m app.cli create-admin <username> [--reset]. Password via stdin only.

create-admin is break-glass: when Google sign-in is configured (D39) the password login it
feeds is refused unless LF_ADMIN_PASSWORD_LOGIN=1 is set for the manager.
"""
import argparse
import asyncio
import datetime as dt
import getpass
import os
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


SMOKE_COHORT = "lf-smoke"
SMOKE_ID = "990000000000000000000000000001"  # 30 digits: cannot be a real id
SMOKE_NAME = "Smoke Test"


async def smoke_student(dsn: str, secret: str, password: str | None) -> None:
    """Upsert the synthetic smoke student (password given), or disable it (password None)."""
    h = auth.id_hash(SMOKE_ID, secret)
    conn = await db.connect(dsn)
    try:
        async with conn.transaction():
            if password is None:
                await conn.execute(
                    f"update {db.SCHEMA}.students set disabled = true where id_hash = $1", h
                )
            else:
                until = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
                cid = await conn.fetchval(
                    f"select id from {db.SCHEMA}.cohorts where name = $1 order by id limit 1",
                    SMOKE_COHORT,
                )
                pw_hash = auth.hash_password(password)
                if cid is None:
                    cid = await conn.fetchval(
                        f"insert into {db.SCHEMA}.cohorts (name, password_hash, access_until) "
                        "values ($1, $2, $3) returning id",
                        SMOKE_COHORT,
                        pw_hash,
                        until,
                    )
                else:
                    await conn.execute(
                        f"update {db.SCHEMA}.cohorts set password_hash = $2, access_until = $3 "
                        "where id = $1",
                        cid,
                        pw_hash,
                        until,
                    )
                await conn.execute(
                    f"insert into {db.SCHEMA}.students (id_hash, name, cohort_id, disabled) "
                    "values ($1, $2, $3, false) on conflict (id_hash) do update set "
                    "name = excluded.name, cohort_id = excluded.cohort_id, disabled = false",
                    h,
                    SMOKE_NAME,
                    cid,
                )
            await conn.execute(
                f"delete from {db.SCHEMA}.auth_sessions where kind = 'student' and subject = $1", h
            )
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create an admin (password read from stdin)")
    p.add_argument("username")
    p.add_argument("--reset", action="store_true", help="change the password of an existing admin")
    sp = sub.add_parser(
        "smoke-student", help="upsert the synthetic smoke student (password on stdin)"
    )
    sp.add_argument("--disable", action="store_true", help="disable it and sign it out")
    args = parser.parse_args(argv)

    if args.command == "smoke-student":
        return _smoke_main(args.disable)
    dsn = db.database_url_from_env()
    if dsn is None:
        print("LF_DATABASE_URL is not set", file=sys.stderr)
        return 1
    name = args.username
    if not name or len(name) > auth.MAX_USERNAME_LEN or not name.isprintable() or ":" in name:
        print("username must be 1-64 printable characters, no colon", file=sys.stderr)
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


def _smoke_main(disable: bool) -> int:
    dsn = db.database_url_from_env()
    if dsn is None:
        print("LF_DATABASE_URL is not set", file=sys.stderr)
        return 1
    secret = os.environ.get("LF_ID_SECRET")
    if not secret:
        print("LF_ID_SECRET is not set", file=sys.stderr)
        return 1
    password = None
    if not disable:
        password = _read_password()
        if len(password) < 8:
            print("password must be at least 8 characters", file=sys.stderr)
            return 1
    try:
        asyncio.run(smoke_student(dsn, secret, password))
    except Exception as exc:  # never print the message: it can contain the DSN
        print(f"database error ({type(exc).__name__})", file=sys.stderr)
        return 1
    print("smoke student disabled" if disable else "smoke student ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
