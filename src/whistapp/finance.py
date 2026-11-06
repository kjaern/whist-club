"""Club treasury and spreadsheet export. Amounts in whole DKK."""

import configparser
from fractions import Fraction
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from .settings import CONFIG


def settings():
    c = configparser.ConfigParser()
    c.read(CONFIG / "club.ini", encoding="utf-8")
    return c


def fee_amount():
    return settings().getint("finance", "fejl40", fallback=40)


def placement_amounts():
    raw = settings().get("finance", "placements", fallback="0,25,75,115,135,150")
    amounts = [int(x.strip()) for x in raw.split(",")]
    if any(x < 0 for x in amounts):
        raise ValueError("Negative placement payment")
    return amounts


def settlement_rows(db, sid, amounts):
    """Rank every active club member, including absentees with zero points."""
    members = db.execute(
        """SELECT id FROM club_members WHERE status='active'
        AND club_id=(SELECT club_id FROM sessions WHERE id=?) ORDER BY id""",
        (sid,),
    ).fetchall()
    if len(members) != len(amounts):
        raise ValueError(
            f"Club payment table supports {len(amounts)} active members, not {len(members)}"
        )
    if not members:
        raise ValueError("No active club members")

    totals = _member_totals(db, sid, [m["id"] for m in members])
    ranked = sorted(totals, key=lambda mid: (-totals[mid], mid))
    result = []
    i = 0

    while i < len(ranked):
        j = i + 1
        while j < len(ranked) and totals[ranked[j]] == totals[ranked[i]]:
            j += 1
        payment = sum((Fraction(a) for a in amounts[i:j]), Fraction()) / (j - i)
        result.extend(
            (mid, i + 1, payment.numerator, payment.denominator) for mid in ranked[i:j]
        )
        i = j
    return result


# Club-selectable ways to turn a night's result into money. Amounts are what each
# member pays to the club kasse; a negative amount is money the member receives.
MONEY_MODES = {
    "placement": "Pladsbetaling efter placering",
    "score_settle": "Spillerne afregner indbyrdes efter slutscore",
    "score_losers": "Spillere med minus betaler slutscoren til klubkassen",
    "score_shifted": "Alle betaler afstanden til vinderen til klubkassen",
}


def score_settlement_rows(db, sid, mode, point_rate):
    """Settle the members who played by their final score times kr. per point."""
    if mode not in MONEY_MODES or mode == "placement":
        raise ValueError("Ukendt betalingsform")
    members = [r[0] for r in db.execute(
        "SELECT DISTINCT member_id FROM players WHERE session_id=? AND member_id IS NOT NULL", (sid,))]
    if not members:
        raise ValueError("No club members played")
    totals = _member_totals(db, sid, members)
    rate = Fraction(str(point_rate))
    top = max(totals.values())
    ranked = sorted(totals, key=lambda mid: (-totals[mid], mid))
    result = []
    for mid in ranked:
        place = 1 + sum(1 for other in totals.values() if other > totals[mid])
        if mode == "score_settle":
            amount = -totals[mid] * rate
        elif mode == "score_losers":
            amount = max(Fraction(0), -totals[mid]) * rate
        else:
            amount = (top - totals[mid]) * rate
        result.append((mid, place, amount.numerator, amount.denominator))
    return result


def settlement_entries(db, sid, mode, rates, point_rate):
    if (mode or "placement") == "placement":
        return settlement_rows(db, sid, rates)
    return score_settlement_rows(db, sid, mode, point_rate)


def _member_totals(db, sid, member_ids):
    totals = {mid: Fraction(0) for mid in member_ids}
    for r in db.execute(
        """SELECT p.member_id,hs.numerator,hs.denominator
        FROM hand_scores hs JOIN players p ON p.id=hs.player_id
        JOIN hands h ON h.id=hs.hand_id WHERE h.session_id=?""",
        (sid,),
    ):
        if r["member_id"] in totals:
            totals[r["member_id"]] += Fraction(r["numerator"], r["denominator"])

    # Historical evenings containing final scores only have no hand_scores.
    for r in db.execute(
        """SELECT p.member_id,f.numerator,f.denominator
        FROM historical_final_scores f JOIN players p ON p.id=f.player_id
        WHERE f.session_id=?""",
        (sid,),
    ):
        if r["member_id"] in totals:
            totals[r["member_id"]] += Fraction(r["numerator"], r["denominator"])
    return totals


