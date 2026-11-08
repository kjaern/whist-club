"""Versioned club rules: the values a night is played under, plus its medlemsliste.

A version holds a club's rule values (placement payments, Fejl40, money mode, point
rate, bid settings) and the ids of the members ranked on a club night. Versions are
never changed; changing a rule or the set of active members makes a new one. Member
names and other member fields are not part of a version, so renames carry over to
every night. Each night points to its version through `sessions.rule_version_id`.
"""

import json

RULE_FIELDS = ("placements", "fejl40", "money_mode", "point_rate", "bid_settings")
MIGRATION_ACTOR = "migrering"


def _rule_bid_settings(raw):
    """Bid settings without the short names, which are display text read from the club now.
    A bid left with no settings is the same as a bid with none saved."""
    stripped = {key: {k: v for k, v in entry.items() if k != "short"} if isinstance(entry, dict) else entry
                for key, entry in json.loads(raw or "{}").items()}
    return {key: entry for key, entry in stripped.items() if entry != {}}


def _key(values, member_ids):
    """Comparable form of a version; equal keys mean the same rules and medlemsliste."""
    return (
        tuple(json.loads(values["placements"])),
        int(values["fejl40"]),
        values["money_mode"] or "placement",
        str(values["point_rate"] or "1"),
        json.dumps(_rule_bid_settings(values["bid_settings"]), sort_keys=True),
        tuple(sorted(member_ids)),
    )


def _version_key(row):
    return _key(row, json.loads(row["member_ids"]))


def active_member_ids(db, club_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM club_members WHERE club_id=? AND status='active' ORDER BY id", (club_id,))]


def latest_version(db, club_id):
    return db.execute(
        "SELECT * FROM club_rule_versions WHERE club_id=? ORDER BY number DESC LIMIT 1", (club_id,)
    ).fetchone()


def get_version(db, version_id):
    return db.execute("SELECT * FROM club_rule_versions WHERE id=?", (version_id,)).fetchone()


def _insert(db, club_id, values, member_ids, created_by=None, created_at=None):
    """created_by defaults to the logged-in user making the request."""
    number = db.execute(
        "SELECT COALESCE(MAX(number),0)+1 FROM club_rule_versions WHERE club_id=?", (club_id,)
    ).fetchone()[0]
    return db.execute(
        f"""INSERT INTO club_rule_versions(club_id,number,created_by,created_at,{",".join(RULE_FIELDS)},member_ids)
        VALUES (?,?,COALESCE(?,audit_username()),COALESCE(?,CURRENT_TIMESTAMP),?,?,?,?,?,?)""",
        (club_id, number, created_by, created_at, values["placements"], int(values["fejl40"]),
         values["money_mode"] or "placement", str(values["point_rate"] or "1"),
         values["bid_settings"] or "{}", json.dumps(sorted(member_ids))),
    ).lastrowid


def current_version_id(db, club_id, created_by=None):
    """The club's version for its current settings and active members, made if they changed.

    Called whenever the club's rules or members are saved and whenever a night is created,
    so a night always points to the rules in force when it started.
    """
    settings = db.execute("SELECT * FROM club_settings WHERE id=?", (club_id,)).fetchone()
    if settings is None:
        return None
    members = active_member_ids(db, club_id)
    latest = latest_version(db, club_id)
    if latest is not None and _version_key(latest) == _key(settings, members):
        return latest["id"]
    return _insert(db, club_id, settings, members, created_by)


def stamp_night(db, sid, version_id=None):
    """Point a night at a version of its own club, by default the club's current one."""
    club_id = db.execute("SELECT club_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    if version_id is None:
        version_id = current_version_id(db, club_id)
    elif not db.execute("SELECT 1 FROM club_rule_versions WHERE id=? AND club_id=?", (version_id, club_id)).fetchone():
        raise ValueError("Ukendt regelversion")
    db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (version_id, sid))


def version_at(db, club_id, timestamp):
    """The club's version in force at a moment: the newest one made by then, else its first one.

    Versions made by the migration carry the time of the first night played under them.
    """
    current_version_id(db, club_id)
    row = db.execute("""SELECT id FROM club_rule_versions WHERE club_id=? AND created_at<=?
        ORDER BY number DESC LIMIT 1""", (club_id, timestamp)).fetchone() or db.execute(
        "SELECT id FROM club_rule_versions WHERE club_id=? ORDER BY number LIMIT 1", (club_id,)).fetchone()
    return row and row[0]


def club_versions(db, club_id):
    """The club's versions, newest first."""
    return db.execute("SELECT * FROM club_rule_versions WHERE club_id=? ORDER BY number DESC", (club_id,)).fetchall()


def _night_values(session, settings):
    """A night's snapshot values; anything it never recorded comes from today's settings."""
    return {
        "placements": session["placement_rates"] or settings["placements"],
        "fejl40": session["fejl40_rate"] if session["fejl40_rate"] is not None else settings["fejl40"],
        "money_mode": session["money_mode"] or settings["money_mode"],
        "point_rate": session["point_rate"] or settings["point_rate"],
        "bid_settings": session["bid_settings"] or settings["bid_settings"],
    }


def _settled_members(db, sid):
    return [r[0] for r in db.execute(
        "SELECT member_id FROM club_member_settlements WHERE session_id=? ORDER BY member_id", (sid,))]


def _settled_rosters(db, club_id):
    """(night id, medlemsliste) for the club's nights that were settled by placement."""
    nights = db.execute(
        "SELECT id FROM sessions WHERE club_id=? AND COALESCE(money_mode,'placement')='placement'", (club_id,))
    return [(sid, m) for (sid,) in nights.fetchall() if (m := _settled_members(db, sid))]


def _assign(db, session, known, rosters):
    """Give one night without a version the version its own snapshot describes."""
    club_id = session["club_id"]
    settings = db.execute("SELECT * FROM club_settings WHERE id=?", (club_id,)).fetchone()
    if settings is None:
        return None
    values = _night_values(session, settings)
    members = [] if values["money_mode"] != "placement" else _settled_members(db, session["id"])
    if not members:
        size = len(json.loads(values["placements"]))
        played = {r[0] for r in db.execute(
            "SELECT member_id FROM players WHERE session_id=? AND member_id IS NOT NULL", (session["id"],))}
        candidates = sorted(rosters, key=lambda c: abs(c[0] - session["id"]))
        members = next((m for _, m in candidates if len(m) == size and played <= set(m)), None)             or active_member_ids(db, club_id)
    key = _key(values, members)
    if key not in known:
        known[key] = _insert(db, club_id, values, members, MIGRATION_ACTOR, session["created_at"])
    db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (known[key], session["id"]))
    return known[key]


