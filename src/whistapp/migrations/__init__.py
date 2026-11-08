"""One-off data migrations, run by hand with `whistapp migrate`.

`init_db()` only creates what is missing (tables, columns, the first club's first rule
version on a brand-new database). Anything that rewrites or removes existing data lives
here instead: no route, no game logic and not `init_db()` imports this package. Each
migration says whether it is needed, refuses to run while its preconditions are not met,
backs the database up next to itself first, and runs in one transaction.

A migration module has NAME, DESCRIPTION, `needed(db) -> bool`, `blockers(db) -> list[str]`
and `run(db) -> str`.
"""

import sqlite3
from datetime import datetime

from ..database import connect, db_path
from . import drop_night_rule_columns

MIGRATIONS = [drop_night_rule_columns]


def status():
    """One line per migration: needed, blocked or done."""
    lines = []
    with connect() as db:
        for m in MIGRATIONS:
            if not m.needed(db):
                state = "done"
            elif problems := m.blockers(db):
                state = "BLOCKED: " + "; ".join(problems)
            else:
                state = "needed"
            lines.append(f"{m.NAME}: {state}\n    {m.DESCRIPTION}")
    return "\n".join(lines)


def _backup(name):
    """A full copy of the database next to it, made with SQLite's online backup."""
    source = db_path()
    target = source.with_name(f"{source.stem}-before-{name}-{datetime.now():%Y%m%d-%H%M%S}{source.suffix}")
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def run(name):
    migration = next((m for m in MIGRATIONS if m.NAME == name), None)
    if migration is None:
        raise SystemExit(f"Unknown migration {name!r}; see `whistapp migrate`")
    with connect() as db:
        if not migration.needed(db):
            return f"{name}: already done, nothing changed"
        if problems := migration.blockers(db):
            raise SystemExit(f"{name}: not run, fix this first:\n  " + "\n  ".join(problems))
    backup = _backup(name)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = migration.run(db)
    return f"{name}: {result}\nBackup from before: {backup}"