def export_workbook(db, data):
    """Export a single editable sheet accepted by historical_import.read_workbook.

    The original hand scores are retained; inferred values are never substituted
    for monetary records. Additional special bidders are placed at the far right.
    A night with more than four players gets a column pair per player (0 while
    sitting out); the importer only reads four-player sheets back.
    """
    wb = Workbook()
    sh = wb.active
    sh.title = "Spilaften"
    write_night_sheet(db, sh, data)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def write_night_sheet(db, sh, data):
    """Write one night's hands, score changes and running totals to a worksheet."""
    players = data["players"]
    if not players:
        raise ValueError("Spilaftenen har ingen spillere")

    names = {p["id"]: p["name"] for p in players}
    rows = data["rows"]
    extras = max((max(0, len(r["special_results"]) - 1) for r in rows), default=0)

    headers = ["Hånd", "Melder", "Melding", "Meldt", "Stik", "Makker"]
    for p in players:
        headers.extend([f"{p['name']} Δ", f"{p['name']} total"])
    headers.extend(f"Melder {n}" for n in range(2, extras + 2))

    sh.append(headers)
    totals = {p["id"]: Fraction(0) for p in players}
    display_bids = {
        "almindelige": "Almindelige",
        "halve": "Halve",
        "sans": "Sans",
        "gode": "Gode",
        "sol": "SOL",
        "ren_sol": "Ren sol",
        "bordlaegger": "Bordlægger",
        "paskrig": "Paskrig",
        "vip1": "VIP1",
        "vip2": "VIP2",
        "vip3": "VIP3",
    }
    special = {"sol", "ren_sol", "bordlaegger"}

    for number, r in enumerate(rows, 1):
        hand = r["hand"]
        key = hand["bid_key"]
        declarer = r["declarer"]
        special_ids = list(r["special_results"])

        if key in special:
            # hand_declarers is the authoritative primary bidder; other special
            # bidders have no stable ordering requirement.
            if declarer is None and special_ids:
                declarer = special_ids[0]
            additional = [names[i] for i in special_ids if i != declarer]
        else:
            additional = []

        vals = [
            number,
            names.get(declarer, ""),
            display_bids.get(key, hand["bid_label"]),
            hand["target"] if key not in special | {"paskrig"} else None,
            hand["actual"] if key not in special | {"paskrig"} else None,
            names.get(hand["partner_id"], "")
            if key not in special | {"paskrig"}
            else None,
        ]

        deltas = []
        for p in players:
            pid = p["id"]
            score = db.execute(
                "SELECT numerator, denominator FROM hand_scores WHERE hand_id=? AND player_id=?",
                (hand["id"], pid),
            ).fetchone()
            delta = Fraction(score[0], score[1]) if score else Fraction(0)
            deltas.append(delta)
            totals[pid] += delta
            vals.extend([float(delta), float(totals[pid])])

        vals.extend(additional + [None] * (extras - len(additional)))
        sh.append(vals)

    # Keep the single-sheet import layout intact. The summary has no hand number,
    # so the importer safely ignores it on a later round trip.
    # Nights imported with final scores only have no hand rows; their result is
    # still part of the total.
    for r in db.execute(
        "SELECT player_id,numerator,denominator FROM historical_final_scores WHERE session_id=?",
        (data["session"]["id"],),
    ):
        if r["player_id"] in totals:
            totals[r["player_id"]] += Fraction(r["numerator"], r["denominator"])
    summary = [None, "Total", "", "", "", ""]
    for player in players:
        summary.extend([None, float(totals[player["id"]])])
    summary.extend([None] * extras)
    sh.append(summary)
    total_row = sh.max_row

    # Plain, editable data: no filters, frozen panes, colors or fixed widths.
    # Keep a simple total row, which the importer ignores (no hand number).
    for cell in sh[1]:
        cell.font = Font(bold=True)
    for cell in sh[total_row]:
        cell.font = Font(bold=True)
    for row in sh.iter_rows(min_row=2, max_row=total_row):
        for cell in row:
            if isinstance(cell.value, (int, float)) and cell.column >= 7:
                cell.number_format = "0.##"


def export_year_workbook(db, year, nights):
    """All of a year's club nights: an overview sheet, then one sheet per night.

    `nights` is a list of (date text, view_data) in the order to show. The per-night
    sheets use the single-night layout; the workbook is for reading, not re-import.
    """
    wb = Workbook()
    overview = wb.active
    overview.title = "Oversigt"
    overview.append([f"Spilaftener {year}"])
    overview.append(["Dato", "Spilaften", "Hænder", "Ark"])
    for cell in [*overview[1], *overview[2]]:
        cell.font = Font(bold=True)
    used = {"oversigt"}
    for date_text, data in nights:
        base = f"{date_text} {data['session']['title']}".strip()
        for bad in "[]:*?/\\":
            base = base.replace(bad, "-")
        title, n = base[:31], 2
        while title.casefold() in used:
            suffix = f" ({n})"
            title, n = base[: 31 - len(suffix)] + suffix, n + 1
        used.add(title.casefold())
        write_night_sheet(db, wb.create_sheet(title), data)
        overview.append([date_text, data["session"]["title"], len(data["rows"]), title])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
