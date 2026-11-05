"""Initialize an external application home and start Whist."""

import argparse
import secrets
import shutil
from configparser import ConfigParser
from getpass import getpass
from pathlib import Path

from argon2 import PasswordHasher

from .settings import CONFIG, DATA, HOME


def initialize(username, password):
    if not password:
        raise ValueError("Password cannot be empty")
    CONFIG.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    template_root = Path(__file__).parent / "defaults"
    for name in ("config.ini", "club.ini"):
        dest = CONFIG / name
        if not dest.exists():
            shutil.copyfile(template_root / name, dest)

    dest = CONFIG / "secrets.ini"
    if dest.exists():
        raise RuntimeError(f"{dest} already exists; refusing to overwrite credentials")

    cfg = ConfigParser(interpolation=None)
    cfg["auth"] = {
        "username": username,
        "password_hash": PasswordHasher().hash(password),
        "session_key": secrets.token_urlsafe(48),
    }
    cfg["llm"] = {"api_key": ""}
    with dest.open("w", encoding="utf-8") as f:
        cfg.write(f)
    try:
        dest.chmod(0o600)
    except OSError:
        pass

    print(f"Initialized {HOME}; keep config/secrets.ini private")


def list_nights():
    """One line per night (all clubs) with its server stamp, for choosing what to stamp."""
    from .database import connect, init_db

    init_db()
    with connect() as db:
        rows = db.execute(
            """SELECT s.id,s.club_id,s.title,s.created_at,s.origin,s.ended_at,
                      s.server_version,s.server_commit,
                      (SELECT count(*) FROM hands h WHERE h.session_id=s.id) AS hands
               FROM sessions s ORDER BY s.id"""
        ).fetchall()
    lines = ["id  club  created (UTC)        origin    hands  closed  title  [stamp]"]
    for r in rows:
        stamp = (f"v{r['server_version']}, commit {r['server_commit']}"
                 if r["server_version"] or r["server_commit"] else "not stamped")
        lines.append(f"{r['id']:<3} {r['club_id']:<5} {r['created_at']:<20} {r['origin']:<9} "
                     f"{r['hands']:<6} {'yes' if r['ended_at'] else 'no':<7} {r['title']}  [{stamp}]")
    return "\n".join(lines) if rows else "No nights found"


def stamp_night(night_id, version, commit, force=False):
    """Fill server_version/server_commit on one existing night; refuses to overwrite unless forced."""
    from .database import connect, init_db

    version = version.strip().removeprefix("v"); commit = commit.strip()[:7]
    if not version or not commit:
        raise ValueError("Version and commit cannot be empty")
    init_db()
    with connect() as db:
        night = db.execute(
            """SELECT s.id,s.title,s.created_at,s.origin,s.server_version,s.server_commit,
                      (SELECT count(*) FROM hands h WHERE h.session_id=s.id) AS hands
               FROM sessions s WHERE s.id=?""", (night_id,)
        ).fetchone()
        if night is None:
            raise SystemExit(f"Night {night_id} not found")
        described = (f"night {night['id']} '{night['title']}' ({night['origin']}, created "
                     f"{night['created_at']} UTC, {night['hands']} hands)")
        if (night["server_version"] or night["server_commit"]) and not force:
            raise SystemExit(
                f"{described} is already stamped v{night['server_version']}, commit "
                f"{night['server_commit']}; use --force to overwrite"
            )
        db.execute("UPDATE sessions SET server_version=?,server_commit=? WHERE id=?",
                   (version, commit, night_id))
    return f"Stamped {described} as v{version}, commit {commit}"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init")
    init.add_argument("--username", default="admin")
    init.add_argument(
        "--password",
        help="Avoid passing passwords on shared systems; prompts by default",
    )
    sub.add_parser("set-password")

    stamp = sub.add_parser(
        "stamp-night",
        help="Record the server version/commit for a night created before this was stored",
    )
    stamp.add_argument("--list", action="store_true", help="List nights and their stamps, then exit")
    stamp.add_argument("--night", type=int, help="Night id (from /sessions/<id>)")
    stamp.add_argument("--version", help="Server version, e.g. 1.0.0")
    stamp.add_argument("--commit", help="Short commit hash, e.g. 338dfb5")
    stamp.add_argument("--force", action="store_true", help="Overwrite an existing stamp")

    run = sub.add_parser("serve")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.action == "init":
        print(
            f"Server administrator username: {args.username} (change with --username)"
        )
        initialize(
            args.username,
            args.password
            if args.password is not None
            else getpass(f"Password for server admin {args.username!r}: "),
        )
    elif args.action == "set-password":
        cfg = ConfigParser(interpolation=None)
        path = CONFIG / "secrets.ini"
        if not cfg.read(path, encoding="utf-8"):
            raise RuntimeError("Run init first")

        password = getpass("New password: ")
        if not password:
            raise ValueError("Password cannot be empty")

        cfg["auth"]["password_hash"] = PasswordHasher().hash(password)
        with path.open("w", encoding="utf-8") as f:
            cfg.write(f)

        print(
            "Password updated. Restart the server to clear existing sessions if needed."
        )
    elif args.action == "stamp-night":
        if args.list:
            print(list_nights())
        elif args.night is None or not args.version or not args.commit:
            stamp.error("--night, --version and --commit are required (or use --list)")
        else:
            print(stamp_night(args.night, args.version, args.commit, args.force))
    else:
        import uvicorn

        uvicorn.run("whistapp.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
