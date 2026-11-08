"""Remove the per-night rule copies that rule versions replaced.

Before rule versions, each night copied the club's rules into its own row in
`sessions`. Every night now reads its rules from its rule version, and nothing reads or
writes these columns. Removing them leaves one source of truth for a night's rules.
"""

NAME = "drop-night-rule-columns"
DESCRIPTION = ("Remove sessions.placement_rates, fejl40_rate, money_mode, point_rate and bid_settings; "
               "every night reads its rules from its rule version.")
COLUMNS = ("placement_rates", "fejl40_rate", "money_mode", "point_rate", "bid_settings")


def _present(db):
    existing = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
    return [c for c in COLUMNS if c in existing]


def needed(db):
    return bool(_present(db))


def blockers(db):
    """Every night must point to a rule version, or dropping the columns would lose its rules."""
    missing = [r[0] for r in db.execute("SELECT id FROM sessions WHERE rule_version_id IS NULL ORDER BY id")]
    if missing:
        return [f"nights without a rule version: {', '.join(map(str, missing))}"]
    return []


def run(db):
    columns = _present(db)
    for column in columns:
        db.execute(f"ALTER TABLE sessions DROP COLUMN {column}")
    return "removed sessions." + ", ".join(columns)
