"""The club's rule versions for every member: the history, one version, and a night's rules."""

import json
from datetime import datetime
from fractions import Fraction

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..club import active_club_id
from ..database import connect
from ..finance import MONEY_MODES
from ..rule_versions import club_versions, night_version
from ..rules import bid_classes, bid_type_order
from ..web import fmt, get_session, local_time, render

router = APIRouter()

MONTHS = ("januar", "februar", "marts", "april", "maj", "juni", "juli",
          "august", "september", "oktober", "november", "december")


def danish_date(stamp):
    """'12. marts 2026' from a stored UTC timestamp, in Danish time."""
    shown = local_time(stamp)
    try:
        day = datetime.strptime(str(shown)[:10], "%Y-%m-%d")
    except ValueError:
        return str(shown or "")
    return f"{day.day}. {MONTHS[day.month - 1]} {day.year}"


def version_name(version):
    return f"Version {version['number']} · fra {danish_date(version['created_at'])}"


def _bid_label(bid):
    return "VIP" if bid.key == "vip" else bid.label


def _bids(version):
    """Each bid type in the version's order: whether it is played and its values."""
    settings = json.loads(version["bid_settings"] or "{}")
    classes = {bid.key: bid for bid in bid_classes()}
    rows = []
    for key in bid_type_order(settings):
        bid, chosen = classes[key], settings.get(key, {})
        values = bid.option_values(chosen.get("options"))
        rows.append(dict(key=key, label=_bid_label(bid), enabled=chosen.get("enabled", True),
                         options=[(label, fmt(values[name])) for name, (label, _d) in bid.options.items()]))
    return rows


def _names(db, club_id):
    return {r["id"]: r for r in db.execute("SELECT id,name,status FROM club_members WHERE club_id=?", (club_id,))}


def describe(db, version):
    """A version as the rules page shows it. Members carry their current names."""
    members = _names(db, version["club_id"])
    placements = json.loads(version["placements"])
    return dict(
        id=version["id"], number=version["number"], name=version_name(version),
        created_at=local_time(version["created_at"]), created_by=version["created_by"],
        money_mode=version["money_mode"], money_mode_label=MONEY_MODES.get(version["money_mode"], version["money_mode"]),
        point_rate=fmt(Fraction(version["point_rate"] or "1")), fejl40=version["fejl40"],
        placements=list(enumerate(placements, 1)), bids=_bids(version),
        members=sorted((dict(id=m, name=members[m]["name"] if m in members else f"Medlem #{m}",
                             status=members[m]["status"] if m in members else None)
                        for m in json.loads(version["member_ids"])), key=lambda m: m["name"].casefold()),
    )


def changes(db, before, after):
    """What a version changed from the one before it, in words."""
    if before is None:
        return ["Første version"]
    out = []
    old, new = json.loads(before["placements"]), json.loads(after["placements"])
    if old != new:
        out.append(f"Pladsbetaling {', '.join(map(str, old))} → {', '.join(map(str, new))} kr")
    if before["fejl40"] != after["fejl40"]:
        out.append(f"Fejl40 {before['fejl40']} → {after['fejl40']} kr")
    if before["money_mode"] != after["money_mode"]:
        out.append(f"Betalingsform: {MONEY_MODES.get(after['money_mode'], after['money_mode'])}")
    if Fraction(before["point_rate"] or "1") != Fraction(after["point_rate"] or "1"):
        out.append(f"Kr. pr. point {fmt(Fraction(before['point_rate'] or '1'))} → {fmt(Fraction(after['point_rate'] or '1'))}")
    old_bids = {b["key"]: b for b in _bids(before)}
    new_bids = _bids(after)
    for bid in new_bids:
        was = old_bids[bid["key"]]
        if was["enabled"] != bid["enabled"]:
            out.append(f"{bid['label']} slået {'til' if bid['enabled'] else 'fra'}")
        for (label, value), (_l, old_value) in zip(bid["options"], was["options"]):
            if value != old_value:
                out.append(f"{bid['label']}: {label} {old_value} → {value}")
    if [b["key"] for b in new_bids] != [b["key"] for b in _bids(before)]:
        out.append("Meldingernes rækkefølge ændret")
    names = _names(db, after["club_id"])
    old_members, new_members = set(json.loads(before["member_ids"])), set(json.loads(after["member_ids"]))

    def name(mid):
        return names[mid]["name"] if mid in names else f"Medlem #{mid}"
    out += [f"{name(m)} kom på medlemslisten" for m in sorted(new_members - old_members, key=name)]
    out += [f"{name(m)} gik af medlemslisten" for m in sorted(old_members - new_members, key=name)]
    return out or ["Ingen ændringer i regler eller medlemsliste"]


@router.get("/rules", response_class=HTMLResponse)
def rule_history(request: Request):
    with connect() as db:
        versions = club_versions(db, active_club_id())
        nights = {r[0]: r[1] for r in db.execute(
            "SELECT rule_version_id,COUNT(*) FROM sessions WHERE club_id=? GROUP BY rule_version_id", (active_club_id(),))}
        history = [dict(number=v["number"], name=version_name(v), created_by=v["created_by"],
                        nights=nights.get(v["id"], 0),
                        changes=changes(db, versions[i + 1] if i + 1 < len(versions) else None, v))
                   for i, v in enumerate(versions)]
    return render(request, "rule_versions.html", history=history)


@router.get("/rules/{number}", response_class=HTMLResponse)
def rule_version(request: Request, number: int):
    with connect() as db:
        version = db.execute("SELECT * FROM club_rule_versions WHERE club_id=? AND number=?",
                             (active_club_id(), number)).fetchone()
        if version is None:
            raise HTTPException(404, "Ukendt regelversion")
        latest = club_versions(db, active_club_id())[0]["number"]
        view = describe(db, version)
    return render(request, "rule_version.html", version=view, session=None, latest=latest)


@router.get("/sessions/{sid}/rules", response_class=HTMLResponse)
def night_rules_page(request: Request, sid: int):
    with connect() as db:
        session = get_session(db, sid)
        version = night_version(db, sid)
        latest = club_versions(db, session["club_id"])[0]["number"]
        view = describe(db, version)
    return render(request, "rule_version.html", version=view, session=session, latest=latest)
