"""Club administration: members, aliases, money and bid rules, AI import and LLM settings."""

import json
import re

from fractions import Fraction

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import is_server_admin
from ..club import active_club_id, club_settings
from ..database import connect
from ..finance import MONEY_MODES
from ..rules import bid_classes, bid_type_order
from ..web import can_manage_club, fmt, render

router = APIRouter()


@router.get("/club", response_class=HTMLResponse)
def club_admin(request: Request):
    with connect() as db:
        members = db.execute("SELECT * FROM club_members WHERE club_id=? ORDER BY id", (active_club_id(),)).fetchall()
        settings = club_settings(db)
        member_aliases = _club_alias_values(db, members)
        account_context = _club_page_context(request, db)
    return render(
        request,
        "club.html",
        members=members,
        **account_context,
        settings=settings,
        active_count=sum(m["status"] == "active" for m in members),
        rates=", ".join(str(x) for x in json.loads(settings["placements"])),
        form_values={},
        new_members=[],
        saved=request.query_params.get("saved") == "1",
        member_aliases=member_aliases,
        alias_form_values={},
    )


# Fields club.html needs on every render, including error re-renders (form keeps typed values).
def _club_page_context(request, db, form=None):
    member_logins = {r["member_id"]: dict(r) for r in db.execute(
        "SELECT ma.member_id,u.username,u.must_change_password FROM member_accounts ma "
        "JOIN user_accounts u ON u.id=ma.user_id JOIN club_members cm ON cm.id=ma.member_id "
        "WHERE cm.club_id=?", (active_club_id(),)
    )}
    # Linking an existing login is server-admin only; a login has at most one member per club.
    unlinked_accounts = db.execute("""SELECT u.id,u.username FROM user_accounts u
        WHERE NOT EXISTS (SELECT 1 FROM member_accounts ma JOIN club_members cm ON cm.id=ma.member_id
            WHERE ma.user_id=u.id AND cm.club_id=?)
        ORDER BY u.username""", (active_club_id(),)).fetchall() if is_server_admin(request) else []
    return {
        "member_logins": member_logins,
        "can_reset_invites": can_manage_club(request),
        "server_admin": is_server_admin(request),
        "unlinked_accounts": unlinked_accounts,
        "club_name": str(form.get("club_name")) if form is not None and form.get("club_name") is not None
        else db.execute("SELECT name FROM clubs WHERE id=?", (active_club_id(),)).fetchone()[0],
        **_club_rules_context(club_settings(db), form),
    }


def _bid_order(saved, form=None):
    """The bid types in the club's order: as posted on the club form (unknown and repeated
    keys ignored, missing ones appended), else as saved."""
    order = bid_type_order(saved)
    if form is None or not form.get("bid_settings_form"):
        return order
    posted = []
    for key in form.getlist("bid_order"):
        if key in order and key not in posted:
            posted.append(key)
    return posted + [key for key in order if key not in posted]


def _club_rules_context(settings, form=None):
    """Money mode and per-bid on/off and values for the club page, in the club's bid order."""
    saved = json.loads(settings["bid_settings"] or "{}")
    rows = []
    classes = {bid.key: bid for bid in bid_classes()}
    for bid in (classes[key] for key in _bid_order(saved, form)):
        chosen = saved.get(bid.key, {})
        values = bid.option_values(chosen.get("options"))
        enabled = chosen.get("enabled", True)
        if form is not None and form.get("bid_settings_form"):
            enabled = bool(form.get(f"bid_enabled_{bid.key}"))
        rows.append({
            "key": bid.key,
            "label": "VIP" if bid.key == "vip" else bid.label,
            "enabled": enabled,
            "options": [
                {"field": f"bid_{bid.key}_{name}", "label": label,
                 "value": str(form.get(f"bid_{bid.key}_{name}")) if form is not None
                 and form.get(f"bid_{bid.key}_{name}") is not None else fmt(values[name])}
                for name, (label, _default) in bid.options.items()
            ],
        })
    return {
        "bid_rows": rows,
        "money_modes": MONEY_MODES,
        "money_mode": str(form.get("money_mode")) if form is not None and form.get("money_mode") else settings["money_mode"],
        "point_rate": str(form.get("point_rate")) if form is not None and form.get("point_rate") is not None
        else fmt(Fraction(settings["point_rate"])),
    }


