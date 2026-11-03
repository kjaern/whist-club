"""Persistent club identities and initial import from club.ini."""

import json
from configparser import ConfigParser
from contextvars import ContextVar

from .settings import CONFIG

# The club a request works in. Outside a request (CLI, scripts) it is the original club.
_active_club = ContextVar("whist_active_club", default=1)


def active_club_id():
    return _active_club.get()


def active_club_set(club_id):
    return _active_club.set(club_id)


def active_club_reset(token):
    _active_club.reset(token)


def _initial_config():
    config = ConfigParser()
    config.read(CONFIG / "club.ini", encoding="utf-8")
    return config


def initial_club_name():
    return _initial_config().get("club", "name", fallback="").strip() or "Klubben"


def initial_roster():
    return (
        list(_initial_config().items("players"))
        if _initial_config().has_section("players")
        else []
    )


def initial_finance():
    config = _initial_config()
    return (
        json.dumps([50, 75, 125, 200, 250]),
        config.getint("finance", "fejl40", fallback=40),
    )


def club_players(db, active_only=False):
    sql = "SELECT id,name FROM club_members WHERE club_id=?"
    if active_only:
        sql += " AND status='active'"

    return [(str(r["id"]), r["name"]) for r in db.execute(sql + " ORDER BY id", (active_club_id(),))]


def club_settings(db, club_id=None):
    return db.execute(
        "SELECT * FROM club_settings WHERE id=?",
        (active_club_id() if club_id is None else club_id,),
    ).fetchone()
