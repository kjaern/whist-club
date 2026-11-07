"""Import handwritten-score transcriptions. Original score deltas remain authoritative.

No statistics are manufactured for missing bids or unknown trick counts.
"""

import hashlib
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from io import BytesIO
from itertools import combinations, product
from pathlib import Path

from openpyxl import load_workbook

from .bids.registry import get_bid
from .buildinfo import build_stamp
from .club import active_club_id
from .rule_versions import stamp_night
from .rules import load_rules, rules_snapshot, target_for

MAX_BYTES = 2_000_000
MAX_WORKSHEETS = 20
MAX_ROWS = 1000
MAX_COLUMNS = 100
MAX_VIEW_ROWS = 600
MAX_VIEW_COLUMNS = 60
SPECIAL = {"sol", "ren_sol", "bordlaegger"}
BID_NAMES = {
    "almindelige": "almindelige",
    "alm": "almindelige",
    "halve": "halve",
    "sans": "sans",
    "gode": "gode",
    "sol": "sol",
    "ren sol": "ren_sol",
    "ren_sol": "ren_sol",
    "bordlægger": "bordlaegger",
    "bordlaegger": "bordlaegger",
    "paskrig": "paskrig",
    "vip1": "vip1",
    "vip2": "vip2",
    "vip3": "vip3",
}