def _club_name_from_form(db, form):
    """The new club name, or None when the form has no name field; names are unique ignoring case."""
    if form.get("club_name") is None:
        return None
    name = " ".join(str(form.get("club_name")).split())
    if not 1 <= len(name) <= 80:
        raise ValueError("Klubnavnet skal være 1–80 tegn.")
    if db.execute("SELECT 1 FROM clubs WHERE name=? COLLATE NOCASE AND id<>?",
                  (name, active_club_id())).fetchone():
        raise ValueError(f"Der findes allerede en klub med navnet {name}.")
    return name


def _club_amount(raw, label):
    # Accept Danish decimal commas; store exact fractions such as "3/2".
    text = str(raw).strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,2})?", text):
        raise ValueError(f"{label} skal være et tal fra 0 til 9999 med højst to decimaler.")
    return Fraction(text)


def _parse_club_rules(form, settings):
    """Money mode, kr. per point and bid settings from the club form; absent fields keep their values."""
    money_mode = str(form.get("money_mode") or settings["money_mode"])
    if money_mode not in MONEY_MODES:
        raise ValueError("Ukendt betalingsform.")
    point_rate = settings["point_rate"]
    if form.get("point_rate") is not None:
        point_rate = str(_club_amount(form.get("point_rate"), "Kr. pr. point"))
    bid_settings = json.loads(settings["bid_settings"] or "{}")
    if form.get("bid_settings_form"):
        order = {key: i for i, key in enumerate(_bid_order(bid_settings, form))}
        bid_settings = {}
        for bid in bid_classes():
            bid_settings[bid.key] = {
                "order": order[bid.key],
                "enabled": bool(form.get(f"bid_enabled_{bid.key}")),
                "options": {
                    name: str(_club_amount(form.get(f"bid_{bid.key}_{name}", fmt(Fraction(str(default)))),
                                           f"{'VIP' if bid.key == 'vip' else bid.label}: {label}"))
                    for name, (label, default) in bid.options.items()
                },
            }
        if not any(entry["enabled"] for entry in bid_settings.values()):
            raise ValueError("Mindst én melding skal være slået til.")
    return money_mode, point_rate, json.dumps(bid_settings, ensure_ascii=False)


def _club_alias_values(db, members):
    aliases = {member["id"]: [] for member in members}
    for row in db.execute(
        "SELECT member_id,alias FROM club_member_aliases WHERE club_id=? ORDER BY alias COLLATE NOCASE",
        (active_club_id(),),
    ):
        aliases.setdefault(row["member_id"], []).append(row["alias"])
    return {member_id: ", ".join(values) for member_id, values in aliases.items()}


def _prepare_club_alias_rows(members, updates, new_names, form, current_aliases):
    canonical_names = {name.strip().casefold() for name in new_names}
    canonical_names.update(
        name.strip().casefold() for name, _status, _member_id in updates
    )
    own_names = {member_id: name.strip().casefold() for name, _status, member_id in updates}
    seen = set()
    rows = []
    form_values = {}

    for member in members:
        member_id = member["id"]
        field = f"aliases_{member_id}"
        raw = str(form.get(field, current_aliases.get(member_id, "")))
        form_values[field] = raw
        aliases = [" ".join(value.split()) for value in raw.split(",")]
        aliases = [alias for alias in aliases if alias]
        if len(aliases) > 30:
            raise ValueError(f"Højst 30 aliaser pr. medlem ({member['name']}).")

        for alias in aliases:
            if len(alias) > 80 or any(ord(char) < 32 for char in alias):
                raise ValueError(
                    f"Hvert alias skal være på højst 80 tegn ({member['name']})."
                )
            key = alias.casefold()
            # The member's own name already matches; older imports stored it as an alias too.
            if key == own_names.get(member_id):
                continue
            if key in canonical_names:
                raise ValueError(f"Aliaset '{alias}' er allerede et medlemsnavn.")
            if key in seen:
                raise ValueError(f"Aliaset '{alias}' skal være unikt mellem medlemmer.")
            seen.add(key)
            rows.append((member_id, alias))

    return rows, form_values


@router.post("/club/members/{member_id}/role")
def change_member_role(request: Request, member_id: int, is_club_admin: int = Form(...)):
    if not can_manage_club(request):
        raise HTTPException(403)
    if is_club_admin not in (0, 1):
        raise HTTPException(400)
    with connect() as db:
        member = db.execute("SELECT id,is_club_admin FROM club_members WHERE id=?", (member_id,)).fetchone()
        if not member:
            raise HTTPException(404)
        if not is_club_admin and member["is_club_admin"]:
            count = db.execute("SELECT COUNT(*) FROM club_members WHERE is_club_admin=1 AND status='active' AND club_id=?", (active_club_id(),)).fetchone()[0]
            if count <= 1 and not is_server_admin(request):
                raise HTTPException(409, "Sidste klubadministrator kan ikke fjernes")
        db.execute("UPDATE club_members SET is_club_admin=? WHERE id=?", (is_club_admin, member_id))
    return RedirectResponse("/club", status_code=303)


