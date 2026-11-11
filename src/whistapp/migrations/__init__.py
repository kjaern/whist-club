"""One-off data migrations, run by hand with `whistapp migrate`.

`init_db()` only creates what is missing (tables, columns, the first club's first rule
version on a brand-new database). Anything that rewrites or removes existing data lives
here instead: no route, no game logic and not `init_db()` imports this package. Each
migration says whether it is needed, refuses to run while its preconditions are not met,
backs the database up first (a "Før migrering" backup in the backup folder, see `backups.py`),
and runs in one transaction; if the backup cannot be written, nothing is changed.

A migration module has NAME, DESCRIPTION, `needed(db) -> bool`, `blockers(db) -> list[str]`
and `run(db) -> str`.
"""

from .. import backups
from ..database import connect
from . import backfill_dealers, drop_night_rule_columns, fix_backfilled_dealers, kasse_post_history

MIGRATIONS = [drop_night_rule_columns, kasse_post_history, backfill_dealers, fix_backfilled_dealers]


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


def run(name):
    migration = next((m for m in MIGRATIONS if m.NAME == name), None)
    if migration is None:
        raise SystemExit(f"Unknown migration {name!r}; see `whistapp migrate`")
    with connect() as db:
        if not migration.needed(db):
            return f"{name}: already done, nothing changed"
        if problems := migration.blockers(db):
            raise SystemExit(f"{name}: not run, fix this first:\n  " + "\n  ".join(problems))
    try:
        backup = backups.before_migration()
    except backups.BackupError as exc:
        raise SystemExit(f"{name}: not run, the backup failed: {exc}") from exc
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = migration.run(db)
    return f"{name}: {result}\nBackup from before: {backup}"