def infer_date_from_filename(filename):
    """Only accept unambiguous ISO-style dates in the uploaded filename.

    Returns (ISO date, precision) or None. Never infer a play date from upload time.
    """
    stem = Path(str(filename).replace("\\", "/")).stem
    matches = re.findall(
        r"(?<![0-9])([12][0-9]{3})[-_.]([01][0-9])[-_.]([0-3][0-9])(?![0-9])", stem
    )
    valid = []

    for y, m, d in matches:
        try:
            valid.append(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return (valid[0], "day") if len(set(valid)) == 1 and valid else None


def number(value):
    if value is None or str(value).strip() == "":
        return None
    return Fraction(str(value).strip().removesuffix("?").strip().replace(",", "."))


def decimal_text(value):
    """Keep money readable as decimals while retaining exact values internally."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        try:
            value = format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            return str(value)
    text = str(value).strip()
    uncertain = text.endswith("?")
    core = text[:-1].strip() if uncertain else text
    if "/" in core:
        try:
            fraction = Fraction(core)
            denominator = fraction.denominator
            for factor in (2, 5):
                while denominator % factor == 0:
                    denominator //= factor
            if denominator == 1:
                core = format(
                    Decimal(fraction.numerator) / Decimal(fraction.denominator), "f"
                )
        except (ValueError, ZeroDivisionError, InvalidOperation):
            pass
    else:
        try:
            numeric = Decimal(core.replace(",", "."))
            if numeric.is_finite():
                core = format(numeric.normalize(), "f")
        except InvalidOperation:
            pass
    if "." in core:
        core = core.rstrip("0").rstrip(".")
    return core + ("?" if uncertain else "")


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return decimal_text(value)
    return str(value).strip()


def workbook_sheets(content, max_bytes=MAX_BYTES):
    if len(content) > max_bytes:
        raise ValueError("Excel-filen er for stor (maks. 2 MB)")
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    if len(wb.sheetnames) > MAX_WORKSHEETS:
        raise ValueError("Maks. 20 faner pr. Excel-fil")
    result = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([_cell_text(value) for value in row[:MAX_COLUMNS]])
            if len(rows) >= MAX_ROWS:
                break
        result.append({"name": ws.title, "rows": rows})
    return result


def _header_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return (
        text.casefold()
        .replace("æ", "ae")
        .replace("ø", "oe")
        .replace("å", "aa")
        .replace("δ", "delta")
        .strip()
    )


def _field_name(header):
    key = _header_key(header)
    return {
        "haand": "number",
        "hand": "number",
        "melder": "bidder",
        "melding": "bid",
        "meldt": "target",
        "stik": "actual",
        "makker": "partner",
        "notation": "notation",
    }.get(key)


def _group_column(header, kind):
    original = str(header or "").strip()
    suffixes = {
        "delta": (
            " score changes",
            " score change",
            " score ændringer",
            " score ændring",
            " scoreændringer",
            " scoreændring",
            " score aendring",
            " ændringer",
            " ændring",
            " ændring i kr",
            " ændring kr",
            " aendring",
            " score",
            " delta",
            " Δ",
            " δ",
            " per hand",
        ),
        "total": (
            " running total",
            " løbende total",
            " loebende total",
            " løbende sum",
            " total",
            " saldo",
            " sum",
        ),
        "notation": (
            " bid notation",
            " notation",
            " meldinger",
            " melding",
            " bid",
        ),
    }[kind]
    for suffix in suffixes:
        if original.casefold().endswith(suffix.casefold()):
            player = original[: -len(suffix)].strip(" _:-Δδ")
            if player:
                return player
    return None


def _score_columns(headers):
    return [
        (index, _group_column(header, "delta"))
        for index, header in enumerate(headers)
        if _group_column(header, "delta")
    ]


def _expanded_headers(top, sub):
    result = []
    group = ""
    for index in range(max(len(top), len(sub))):
        top_value = _cell_text(top[index]) if index < len(top) else ""
        sub_value = _cell_text(sub[index]) if index < len(sub) else ""
        if top_value and _field_name(top_value):
            group = ""
            result.append(top_value)
        else:
            if top_value:
                group = top_value
            if sub_value:
                result.append(f"{group} {sub_value}".strip())
            else:
                result.append(group)
    return result


def _find_sheet_header(sheet):
    rows = sheet["rows"]
    for start in range(min(8, len(rows))):
        candidates = [(rows[start], 1)]
        if start + 1 < len(rows):
            candidates.append((_expanded_headers(rows[start], rows[start + 1]), 2))
        for headers, span in candidates:
            scores = _score_columns(headers)
            if len(scores) == 4 and len({name.casefold() for _, name in scores}) == 4:
                return start, span, headers, scores
    return None


def _source_preview(sheets, warnings=()):
    marks = {}
    for warning in warnings:
        if warning.get("column_index") is not None:
            marks.setdefault((warning["sheet"], warning["row"]), set()).add(
                warning["column_index"]
            )
    result = []
    for sheet in sheets:
        rows = sheet["rows"][:MAX_VIEW_ROWS]
        flags = []
        for row_index, row in enumerate(rows, 1):
            flagged = marks.get((sheet["name"], row_index), set())
            flags.append(
                [
                    ("uncertain" if "?" in str(value) else "")
                    + (" source-warning" if col in flagged else "")
                    for col, value in enumerate(row[:MAX_VIEW_COLUMNS])
                ]
            )
        result.append(
            {
                "name": sheet["name"],
                "rows": [row[:MAX_VIEW_COLUMNS] for row in rows],
                "flags": flags,
                "truncated": len(sheet["rows"]) > MAX_VIEW_ROWS
                or any(len(row) > MAX_VIEW_COLUMNS for row in sheet["rows"]),
            }
        )
    return result


def workbook_preview(content, max_bytes=10 * 1024 * 1024):
    """Return bounded HTML-view data for an uploaded or generated workbook."""
    return _source_preview(workbook_sheets(content, max_bytes=max_bytes))


def _parse_sheet(sheet, sheet_index, header_info, players, source_warnings):
    header_row, header_span, headers, score_cols = header_info
    field_cols = {}
    extra_cols = []
    note_cols = []
    for col, header in enumerate(headers):
        field = _field_name(header)
        if field:
            field_cols.setdefault(field, col)
        match = re.fullmatch(r"Melder\s*([23])", str(header), re.IGNORECASE)
        if match:
            extra_cols.append((int(match.group(1)), col))
        note_player = _group_column(header, "notation")
        if note_player:
            note_cols.append((note_player, col))

    if extra_cols:
        extra_numbers = [value for value, _col in sorted(extra_cols)]
        if extra_numbers != list(range(2, 2 + len(extra_numbers))):
            source_warnings.append(
                {
                    "sheet": sheet["name"],
                    "row": header_row + 1,
                    "column_index": None,
                    "message": "Ekstra melderkolonner skal hedde Melder 2, Melder 3 osv.",
                }
            )
        nonempty_headers = [i for i, value in enumerate(headers) if str(value).strip()]
        if nonempty_headers and max(col for _value, col in extra_cols) != max(
            nonempty_headers
        ):
            source_warnings.append(
                {
                    "sheet": sheet["name"],
                    "row": header_row + 1,
                    "column_index": None,
                    "message": "Melder 2/Melder 3 skal stå yderst til højre.",
                }
            )

    sheet_player_map = {name.casefold(): (col, name) for col, name in score_cols}
    hands = []
    running = {name: Fraction() for name in players}
    running_valid = {name: True for name in players}
    for row_offset, cells in enumerate(
        sheet["rows"][header_row + header_span :], header_row + header_span + 1
    ):
        if not any(str(value).strip() for value in cells):
            continue
        lead = _header_key(cells[0] if cells else "")
        row_labels = {_header_key(value) for value in cells if str(value).strip()}
        if lead in {"total", "sluttotal", "final total"} or row_labels.intersection(
            {"total", "sluttotal", "final total"}
        ):
            for player in players:
                total_col = next(
                    (
                        col
                        for col, header in enumerate(headers)
                        if _group_column(header, "total")
                        and _group_column(header, "total").casefold()
                        == player.casefold()
                    ),
                    None,
                )
                if (
                    total_col is None
                    or total_col >= len(cells)
                    or not running_valid[player]
                ):
                    continue
                total_value = decimal_text(cells[total_col])
                if not total_value or "?" in total_value:
                    continue
                try:
                    mismatch = number(total_value) != running[player]
                except (ValueError, TypeError, ZeroDivisionError):
                    mismatch = True
                if mismatch:
                    source_warnings.append(
                        {
                            "sheet": sheet["name"],
                            "row": row_offset,
                            "column_index": total_col,
                            "message": f"Sluttotal for {player} afviger fra summen af håndændringerne i scorearket.",
                        }
                    )
            continue
        if lead in {"haand", "hand", "kontrol"}:
            continue
        if len(hands) >= 500:
            raise ValueError("Maks. 500 hænder pr. import")

        hand_number = len(hands) + 1
        raw_number = (
            cells[field_cols["number"]]
            if "number" in field_cols and field_cols["number"] < len(cells)
            else ""
        )
        scores = {}
        notes = []
        for player in players:
            col_info = sheet_player_map.get(player.casefold())
            value = cells[col_info[0]] if col_info and col_info[0] < len(cells) else ""
            scores[player] = decimal_text(value)
            if not scores[player] or scores[player] == "?":
                source_warnings.append(
                    {
                        "sheet": sheet["name"],
                        "row": row_offset,
                        "column_index": col_info[0] if col_info else None,
                        "message": f"Hånd {hand_number}: score for {player} mangler eller er ulæselig i kilden.",
                    }
                )
            try:
                score = number(scores[player])
            except (ValueError, TypeError, ZeroDivisionError):
                score = None
            if score is not None:
                running[player] += score
                if scores[player].endswith("?"):
                    running_valid[player] = False
            else:
                running_valid[player] = False
                if scores[player] and scores[player] != "?":
                    source_warnings.append(
                        {
                            "sheet": sheet["name"],
                            "row": row_offset,
                            "column_index": col_info[0] if col_info else None,
                            "message": f"Hånd {hand_number}: score for {player} kan ikke læses som et decimalbeløb.",
                        }
                    )

            total_col = next(
                (
                    col
                    for col, header in enumerate(headers)
                    if _group_column(header, "total")
                    and _group_column(header, "total").casefold() == player.casefold()
                ),
                None,
            )
            if (
                total_col is not None
                and total_col < len(cells)
                and running_valid[player]
            ):
                total_value = decimal_text(cells[total_col])
                if total_value and "?" not in total_value:
                    try:
                        mismatch = number(total_value) != running[player]
                    except (ValueError, TypeError, ZeroDivisionError):
                        mismatch = True
                    if mismatch:
                        source_warnings.append(
                            {
                                "sheet": sheet["name"],
                                "row": row_offset,
                                "column_index": total_col,
                                "message": f"Hånd {hand_number}: løbende total for {player} afviger fra ændringerne i scorearket.",
                            }
                        )

        for player, col in note_cols:
            value = cells[col] if col < len(cells) else ""
            if value:
                notes.append(f"{player}: {value}")

        def get(field):
            col = field_cols.get(field)
            return (
                decimal_text(cells[col]) if col is not None and col < len(cells) else ""
            )

        if raw_number:
            try:
                if int(float(str(raw_number))) != hand_number:
                    source_warnings.append(
                        {
                            "sheet": sheet["name"],
                            "row": row_offset,
                            "column_index": field_cols.get("number"),
                            "message": f"Kildens håndnummer er {raw_number}; reviewrækkefølgen er {hand_number}.",
                        }
                    )
            except (TypeError, ValueError):
                source_warnings.append(
                    {
                        "sheet": sheet["name"],
                        "row": row_offset,
                        "column_index": field_cols.get("number"),
                        "message": "Kildens håndnummer kan ikke læses; rækkefølgen er bevaret.",
                    }
                )
        hands.append(
            {
                "number": hand_number,
                "source_id": f"{sheet_index}:{row_offset}",
                "source_sheet": sheet["name"],
                "source_row": row_offset,
                "bidder": get("bidder"),
                "bid": get("bid"),
                "target": get("target"),
                "actual": get("actual"),
                "partner": get("partner"),
                "extra": [
                    decimal_text(cells[col])
                    for _number, col in sorted(extra_cols)
                    if col < len(cells) and str(cells[col]).strip()
                ],
                "scores": scores,
                "source_notes": notes,
            }
        )
    return hands


def normalize_workbook(content, sheets=None):
    if len(content) > MAX_BYTES:
        raise ValueError("Excel-filen er for stor (maks. 2 MB)")
    sheets = sheets if sheets is not None else workbook_sheets(content)
    if not sheets:
        raise ValueError("Excel-filen indeholder ingen faner")

    warnings = []
    parsed = []
    for index, sheet in enumerate(sheets):
        header = _find_sheet_header(sheet)
        if header:
            parsed.append((index, sheet, header))
        else:
            warnings.append(
                {
                    "sheet": sheet["name"],
                    "row": 1,
                    "column_index": None,
                    "message": "Arket vises som kilde, men dets spiller-/scorekolonner kunne ikke genkendes automatisk.",
                }
            )
    if not parsed:
        raise ValueError(
            "Kunne ikke finde fire spilleres scoreændringskolonner. Se kildearket, og brug Hånd, spillernavn + Δ eller to rækker med spillernavne og Δ."
        )

    players = [name for _col, name in parsed[0][2][3]]
    player_keys = {name.casefold() for name in players}
    hands = []
    parsed_headers = parsed[0][2][2]
    parsed_fields = {_field_name(header) for header in parsed_headers}
    structured = (
        len(sheets) == 1
        and sheets[0]["name"].casefold() == "hænder"
        and any(str(header).strip().casefold() == "hånd" for header in parsed_headers)
        and {
            "number",
            "bidder",
            "bid",
            "target",
            "actual",
            "partner",
        }.issubset(parsed_fields)
    )
    if not structured:
        for _index, sheet, header in parsed:
            sheet_players = {name.casefold() for _col, name in header[3]}
            if sheet_players != player_keys:
                warnings.append(
                    {
                        "sheet": sheet["name"],
                        "row": header[0] + 1,
                        "column_index": None,
                        "message": "Arket har andre spillerkolonner end resten og skal kontrolleres manuelt.",
                    }
                )
                continue

    for index, sheet, header in parsed:
        sheet_players = {name.casefold() for _col, name in header[3]}
        if sheet_players != player_keys:
            continue
        hands.extend(_parse_sheet(sheet, index, header, players, warnings))
    if not hands:
        raise ValueError("Ingen håndrækker kunne findes i de genkendte ark")

    if not structured:
        warnings.append(
            {
                "sheet": "",
                "row": 0,
                "column_index": None,
                "message": "Rå transskriptionsark er normaliseret konservativt. Melding, melder, target og stik, der ikke stod i tydelige særskilte kolonner, skal udfyldes i review.",
            }
        )
    return {
        "players": players,
        "hands": hands,
        "sha256": hashlib.sha256(content).hexdigest(),
        "source_kind": "structured" if structured else "raw_transcription",
        "source_warnings": warnings,
        "source_sheets": _source_preview(sheets, warnings),
    }


def read_workbook(content):
    """Compatibility wrapper; parsing no longer aborts on correctable score errors."""
    data = normalize_workbook(content)
    data.pop("source_sheets", None)
    return data


def reconstruct(hand, players, rules):
    """Find all rule-consistent results from the recorded monetary deltas."""
    bid = BID_NAMES.get(hand["bid"].strip().casefold())
    if not bid:
        return []

    observed = {i: number(hand["scores"][name]) for i, name in enumerate(players)}
    bidder = hand["bidder"]
    if bid != "paskrig" and bidder not in players:
        return []

    rule = rules[bid]
    engine = get_bid(bid)
    solutions = []

    if bid in SPECIAL:
        names = [bidder] + hand["extra"]
        if (
            not names
            or len(names) > 4
            or len(set(names)) != len(names)
            or any(n not in players for n in names)
        ):
            return []
        ids = [players.index(n) for n in names]

        # Representative trick counts for each win/loss state; exact counts are
        # not recoverable, but each bidder's outcome is.
        for outcomes in product((True, False), repeat=len(ids)):
            actuals = {
                pid: (0 if won else int(rule["max_allowed_tricks"]) + 1)
                for pid, won in zip(ids, outcomes)
            }
            result = engine.score(
                rule, active=list(range(4)), declarer=ids[0], actuals=actuals
            )
            if all(Fraction(result.scores[i]) == observed[i] for i in range(4)):
                solutions.append(
                    {
                        "actual": None,
                        "partner": "",
                        "outcomes": dict(zip(names, outcomes)),
                    }
                )
    elif bid == "paskrig":
        # The highest-trick players are the losers. Enumerate loser subsets,
        # not fabricated individual trick counts.
        for count in range(1, 5):
            for losers in combinations(range(4), count):
                actuals = {i: (2 if i in losers else 1) for i in range(4)}
                result = engine.score(
                    rule, active=list(range(4)), declarer=None, actuals=actuals
                )
                if all(Fraction(result.scores[i]) == observed[i] for i in range(4)):
                    solutions.append(
                        {
                            "actual": None,
                            "partner": "",
                            "outcomes": {players[i]: i in losers for i in range(4)},
                        }
                    )
    else:
        try:
            target = target_for(rule, hand["target"])
        except (ValueError, TypeError, KeyError):
            return []

        partners = [hand["partner"]] if hand["partner"] else players
        for partner in partners:
            if partner not in players:
                continue
            for actual in range(14):
                result = engine.score(
                    rule,
                    active=list(range(4)),
                    declarer=players.index(bidder),
                    partner=players.index(partner),
                    target=target,
                    actual=actual,
                    **({"level": int(bid[-1])} if bid.startswith("vip") else {}),
                )
                if all(
                    Fraction(str(result.scores[i])) == observed[i] for i in range(4)
                ):
                    solutions.append(
                        {"actual": actual, "partner": partner, "outcomes": {}}
                    )
    return solutions


def validate(data):
    """Reconstruct from scores, not from guessed actual tricks."""
    players = data["players"]
    issues = []
    rules = load_rules()["bids"]

    for hand in data["hands"]:
        problems = []
        scores = hand.get("scores") if isinstance(hand.get("scores"), dict) else {}
        numeric_scores = []
        for name in players:
            if name not in scores:
                problems.append(f"Mangler score for {name}")
                continue
            if "/" in str(scores[name]).removesuffix("?"):
                problems.append(f"Score for {name} skal skrives som decimal, ikke brøk")
                continue
            try:
                score = number(scores[name])
            except (ValueError, TypeError, ZeroDivisionError):
                score = None
            if score is None:
                problems.append(f"Ugyldig eller manglende score for {name}")
            else:
                numeric_scores.append(score)
        if len(numeric_scores) == len(players) and sum(numeric_scores, Fraction()):
            problems.append("Scoreændringer summerer ikke til nul")

        bid = BID_NAMES.get(hand["bid"].strip().casefold())

        if not bid:
            problems.append(
                "Ukendt melding"
                if hand["bid"].strip().casefold() not in ("", "ukendt")
                else "Melding mangler"
            )
        elif bid != "paskrig" and hand["bidder"] not in players:
            problems.append("Melder mangler eller er ukendt")
        if (
            len(set(hand["extra"])) != len(hand["extra"])
            or hand["bidder"] in hand["extra"]
            or any(n not in players for n in hand["extra"])
        ):
            problems.append("Ugyldige ekstra meldere")
        if bid in SPECIAL or bid == "paskrig":
            if hand["target"] or hand["actual"] or hand["partner"]:
                problems.append(
                    "Meldt, Stik og Makker skal være tomme for denne melding"
                )
            if bid == "paskrig" and hand["extra"]:
                problems.append("Paskrig har ingen ekstra meldere")
        elif bid and hand["extra"]:
            problems.append("Ekstra meldere er kun tilladt for SOL-lignende meldinger")

        if bid and not problems:
            solutions = reconstruct(hand, players, rules)
            if not solutions:
                problems.append("Melding stemmer ikke med scoreændringer")
            else:
                # Numeric trick counts can be inferred for ordinary bids.
                if bid not in SPECIAL and bid != "paskrig":
                    if hand["actual"]:
                        try:
                            solutions = [
                                s
                                for s in solutions
                                if s["actual"] == int(hand["actual"])
                            ]
                        except ValueError:
                            solutions = []
                        if not solutions:
                            problems.append(
                                "Stik stemmer ikke med scoreændringer (ret eller ryd Stik)"
                            )
                    if (
                        not problems
                        and len({(s["actual"], s["partner"]) for s in solutions}) > 1
                    ):
                        problems.append(
                            "Flere mulige resultater — angiv Stik og Makker"
                        )
                    elif not problems:
                        hand["actual"] = str(solutions[0]["actual"])
                        hand["partner"] = solutions[0]["partner"]
                elif len({tuple(sorted(s["outcomes"].items())) for s in solutions}) > 1:
                    problems.append("Flere mulige udfald ud fra scoreændringer")
                else:
                    hand["inferred_outcomes"] = solutions[0]["outcomes"]

        issues.append(problems)

    return issues


def field_issues(hand, players, problems):
    """Map validation feedback to editable cells without conflating source notes and errors."""
    fields = {
        "bidder": False,
        "bid": False,
        "target": False,
        "actual": False,
        "partner": False,
        "extra": False,
        "scores": [],
    }
    scores = hand.get("scores", {})
    for name in players:
        if "/" in str(scores.get(name, "")).removesuffix("?"):
            fields["scores"].append(name)
            continue
        try:
            value = number(scores.get(name))
        except (ValueError, TypeError, ZeroDivisionError):
            value = None
        if value is None:
            fields["scores"].append(name)

    for problem in problems:
        text = problem.casefold()
        if "scoreændringer summerer" in text:
            fields["scores"] = list(players)
        if "melder" in text:
            fields["bidder"] = True
        if "melding" in text:
            fields["bid"] = True
            if "meldt" in text or "target" in text:
                fields["target"] = True
            if "stik" in text:
                fields["actual"] = True
            if "makker" in text:
                fields["partner"] = True
            if "stemmer ikke med scoreændringer" in text:
                fields["target"] = True
                fields["scores"] = list(players)
        if "meldt" in text:
            fields["target"] = True
        if "stik" in text:
            fields["actual"] = True
        if "makker" in text:
            fields["partner"] = True
        if "ekstra melder" in text:
            fields["extra"] = True

    return fields


def save_import(db, data, title, created_at, member_mapping, replace_id=None):
    """All-or-nothing import; caller owns the transaction."""
    issues = validate(data)
    if any(issues):
        raise ValueError("Ret fejlene før import")

    if replace_id is not None:
        old = db.execute(
            "SELECT origin FROM sessions WHERE id=? AND club_id=?", (replace_id, active_club_id())
        ).fetchone()
        if not old or old["origin"] != "imported":
            raise ValueError("Kun importerede aftener kan erstattes")

        # Keep session ID, but delete dependent data only after validation.
        sid = replace_id
        db.execute("DELETE FROM historical_final_scores WHERE session_id=?", (sid,))
        db.execute("DELETE FROM hands WHERE session_id=?", (sid,))
        db.execute("DELETE FROM players WHERE session_id=?", (sid,))
        db.execute("DELETE FROM club_member_settlements WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_settlements WHERE session_id=?", (sid,))
        db.execute("DELETE FROM penalty_payments WHERE session_id=?", (sid,))
        db.execute(
            "UPDATE sessions SET title=?,created_at=?,ended_at=?,is_club=?,collect_club_money=0 WHERE id=?",
            (title, created_at, created_at, int(all(member_mapping.values())), sid),
        )
    else:
        sid = db.execute(
            """INSERT INTO sessions(title,created_at,ended_at,origin,is_club,collect_club_money,club_id,
             server_version,server_commit) VALUES (?,?,?,'imported',?,0,?,?,?)""",
            (title, created_at, created_at, int(all(member_mapping.values())), active_club_id(),
             *build_stamp()),
        ).lastrowid
        stamp_night(db, sid)

    ids = {}
    for i, name in enumerate(data["players"]):
        ids[name] = db.execute(
            "INSERT INTO players(session_id,name,seat_order,member_id) VALUES (?,?,?,?)",
            (sid, name, i, member_mapping.get(name)),
        ).lastrowid

    rules = load_rules()["bids"]
    for hand in data["hands"]:
        bid = BID_NAMES.get(hand["bid"].strip().casefold(), "historical_unknown")
        rule = (
            rules[bid]
            if bid != "historical_unknown"
            else {"label": "Ukendt", "target": "none"}
        )
        target = target_for(rule, hand["target"])
        actual = (
            int(hand["actual"])
            if hand["actual"] and bid not in SPECIAL and bid != "paskrig"
            else None
        )
        bidder = ids.get(hand["bidder"])
        partner = ids.get(hand["partner"]) if bid not in SPECIAL else None
        won = None
        points = None

        if bid not in SPECIAL and bid not in ("paskrig", "historical_unknown"):
            result = get_bid(bid).score(
                rule,
                active=list(ids.values()),
                declarer=bidder,
                partner=partner,
                target=target,
                actual=actual,
                **({"level": int(bid[-1])} if bid.startswith("vip") else {}),
            )
            won = int(result.won)
            points = float(result.contract_points)

        hid = db.execute(
            """INSERT INTO hands(session_id,bid_key,bid_label,target,actual,won,contract_points,rules_snapshot,partner_id)
                VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                sid,
                bid,
                rule["label"],
                target,
                actual,
                won,
                points,
                rules_snapshot(rule),
                partner,
            ),
        ).lastrowid

        if bidder is not None:
            db.execute(
                "INSERT INTO hand_declarers(hand_id,player_id) VALUES (?,?)",
                (hid, bidder),
            )
        db.executemany(
            "INSERT INTO hand_participants(hand_id,player_id) VALUES (?,?)",
            [(hid, p) for p in ids.values()],
        )

        if bid in SPECIAL:
            db.executemany(
                "INSERT INTO special_bids(hand_id,player_id,actual,won) VALUES (?,?,NULL,?)",
                [
                    (hid, ids[name], int(hand["inferred_outcomes"][name]))
                    for name in [hand["bidder"]] + hand["extra"]
                ],
            )
        db.executemany(
            "INSERT INTO hand_scores(hand_id,player_id,numerator,denominator) VALUES (?,?,?,?)",
            [
                (hid, ids[name], number(score).numerator, number(score).denominator)
                for name, score in hand["scores"].items()
            ],
        )

    return sid
