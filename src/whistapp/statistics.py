"""Club-only statistics derived from recorded hands, never from current bid rules."""

from collections import defaultdict
from fractions import Fraction

from .club import active_club_id

PARTNERLESS = {"sol", "ren_sol", "bordlaegger", "paskrig"}


def club_statistics(db, roster, session_id=None, origin="all"):
    if origin not in ("all", "live", "imported"):
        raise ValueError("Invalid origin")
    club_id = active_club_id()

    # Rows are grouped by member id; club_players() yields string ids, players.member_id is int.
    names = {str(k): v for k, v in roster}
    players = db.execute(
        """
        SELECT p.id, p.member_id AS club_id, p.name, p.session_id, s.created_at, s.title
        FROM players p JOIN sessions s ON s.id=p.session_id
        WHERE s.is_club=1 AND p.member_id IS NOT NULL AND s.club_id=?
          AND (? IS NULL OR s.id=?)
          AND (?='all' OR s.origin=?)
    """,
        (club_id, session_id, session_id, origin, origin),
    ).fetchall()

    by_id = {p["id"]: p for p in players}
    session_years = {}
    sessions = defaultdict(set)
    for p in players:
        sessions[p["session_id"]].add(p["id"])
        session_years[p["session_id"]] = str(p["created_at"])[:4]

    hands = db.execute(
        """
        SELECT h.*, d.player_id AS declarer_id
        FROM hands h JOIN sessions s ON s.id=h.session_id
        LEFT JOIN hand_declarers d ON d.hand_id=h.id
        WHERE s.is_club=1 AND s.club_id=? AND (? IS NULL OR s.id=?)
          AND (?='all' OR s.origin=?) ORDER BY h.id
    """,
        (club_id, session_id, session_id, origin, origin),
    ).fetchall()

    participants = defaultdict(set)
    for r in db.execute("SELECT hand_id,player_id FROM hand_participants"):
        participants[r["hand_id"]].add(r["player_id"])

    scores = defaultdict(dict)
    for r in db.execute(
        "SELECT hand_id,player_id,numerator,denominator FROM hand_scores"
    ):
        scores[r["hand_id"]][r["player_id"]] = Fraction(
            r["numerator"], r["denominator"]
        )

    penalties = defaultdict(int)
    for r in db.execute(
        """SELECT pp.session_id,pp.player_id,pp.amount FROM penalty_payments pp
        JOIN sessions s ON s.id=pp.session_id WHERE s.collect_club_money=1 AND s.club_id=? AND (?='all' OR s.origin=?)""",
        (club_id, origin, origin),
    ):
        penalties[(r["session_id"], r["player_id"])] += r["amount"]

    settlements = defaultdict(Fraction)
    for r in db.execute(
        """SELECT session_id,member_id,amount_numerator,amount_denominator
        FROM club_member_settlements cms JOIN sessions s ON s.id=cms.session_id
        WHERE s.club_id=? AND (?='all' OR s.origin=?)""",
        (club_id, origin, origin),
    ):
        settlements[(r["session_id"], r["member_id"])] += Fraction(
            r["amount_numerator"], r["amount_denominator"]
        )

    for r in db.execute(
        """SELECT ss.session_id,p.member_id,ss.placement_amount
        FROM session_settlements ss JOIN players p ON p.id=ss.player_id JOIN sessions s ON s.id=ss.session_id
        WHERE p.member_id IS NOT NULL AND s.club_id=? AND (?='all' OR s.origin=?)""",
        (club_id, origin, origin),
    ):
        settlements[(r["session_id"], r["member_id"])] += Fraction(
            r["placement_amount"]
        )

    def empty(p):
        return dict(
            name=names.get(str(p["club_id"]), p["name"]),
            club_id=p["club_id"],
            nights=set(),
            hands=0,
            bids=0,
            partner=0,
            won=0,
            lost=0,
            bid_won=0,
            bid_lost=0,
            eligible=0,
            self_partner=0,
            points=Fraction(),
            amount=0,
            fejl40=0,
        )

    sections = defaultdict(dict)

    def entry(year, p):
        section = sections[year]
        if p["club_id"] not in section:
            section[p["club_id"]] = empty(p)
        return section[p["club_id"]]

    # Only actual hand participation counts as attendance.
    active_sessions = defaultdict(set)
    for h in hands:
        year = session_years.get(h["session_id"])
        if year is None:
            continue

        involved = participants[h["id"]] & sessions[h["session_id"]]
        for pid in involved:
            p = by_id[pid]
            for period in ("all", year):
                row = entry(period, p)
                row["nights"].add(h["session_id"])
                row["hands"] += 1
                row["points"] += scores[h["id"]].get(pid, Fraction())

        active_sessions[h["session_id"]].update(involved)
        declarer = h["declarer_id"]
        partner = h["partner_id"]
        normal = h["bid_key"] not in PARTNERLESS

        for pid in involved:
            p = by_id[pid]
            for period in ("all", year):
                row = entry(period, p)
                if pid == declarer:
                    row["bids"] += 1
                    if normal:
                        row["eligible"] += 1
                        if partner == declarer:
                            row["self_partner"] += 1
                    if h["won"] is not None:
                        row["bid_won" if h["won"] else "bid_lost"] += 1

                if (
                    normal
                    and partner is not None
                    and partner != declarer
                    and pid == partner
                ):
                    row["partner"] += 1

                # For special bids, per-player outcomes are recorded separately.
                if h["won"] is not None and normal:
                    on_declaring_side = pid == declarer or (
                        partner != declarer and pid == partner
                    )
                    success = (
                        bool(h["won"]) if on_declaring_side else not bool(h["won"])
                    )
                    row["won" if success else "lost"] += 1

    # Final-only historical nights: count attendance and points, never invented hands/bids.
    for r in db.execute(
        """SELECT fs.session_id,fs.player_id,fs.numerator,fs.denominator
        FROM historical_final_scores fs JOIN sessions s ON s.id=fs.session_id
        WHERE s.is_club=1 AND (? IS NULL OR s.id=?) AND (?='all' OR s.origin=?)""",
        (session_id, session_id, origin, origin),
    ):
        p = by_id.get(r["player_id"])
        if p is None:
            continue

        for period in ("all", session_years[r["session_id"]]):
            row = entry(period, p)
            row["nights"].add(r["session_id"])
            row["points"] += Fraction(r["numerator"], r["denominator"])

    # Special bids may have multiple independent bidders and outcomes.
    special = db.execute(
        """SELECT sb.*,h.session_id,h.bid_key,s.created_at
        FROM special_bids sb JOIN hands h ON h.id=sb.hand_id
        JOIN sessions s ON s.id=h.session_id WHERE s.is_club=1 AND (? IS NULL OR s.id=?)
        AND (?='all' OR s.origin=?)""",
        (session_id, session_id, origin, origin),
    ).fetchall()
    for r in special:
        p = by_id.get(r["player_id"])
        if p is None or r["player_id"] not in participants[r["hand_id"]]:
            continue

        year = session_years[r["session_id"]]
        for period in ("all", year):
            row = entry(period, p)
            # A special bid is a personal attempt, distinct from the declarer field.
            if r["player_id"] != next(
                (h["declarer_id"] for h in hands if h["id"] == r["hand_id"]), None
            ):
                row["bids"] += 1
            if r["won"] is not None:
                row["bid_won" if r["won"] else "bid_lost"] += 1

    # Special hands have outcomes for all four players, not just bidders.
    # A zero transfer is neutral (neither won nor lost).
    for h in hands:
        if h["bid_key"] not in PARTNERLESS:
            continue
        year = session_years.get(h["session_id"])
        if year is None:
            continue
        for pid in participants[h["id"]] & sessions[h["session_id"]]:
            delta = scores[h["id"]].get(pid, Fraction())
            if not delta:
                continue

            for period in ("all", year):
                entry(period, by_id[pid])["won" if delta > 0 else "lost"] += 1

    for sid, pids in active_sessions.items():
        session_year = session_years[sid]
        for pid in pids:
            for period in ("all", session_year):
                row = entry(period, by_id[pid])
                row["amount"] += penalties[(sid, pid)]
                row["fejl40"] += db.execute(
                    """SELECT COUNT(*) FROM penalty_payments pp JOIN sessions s ON s.id=pp.session_id
                    WHERE pp.session_id=? AND pp.player_id=? AND s.collect_club_money=1""",
                    (sid, pid),
                ).fetchone()[0]

    # Include payments for absent members (who have no player row for that night).
    for (sid, mid), amount in settlements.items():
        if session_id is not None and sid != session_id:
            continue

        night = db.execute(
            "SELECT is_club,created_at FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        member = db.execute(
            "SELECT name FROM club_members WHERE id=?", (mid,)
        ).fetchone()
        if not night or not night["is_club"] or not member:
            continue

        year = str(night["created_at"])[:4]
        synthetic = {"club_id": mid, "name": member["name"]}
        for period in ("all", year):
            entry(period, synthetic)["amount"] += amount

    output = []
    for year in sorted((x for x in sections if x != "all"), reverse=True):
        output.append((year, sections[year]))
    if "all" in sections:
        output.insert(0, ("Alle år", sections["all"]))

    return [
        dict(
            title=title,
            night_count=len(set().union(*(r["nights"] for r in rows.values())))
            if rows
            else 0,
            players=[
                dict(
                    **{
                        k: (
                            len(v)
                            if k == "nights"
                            else (
                                f"{float(v):.2f}".rstrip("0")
                                .rstrip(".")
                                .replace(".", ",")
                                if k in ("points", "amount")
                                else v
                            )
                        )
                        for k, v in row.items()
                    },
                    self_pct=(
                        f"{100 * row['self_partner'] / row['eligible']:.1f}".replace(
                            ".", ","
                        )
                        + " %"
                    )
                    if row["eligible"]
                    else "—",
                    bid_balance=row["bid_won"] - row["bid_lost"],
                )
                for row in sorted(rows.values(), key=lambda r: r["name"].casefold())
            ],
        )
        for title, rows in output
    ]
