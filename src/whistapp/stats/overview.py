"""Overblik: the club's per-member totals table (the original statistics)."""

from collections import defaultdict
from fractions import Fraction

from .base import PARTNERLESS, Cell, Column, Statistic, Table, ratio


def member_rows(data):
    """Per-member totals for everything in `data`, keyed by member id."""
    rows = {}

    def entry(mid, name):
        if mid not in rows:
            rows[mid] = dict(
                name=data.member_name(mid, name), club_id=mid, nights=set(), hands=0, bids=0,
                partner=0, won=0, lost=0, bid_won=0, bid_lost=0, eligible=0, self_partner=0,
                points=Fraction(), amount=0, fejl40=0,
            )
        return rows[mid]

    def row(pid):
        p = data.players[pid]
        return entry(p.member_id, p.name)

    def involved(h):
        return {p for p in h.participants
                if p in data.players and data.players[p].session_id == h.session_id}

    # Only actual hand participation counts as attendance.
    active_sessions = defaultdict(set)
    for h in data.hands:
        members = involved(h)
        for pid in members:
            r = row(pid)
            r["nights"].add(h.session_id)
            r["hands"] += 1
            r["points"] += h.scores.get(pid, Fraction())
        active_sessions[h.session_id].update(members)

        declarer, partner = h.declarer, h.partner
        normal = h.bid_key not in PARTNERLESS
        for pid in members:
            r = row(pid)
            if pid == declarer:
                r["bids"] += 1
                if normal:
                    r["eligible"] += 1
                    if partner == declarer:
                        r["self_partner"] += 1
                if h.won is not None:
                    r["bid_won" if h.won else "bid_lost"] += 1
            if normal and partner is not None and partner != declarer and pid == partner:
                r["partner"] += 1
            # For special bids, per-player outcomes are recorded separately.
            if h.won is not None and normal:
                declaring = pid == declarer or (partner != declarer and pid == partner)
                success = bool(h.won) if declaring else not bool(h.won)
                r["won" if success else "lost"] += 1

    # Final-only historical nights: count attendance and points, never invented hands/bids.
    for night in data.nights.values():
        for pid, score in night.final_scores.items():
            if pid in data.players:
                r = row(pid)
                r["nights"].add(night.id)
                r["points"] += score

    # Special bids may have multiple independent bidders and outcomes.
    for h in data.hands:
        for pid, _actual, won in h.specials:
            if pid not in data.players or pid not in h.participants:
                continue
            r = row(pid)
            # A special bid is a personal attempt, distinct from the declarer field.
            if pid != h.declarer:
                r["bids"] += 1
            if won is not None:
                r["bid_won" if won else "bid_lost"] += 1

    # Special hands have outcomes for all four players; a zero transfer is neutral.
    for h in data.hands:
        if h.bid_key not in PARTNERLESS:
            continue
        for pid in involved(h):
            delta = h.scores.get(pid, Fraction())
            if delta:
                row(pid)["won" if delta > 0 else "lost"] += 1

    for sid, pids in active_sessions.items():
        penalties = data.nights[sid].penalties
        for pid in pids:
            r = row(pid)
            r["amount"] += sum(amount for p, amount in penalties if p == pid)
            r["fejl40"] += sum(1 for p, _ in penalties if p == pid)

    # Payments include absent members (who have no player row for that night).
    for night in data.nights.values():
        for mid, amount in night.settlements.items():
            entry(mid, night.member_names.get(mid, ""))["amount"] += amount
    return rows


def sorted_rows(rows):
    return sorted(rows.values(), key=lambda r: r["name"].casefold())


COLUMNS = [
    Column("name", "Spiller", "Klubmedlemmet, samlet på tværs af spilaftener.", "text"),
    Column("nights", "Aftener", "Antal spilaftener med mindst én registreret hånd."),
    Column("hands", "Hænder", "Antal hænder spilleren deltog i."),
    Column("bids", "Bud", "Antal gange spilleren selv meldte."),
    Column("partner", "Makker", "Antal gange spilleren var makker, når meldingen tillader makker."),
    Column("won", "Vundet", "Antal vundne hænder for spilleren."),
    Column("lost", "Tabt", "Antal tabte hænder for spilleren."),
    Column("bid_balance", "Bud +/-", "Vundne minus tabte meldinger, hvor spilleren selv meldte.", "signed"),
    Column("points", "Point +/-", "Summen af de registrerede pointændringer.", "points"),
    Column("amount", "Beløb (kr.)", "Beregnet klubbeløb, ikke faktiske betalinger.", "points"),
    Column("fejl40", "Fejl40", "Antal registrerede Fejl40-betalinger."),
    Column("eligible", "Mulige selvmakker",
           "Egne meldinger, hvor selvmakker er mulig; Sol, Ren Sol, Bordlægger og Påskrig tæller ikke med."),
    Column("self_partner", "Selvmakker", "Antal gange spilleren meldte og selv blev makker."),
    Column("self_pct", "Selvmakker %", "Selvmakker divideret med mulige selvmakker, i procent.", "pct"),
]


class Overview(Statistic):
    key = "overblik"
    title = "Klubmedlemmer"
    topic = "overblik"
    description = "Kun klubspilaftener med registrerede hænder. Beløb er beregnede resultater, ikke registrerede betalinger."

    def compute(self, data):
        rows = []
        for r in sorted_rows(member_rows(data)):
            rows.append(dict(
                r, nights=len(r["nights"]), bid_balance=r["bid_won"] - r["bid_lost"],
                name=Cell(r["name"], r["name"].casefold()),
                self_pct=ratio(r["self_partner"], r["eligible"]),
            ))
        return Table(COLUMNS, rows, empty="Ingen klubhænder registreret i perioden.")