@router.post("/club/new-member", response_class=HTMLResponse)
async def new_club_member_row(request: Request):
    form = await request.form()
    index = len(form.getlist("new_member")) + 1
    return HTMLResponse(
        f"<label>Nyt medlem {index} "
        f'<input name="new_member" maxlength="80" placeholder="Navn" required></label>'
    )


@router.post("/club/save", response_class=HTMLResponse)
async def save_club(request: Request):
    form = await request.form()
    with connect() as db:
        members = db.execute("SELECT * FROM club_members WHERE club_id=? ORDER BY id", (active_club_id(),)).fetchall()
        settings = club_settings(db)
        current_aliases = _club_alias_values(db, members)
        alias_form_values = {
            f"aliases_{member['id']}": str(
                form.get(
                    f"aliases_{member['id']}",
                    current_aliases.get(member["id"], ""),
                )
            )
            for member in members
        }

        try:
            updates = []
            active_count = 0
            for member in members:
                mid = member["id"]
                name = str(form.get(f"name_{mid}", "")).strip()
                status = str(form.get(f"status_{mid}", ""))
                if not name or len(name) > 80 or status not in ("active", "paused"):
                    raise ValueError(f"Ugyldigt navn eller status for medlem #{mid}.")
                updates.append((name, status, mid))
                active_count += status == "active"

            new_names = [str(value).strip() for value in form.getlist("new_member")]
            if any(not name or len(name) > 80 for name in new_names):
                raise ValueError(
                    "Alle nye medlemmer skal have et navn på højst 80 tegn."
                )
            active_count += len(new_names)
            alias_rows, alias_form_values = _prepare_club_alias_rows(
                members, updates, new_names, form, current_aliases
            )

            club_name = _club_name_from_form(db, form)
            money_mode, point_rate, bid_settings = _parse_club_rules(form, settings)
            raw_rates = str(form.get("placements", ""))
            try:
                rates = [int(v.strip()) for v in raw_rates.split(",")]
            except ValueError:
                raise ValueError(
                    "Pladsbetalinger skal være hele tal adskilt af komma."
                ) from None
            # Placement rates must match the roster only when they are the money mode in use.
            if money_mode == "placement" and len(rates) != active_count:
                raise ValueError(
                    f"Der er {active_count} aktive medlemmer, men {len(rates)} pladsbetalinger. "
                    "Antallet skal være ens."
                )
            if any(rate < 0 for rate in rates):
                raise ValueError("Pladsbetalinger må ikke være negative.")
            try:
                penalty = int(str(form.get("fejl40", "")))
            except ValueError:
                raise ValueError("Fejl40 skal være et helt tal.") from None
            if penalty < 0:
                raise ValueError("Fejl40 må ikke være negativ.")

            # Validate every field before writing anything; commit all changes together.
            db.executemany(
                "UPDATE club_members SET name=?,status=? WHERE id=?", updates
            )
            db.executemany(
                "INSERT INTO club_members(name,status,club_id) VALUES (?,?,?)",
                [(name, "active", active_club_id()) for name in new_names],
            )
            db.execute(
                """UPDATE club_settings SET placements=?,fejl40=?,money_mode=?,point_rate=?,
                bid_settings=? WHERE id=?""",
                (json.dumps(rates), penalty, money_mode, point_rate, bid_settings, active_club_id()),
            )
            if club_name is not None:
                db.execute("UPDATE clubs SET name=? WHERE id=?", (club_name, active_club_id()))
            db.execute("DELETE FROM club_member_aliases WHERE club_id=?", (active_club_id(),))
            db.executemany(
                "INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,?,?)",
                [(member_id, active_club_id(), alias) for member_id, alias in alias_rows],
            )
        except ValueError as exc:
            return render(
                request,
                "club.html",
                members=members,
                **_club_page_context(request, db, form),
                settings=settings,
                active_count=sum(
                    str(form.get(f"status_{m['id']}", m["status"])) == "active"
                    for m in members
                )
                + len(form.getlist("new_member")),
                rates=", ".join(str(x) for x in json.loads(settings["placements"])),
                error=str(exc),
                form_values=dict(form),
                new_members=form.getlist("new_member"),
                member_aliases=_club_alias_values(db, members),
                alias_form_values=alias_form_values,
            )

    return RedirectResponse("/club?saved=1", status_code=303)