def _known_versions(db, club_id):
    known = {}
    for row in db.execute("SELECT * FROM club_rule_versions WHERE club_id=? ORDER BY number", (club_id,)):
        known.setdefault(_version_key(row), row["id"])
    return known


def migrate(db):
    """Give every night without a version one, then make sure each club has a current version.

    A night's rule values come from its own snapshot, filled from today's settings where it
    recorded nothing. Its medlemsliste is who its placement settlement ranked. A night
    without one borrows the medlemsliste of the nearest night in the club that has one,
    is the same size as its placement table and includes its member players; failing
    that, the club's active members today. Nights with the same values share a version,
    numbered in the order the nights were played. Running this again changes nothing.
    """
    sessions = db.execute(
        "SELECT * FROM sessions WHERE rule_version_id IS NULL ORDER BY created_at, id").fetchall()
    known, rosters = {}, {}
    for session in sessions:
        club_id = session["club_id"]
        if club_id not in known:
            known[club_id], rosters[club_id] = _known_versions(db, club_id), _settled_rosters(db, club_id)
        _assign(db, session, known[club_id], rosters[club_id])

    for (club_id,) in db.execute("SELECT id FROM club_settings").fetchall():
        current_version_id(db, club_id, MIGRATION_ACTOR)


def night_version(db, sid):
    """The version a night is played under. A night made without one gets it as migration would."""
    session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if session is None:
        raise ValueError("Ukendt spilaften")
    version_id = session["rule_version_id"] or _assign(
        db, session, _known_versions(db, session["club_id"]), _settled_rosters(db, session["club_id"]))
    return get_version(db, version_id)


def night_rules(db, sid):
    """A night's rules as values: placements, fejl40, money_mode, point_rate, bid_settings, member_ids."""
    v = night_version(db, sid)
    return dict(id=v["id"], number=v["number"], placements=json.loads(v["placements"]), fejl40=v["fejl40"],
                money_mode=v["money_mode"], point_rate=v["point_rate"],
                bid_settings=json.loads(v["bid_settings"] or "{}"), member_ids=json.loads(v["member_ids"]))


def is_club_night(db, sid):
    """Only members on the night's medlemsliste played; a guest or anyone else makes it a non-club night."""
    players = db.execute("SELECT member_id FROM players WHERE session_id=?", (sid,)).fetchall()
    on_list = set(night_rules(db, sid)["member_ids"])
    return bool(players) and all(p["member_id"] in on_list for p in players)
