"""Versioned club rules: the values a night is played under, plus its medlemsliste.

A version holds a club's rule values (placement payments, Fejl40, money mode, point
rate, bid settings) and the ids of the members ranked on a club night. Versions are
never changed. A club has one active version, its newest. Only saving Klubadministration
makes a new one (and founding a club makes its first); creating or importing a night
never does. Member names and short bid names are not part of a version, so renames
carry over to every night. Each night points to its version through
`sessions.rule_version_id`.
"""

import json

# A closed live night can be reopened, or deleted, for this long. After that it is final:
# its results and settlement never change, so the club's money can rely on them. It lives here,
# not in web.py, so the kasse can read it without loading the web layer.
REOPEN_HOURS = 12

RULE_FIELDS = ("placements", "fejl40", "money_mode", "point_rate", "bid_settings")


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


def active_member_ids(db, club_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM club_members WHERE club_id=? AND status='active' ORDER BY id", (club_id,))]


def latest_version(db, club_id):
    """The club's active version: its newest."""
    return db.execute(
        "SELECT * FROM club_rule_versions WHERE club_id=? ORDER BY number DESC LIMIT 1", (club_id,)
    ).fetchone()


def get_version(db, version_id):
    return db.execute("SELECT * FROM club_rule_versions WHERE id=?", (version_id,)).fetchone()


def club_versions(db, club_id):
    """The club's versions, newest first."""
    return db.execute("SELECT * FROM club_rule_versions WHERE club_id=? ORDER BY number DESC", (club_id,)).fetchall()


def save_version(db, club_id, created_by=None):
    """Make the club's settings and active members its active version, if they differ from it.

    Called only where a club administrator sets the rules: saving Klubadministration and
    founding a club. created_by defaults to the logged-in user. Returns the active version's id.
    """
    settings = db.execute("SELECT * FROM club_settings WHERE id=?", (club_id,)).fetchone()
    members = active_member_ids(db, club_id)
    latest = latest_version(db, club_id)
    if latest is not None and _key(latest, json.loads(latest["member_ids"])) == _key(settings, members):
        return latest["id"]
    number = (latest["number"] if latest else 0) + 1
    return db.execute(
        f"""INSERT INTO club_rule_versions(club_id,number,created_by,{",".join(RULE_FIELDS)},member_ids)
        VALUES (?,?,COALESCE(?,audit_username()),?,?,?,?,?,?)""",
        (club_id, number, created_by, settings["placements"], int(settings["fejl40"]),
         settings["money_mode"] or "placement", str(settings["point_rate"] or "1"),
         settings["bid_settings"] or "{}", json.dumps(sorted(members))),
    ).lastrowid


def stamp_night(db, sid, version_id=None):
    """Point a night at a version of its own club, by default the club's active one."""
    club_id = db.execute("SELECT club_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    if version_id is None:
        latest = latest_version(db, club_id)
        if latest is None:
            raise ValueError("Klubben har ingen regler endnu; gem Klubadministration først")
        version_id = latest["id"]
    elif not db.execute("SELECT 1 FROM club_rule_versions WHERE id=? AND club_id=?", (version_id, club_id)).fetchone():
        raise ValueError("Ukendt regelversion")
    db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (version_id, sid))


def night_version(db, sid):
    """The version a night is played under. Every night has one; a night without is an error."""
    row = db.execute("""SELECT v.* FROM sessions s JOIN club_rule_versions v ON v.id=s.rule_version_id
        WHERE s.id=?""", (sid,)).fetchone()
    if row is None:
        raise ValueError(f"Spilaften {sid} har ingen regelversion")
    return row


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
