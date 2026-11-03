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
    else:
        import uvicorn

        uvicorn.run("whistapp.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
