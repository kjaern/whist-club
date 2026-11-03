"""Transcribe photographed score sheets with an OpenAI-compatible vision model."""

import base64
import json
import os
import re
from configparser import ConfigParser
from itertools import combinations
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from openpyxl import Workbook
from openpyxl.styles import PatternFill

from .historical_import import BID_NAMES, decimal_text, reconstruct, number
from .settings import CONFIG, DATA

PROMPT_PATH = Path(__file__).parent / "prompts" / "transcribe-score-sheet.md"
HISTORICAL_GUIDE_PATH = PROMPT_PATH.parent / "historical-import.md"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_WORKSHEETS = 20
MAX_ROWS = 1000
MAX_COLUMNS = 100
MAX_REMARKS = 100_000
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_DATE_PRECISIONS = {"minute", "day", "month", "year", "unknown"}
_YELLOW = PatternFill("solid", fgColor="FFF2A8")


def _int_setting(value, fallback):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback

    return parsed if parsed > 0 else fallback


def _saved_llm_config():
    saved = ConfigParser(interpolation=None)
    saved.read(DATA / "llm.ini", encoding="utf-8")
    return saved


def admin_llm_settings():
    config = ConfigParser(interpolation=None)
    config.read(CONFIG / "config.ini", encoding="utf-8")
    secrets = ConfigParser(interpolation=None)
    secrets.read(CONFIG / "secrets.ini", encoding="utf-8")
    saved = _saved_llm_config()

    def effective(key):
        return (
            os.environ.get(f"WHIST_LLM_{key.upper()}")
            or saved.get("llm", key, fallback="")
            or config.get("llm", key, fallback="")
        ).strip()

    disabled = saved.has_option("llm", "enabled") and not saved.getboolean(
        "llm", "enabled", fallback=True
    )
    api_key = (
        os.environ.get("WHIST_LLM_API_KEY")
        or saved.get("llm", "api_key", fallback="")
        or secrets.get("llm", "api_key", fallback="")
    )
    base_url = effective("base_url").rstrip("/")
    model = effective("model")
    return {
        "base_url": base_url,
        "model": model,
        "has_api_key": bool(api_key),
        "configured": not disabled and bool(base_url and model),
        "disabled": disabled,
        "can_remove": bool(base_url or model or api_key or saved.has_section("llm")),
    }


def _write_saved_llm_config(config):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "llm.ini"
    temporary = DATA / f".llm-{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as file:
            config.write(file)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def save_admin_llm_settings(base_url, model, api_key):
    base_url = str(base_url).strip().rstrip("/")
    model = str(model).strip()
    api_key = str(api_key).strip()
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Angiv en gyldig LLM-adresse med http:// eller https://")
    if not model or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ValueError("Angiv et gyldigt modelnavn på højst 200 tegn")
    if len(api_key) > 4096 or any(char in api_key for char in "\r\n"):
        raise ValueError("API-nøglen har et ugyldigt format")

    config = _saved_llm_config()
    previous_key = config.get("llm", "api_key", fallback="")
    if not config.has_section("llm"):
        config.add_section("llm")
    config.set("llm", "enabled", "true")
    config.set("llm", "base_url", base_url)
    config.set("llm", "model", model)
    config.set("llm", "api_key", api_key or previous_key)
    _write_saved_llm_config(config)


def remove_admin_llm_settings():
    # Removing the provider keeps the request limits the administrator chose.
    saved = _saved_llm_config()
    config = ConfigParser(interpolation=None)
    config["llm"] = {"enabled": "false", **{
        key: saved.get("llm", key) for key in LIMIT_DEFAULTS if saved.has_option("llm", key)}}
    _write_saved_llm_config(config)


# Request limits: what a single AI import may send, wait for and get back. The output cap is
# what bounds the cost of the answer; the timeout only bounds how long the app waits.
LIMIT_DEFAULTS = {"timeout": 180, "max_images": 10, "max_total_bytes": 20_000_000, "max_tokens": 32_000}
LIMIT_RANGES = {"timeout": (10, 900), "max_images": (1, 30), "max_total_bytes": (1_000_000, 70_000_000),
                "max_tokens": (1_000, 200_000)}
LIMIT_LABELS = {"timeout": "Ventetid", "max_images": "Antal fotos", "max_total_bytes": "Samlet fotostørrelse",
                "max_tokens": "Svarlængde"}


def llm_limits():
    """Effective limits: WHIST_LLM_* overrides, then the admin page, then config.ini."""
    config = ConfigParser(interpolation=None)
    config.read(CONFIG / "config.ini", encoding="utf-8")
    saved = _saved_llm_config()

    def get(key):
        return (os.environ.get(f"WHIST_LLM_{key.upper()}") or saved.get("llm", key, fallback="")
                or config.get("llm", key, fallback=""))

    return {key: _int_setting(get(key), default) for key, default in LIMIT_DEFAULTS.items()}


def save_llm_limits(values):
    limits = {}
    for key, (low, high) in LIMIT_RANGES.items():
        try:
            value = int(str(values.get(key, "")).strip())
        except ValueError:
            raise ValueError(f"{LIMIT_LABELS[key]} skal være et helt tal") from None
        if not low <= value <= high:
            raise ValueError(f"{LIMIT_LABELS[key]} skal være mellem {low} og {high}")
        limits[key] = value
    config = _saved_llm_config()
    if not config.has_section("llm"):
        config.add_section("llm")
    for key, value in limits.items():
        config.set("llm", key, str(value))
    _write_saved_llm_config(config)


def llm_config():
    config = ConfigParser(interpolation=None)
    config.read(CONFIG / "config.ini", encoding="utf-8")
    secrets = ConfigParser(interpolation=None)
    secrets.read(CONFIG / "secrets.ini", encoding="utf-8")
    saved = _saved_llm_config()

    if saved.has_option("llm", "enabled") and not saved.getboolean(
        "llm", "enabled", fallback=True
    ):
        raise ValueError("AI-import er slået fra i klubadministrationen")

    def get(key, fallback=""):
        return (
            os.environ.get(f"WHIST_LLM_{key.upper()}")
            or saved.get("llm", key, fallback="")
            or config.get("llm", key, fallback=fallback)
        )

    base_url = str(get("base_url", "")).strip().rstrip("/")
    model = str(get("model", "")).strip()
    api_key = str(
        os.environ.get("WHIST_LLM_API_KEY")
        or saved.get("llm", "api_key", fallback="")
        or secrets.get("llm", "api_key", fallback="")
    ).strip()

    if not base_url or not model:
        raise ValueError(
            "AI-import er ikke konfigureret. Udfyld [llm] i config/config.ini."
        )
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise ValueError("LLM base_url skal være en http- eller https-adresse")

    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        **llm_limits(),
    }


def _endpoint(base_url):
    if base_url.endswith("/chat/completions"):
        return base_url

    return base_url + "/chat/completions"


def _vision_prompt(bid_settings=None, club_notes=""):
    """Keep the model's club vocabulary aligned with the actual importer and the club's bid values."""
    from .bids import get_bid
    from .rules import load_rules

    instructions = PROMPT_PATH.read_text(encoding="utf-8")
    guide = HISTORICAL_GUIDE_PATH.read_text(encoding="utf-8")
    rules = load_rules(bid_settings)["bids"]
    vocabulary = {
        key: {
            "label": spec["label"],
            "aliases": sorted(alias for alias, canonical in BID_NAMES.items() if canonical == key),
            "target": spec["target"],
            **({"min_target": spec["min_target"], "max_target": spec["max_target"]}
               if spec["target"] == "variable" else {}),
            # The club's configured stakes, e.g. Sol's value or a base price, in kr.
            **({"club_values": {name: decimal_text(spec[name]) for name in get_bid(key).options}}
               if get_bid(key).options else {}),
        }
        for key, spec in rules.items()
    }
    prompt = (
        instructions + "\n\nCLUB BID DEFINITIONS (current application registry and this club's values):\n"
        + json.dumps(vocabulary, ensure_ascii=False, indent=2)
        + "\n\nHISTORICAL IMPORT GUIDANCE (interpretation rules, not output format):\n"
        + guide
    )
    if club_notes.strip():
        prompt += ("\n\nCLUB NOTES (written by the server administrator for this club; context for reading, "
                   "never a reason to invent or change values):\n" + club_notes.strip())
    return prompt + "\n\nThe JSON schema above is mandatory; do not output Excel."


def _photo_data_url(filename, content):
    ext = Path(filename).suffix.lower()
    media_type = IMAGE_TYPES.get(ext)
    if media_type is None:
        raise ValueError("Foto skal være JPG, PNG eller WebP")

    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _text(value, limit=2000):
    if value is None:
        return ""

    return str(value).strip()[:limit]


def _provider_error(response):
    message = ""
    try:
        payload = response.json()
        error = payload.get("error")
        if isinstance(error, dict):
            message = _text(error.get("message"), 300)
        elif isinstance(error, str):
            message = _text(error, 300)
    except (ValueError, TypeError):
        message = _text(response.text, 300)

    return f"AI-tjenesten svarede med status {response.status_code}" + (
        f": {message}" if message else ""
    )


def _message_text(payload):
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("AI-tjenesten returnerede intet svar")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            _text(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        text = "\n".join(part for part in parts if part)
        if text:
            return text

    raise ValueError("AI-tjenesten returnerede intet svar")


def _model_message(payload):
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}
    return message if isinstance(message, dict) else {}


def _model_reasoning(payload):
    message = _model_message(payload)
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _json_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"AI-svaret var ikke gyldig JSON (linje {exc.lineno}, "
            f"kolonne {exc.colno}: {exc.msg})"
        ) from exc
    raise ValueError("AI-svaret var ikke gyldig JSON")


def _players(value):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("AI-svaret skal indeholde præcis fire spillere")

    players = [_text(name, 80) for name in value]
    if (
        any(not name for name in players)
        or len({name.casefold() for name in players}) != 4
    ):
        raise ValueError("AI-svaret skal indeholde fire unikke spillere")

    return players


def _hand(value, number, players):
    if not isinstance(value, dict):
        raise ValueError("AI-svaret indeholder en ugyldig hånd")

    raw_scores = value.get("scores")
    if not isinstance(raw_scores, dict) or not set(players).issubset(raw_scores):
        raise ValueError(f"AI-svaret mangler score for en spiller i hånd {number}")

    extra = value.get("extra") or []
    if not isinstance(extra, list):
        raise ValueError(f"AI-svaret har ugyldige ekstra meldere i hånd {number}")

    return {
        "number": number,
        "source_id": f"ai-{number}",
        "source_notes": [],
        "bidder": _text(value.get("bidder"), 80),
        "bid": _text(value.get("bid"), 80),
        "target": _text(value.get("target"), 20),
        "actual": _text(value.get("actual"), 20),
        "partner": _text(value.get("partner"), 80),
        "extra": [_text(name, 80) for name in extra if _text(name, 80)],
        "scores": {name: decimal_text(_text(raw_scores[name], 40)) for name in players},
    }


def _bid_from_note(note, players):
    """Parse independently: a doubtful contract must not erase a certain bidder."""
    match = re.fullmatch(r"\s*([^:]+):\s*(.*?)\s*", note)
    if not match:
        return None
    name, contract = match.groups()
    player = next((p for p in players if p.casefold() == name.strip().casefold()), None)
    if not player or not contract:
        return None

    clean = re.sub(r"\s+", "", contract.replace("?", "")).casefold()
    target_match = re.match(r"^(\d{1,2})", clean)
    target = target_match.group(1) if target_match else ""
    kind_text = clean[len(target):]
    kinds = {"alm": "almindelige", "rensol": "ren_sol", "bordlægger": "bordlaegger"}
    supported = {"alm", "almindelige", "gode", "halve", "sans", "vip1", "vip2",
                 "vip3", "sol", "rensol", "ren_sol", "bordlægger", "bordlaegger", "paskrig"}
    bid = kinds.get(kind_text, kind_text) if kind_text in supported else ""
    # Keep the legal bid ID clean; uncertainty is a separate review flag.
    return player, bid, target, ("?" in contract or not bid)


def _infer_sol_followers(hand, players, rules):
    """Use the existing score engine; never infer an ambiguous Gå med participant.

    The Excel import/review validator remains authoritative. This adapter only
    supplies missing extra bidders when exactly one participant/outcome set fits.
    """
    if hand["bid"] not in {"sol", "ren_sol", "bordlaegger"}:
        return
    if hand["bidder"] not in players or hand["extra"]:
        return
    # An uncertain score must not be used as evidence for inference.
    if any(not hand["scores"].get(p) or "?" in hand["scores"][p] for p in players):
        return
    try:
        if any(number(hand["scores"][p]) is None for p in players):
            return
    except (ValueError, ZeroDivisionError):
        return
    others = [p for p in players if p != hand["bidder"]]
    candidates = []
    for count in range(3):  # Existing review supports up to two Gå med players.
        for extras in combinations(others, count):
            candidate = {**hand, "extra": list(extras)}
            for solution in reconstruct(candidate, players, rules):
                candidates.append((list(extras), solution["outcomes"]))
    if len(candidates) == 1:
        hand["extra"] = candidates[0][0]


def _simple_transcription(payload, bid_settings=None):
    """Adapt literal player/row JSON to the existing editable import model."""
    players = _players(payload.get("players"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError("AI-svaret skal indeholde mellem 1 og 500 rækker")

    hands = []
    raw_rows = [["Hånd"] + [label for name in players for label in (f"{name} Δ", f"{name} total")]
                + ["Noter"]]
    remarks = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("values"), list) or len(row["values"]) != 4:
            raise ValueError(f"Ugyldige spillerværdier i række {index}")
        scores = {}
        totals = {}
        raw = [index]
        for name, pair in zip(players, row["values"]):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError(f"Række {index}: {name} skal have ændring og total")
            change, total = (_text(value, 40) for value in pair)
            scores[name] = decimal_text(change) if change else ""
            totals[name] = decimal_text(total) if total else ""
            raw.extend([change, total])
            if "?" in change or "?" in total:
                remarks.append(f"Række {index}, {name}: usikker aflæsning ({change!r}, {total!r}).")
        notes = row.get("notes") or []
        if not isinstance(notes, list):
            raise ValueError(f"Ugyldige noter i række {index}")
        notes = [_text(note, 500) for note in notes if _text(note, 500)]
        raw.append("; ".join(notes))
        raw_rows.append(raw)
        for note in notes:
            remarks.append(f"Række {index}: {note}")
        # New fixed schema: the AI has already interpreted the handwriting.
        # Old JSON remains importable without re-running the model.
        bidder = bid = target = ""
        bid_uncertain = False
        if "bid" in row or "bidder" in row or "target" in row:
            bidder = _text(row.get("bidder"), 80)
            bid = _text(row.get("bid"), 80)
            target = _text(row.get("target"), 20)
            bid_uncertain = row.get("bid_uncertain") is True
            if bidder and bidder not in players:
                remarks.append(f"Række {index}: ukendt melder ({bidder}).")
                bidder = ""
            from .bids import registered_bids
            if bid and bid not in registered_bids():
                remarks.append(f"Række {index}: ulovlig melding ({bid}); gennemgå kilden.")
                bid = ""
                bid_uncertain = True
            if bid_uncertain:
                remarks.append(f"Række {index}: usikker melding ({row.get('bid_raw') or bid}).")
        else:
            parsed = [_bid_from_note(note, players) for note in notes]
            parsed = [entry for entry in parsed if entry is not None]
            if len(notes) == 1 and len(parsed) == 1:
                bidder, bid, target, bid_uncertain = parsed[0]
                if bid_uncertain:
                    remarks.append(f"Række {index}: usikker eller ukendt melding ({notes[0]}).")
            elif notes:
                remarks.append(f"Række {index}: melding kræver manuel gennemgang.")
        hands.append({
            "number": index, "source_id": f"ai-{index}", "source_notes": notes + (["Melding som skrevet: " + _text(row.get("bid_raw"), 150)] if row.get("bid_raw") else []),
            "source_json": row, "bid_box": row.get("bid_box"), "bid_quad": row.get("bid_quad"), "value_boxes": row.get("value_boxes"), "row_box": row.get("row_box"), "photo_id": row.get("photo_id"),
            "bidder": bidder, "bid": bid, "bid_uncertain": bid_uncertain, "target": target, "actual": "", "partner": "",
            "extra": [], "scores": scores, "totals": totals,
        })
    # Attach sheet orientation and name boxes for visual verification. These
    # annotations never affect the scoring or Excel importer.
    sheet_lookup = {}
    for sheet in payload.get("sheets", []):
        if not isinstance(sheet, dict):
            continue
        try:
            key = (int(sheet.get("photo_id")), int(sheet.get("sheet_id")))
        except (ValueError, TypeError):
            continue
        sheet_lookup[key] = sheet
    for hand in hands:
        source = hand.get("source_json") or {}
        try:
            key = (int(source.get("photo_id")), int(source.get("sheet_id", 1)))
        except (ValueError, TypeError):
            continue
        sheet = sheet_lookup.get(key, {})
        hand["sheet_id"] = key[1]
        hand["sheet_box"] = sheet.get("sheet_box")
        hand["player_boxes"] = sheet.get("player_boxes")
        rotation = sheet.get("rotation", 0)
        hand["rotation"] = rotation if rotation in (0, 90, 180, 270) else 0

    # Feed uniquely reconstructed SOL participants to the existing validator.
    # No changes to the Excel import path or the scoring rules.
    from .rules import load_rules
    rules = load_rules(bid_settings)["bids"]
    for hand in hands:
        _infer_sol_followers(hand, players, rules)

    other_notes = payload.get("other_notes") or []
    if not isinstance(other_notes, list):
        raise ValueError("Ugyldige øvrige noter")
    remarks.extend(_text(note, 500) for note in other_notes if _text(note, 500))
    date = _text(payload.get("date"), 40)
    date_precision = "day" if re.fullmatch(r"[12][0-9]{3}-[01][0-9]-[0-3][0-9]", date) else "unknown"
    if date_precision == "unknown":
        if date:
            remarks.append(f"Dato som skrevet (ikke bekræftet): {date}")
        date = ""
    return {
        "players": players, "hands": hands,
        "worksheets": [{"name": "AI rådata", "rows": raw_rows}],
        "date": date, "date_precision": date_precision,
        "remarks": "\n".join(remarks),
    }


def _normalize(payload, bid_settings=None):
    if not isinstance(payload, dict):
        raise ValueError("AI-svaret skal være et JSON-objekt")

    if "import" not in payload and "players" in payload and "rows" in payload:
        return _simple_transcription(payload, bid_settings)

    import_data = payload.get("import")
    if not isinstance(import_data, dict):
        raise ValueError("AI-svaret mangler importdata")

    players = _players(import_data.get("players"))
    hands = import_data.get("hands")
    if not isinstance(hands, list) or not hands or len(hands) > 500:
        raise ValueError("AI-svaret skal indeholde mellem 1 og 500 hænder")

    normalized_hands = [
        _hand(hand, number, players) for number, hand in enumerate(hands, 1)
    ]

    worksheets = payload.get("worksheets") or []
    if not isinstance(worksheets, list) or len(worksheets) > MAX_WORKSHEETS:
        raise ValueError("AI-svaret har for mange ark")

    normalized_sheets = []
    for sheet in worksheets:
        if not isinstance(sheet, dict) or not isinstance(sheet.get("rows"), list):
            raise ValueError("AI-svaret indeholder et ugyldigt regneark")
        rows = []
        for row in sheet["rows"][:MAX_ROWS]:
            if not isinstance(row, list):
                raise ValueError("AI-svaret indeholder en ugyldig række")
            rows.append([_text(value) for value in row[:MAX_COLUMNS]])
        normalized_sheets.append({"name": _text(sheet.get("name"), 80), "rows": rows})

    date = _text(import_data.get("date"), 40)
    date_precision = _text(import_data.get("date_precision"), 20).lower()
    if date_precision not in _DATE_PRECISIONS:
        date_precision = "unknown"
    if not re.fullmatch(r"[12][0-9]{3}(?:-[01][0-9](?:-[0-3][0-9])?)?", date):
        date = ""
        date_precision = "unknown"
    if not date:
        date_precision = "unknown"

    remarks = _text(import_data.get("remarks_markdown"), MAX_REMARKS)

    return {
        "players": players,
        "hands": normalized_hands,
        "worksheets": normalized_sheets,
        "date": date,
        "date_precision": date_precision,
        "remarks": remarks,
    }


def _safe_append(worksheet, values):
    worksheet.append(["" if value is None else value for value in values])
    for cell in worksheet[worksheet.max_row]:
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.data_type = "s"


def _import_rows(data):
    players = data["players"]
    extras = max((len(hand["extra"]) for hand in data["hands"]), default=0)
    headers = ["Hånd", "Melder", "Melding", "Meldt", "Stik", "Makker"]
    for name in players:
        headers.extend([f"{name} Δ", f"{name} total"])
    headers.extend(f"Melder {number}" for number in range(2, extras + 2))
    rows = [headers]

    for hand in data["hands"]:
        row = [
            hand["number"],
            hand["bidder"],
            hand["bid"],
            hand["target"],
            hand["actual"],
            hand["partner"],
        ]
        for name in players:
            row.extend([hand["scores"][name], hand.get("totals", {}).get(name, "")])
        row.extend(hand["extra"] + [""] * (extras - len(hand["extra"])))
        rows.append(row)

    return rows


def _build_import_workbook(data):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Hænder"
    for row in _import_rows(data):
        _safe_append(worksheet, row)
        for cell in worksheet[worksheet.max_row]:
            if isinstance(cell.value, str) and "?" in cell.value:
                cell.fill = _YELLOW

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _sheet_name(value, used):
    name = re.sub(r"[\[\]:*?/\\]", "_", value).strip()[:31] or "Ark"
    candidate = name
    suffix = 2
    while candidate.casefold() in used:
        ending = f" ({suffix})"
        candidate = name[: 31 - len(ending)] + ending
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _build_raw_workbook(data):
    workbook = Workbook()
    workbook.remove(workbook.active)
    used = set()

    for index, sheet in enumerate(data["worksheets"], 1):
        worksheet = workbook.create_sheet(
            _sheet_name(sheet["name"] or f"Ark {index}", used)
        )
        for row in sheet["rows"]:
            _safe_append(worksheet, row)
            for cell in worksheet[worksheet.max_row]:
                if isinstance(cell.value, str) and "?" in cell.value:
                    cell.fill = _YELLOW

    if not workbook.sheetnames:
        worksheet = workbook.create_sheet("Hænder")
        for row in _import_rows(data):
            _safe_append(worksheet, row)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _build_archive(result, photos):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("transcription.xlsx", result["transcription"])
        archive.writestr("import.xlsx", result["workbook"])
        archive.writestr("remarks.md", result["remarks"])
        if "raw_json" in result:
            archive.writestr("transcription.json", result["raw_json"])
        for index, (filename, content) in enumerate(photos, 1):
            original = Path(filename.replace("\\", "/")).name
            safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", original).strip(" .")
            archive.writestr(f"photos/{index:02d}-{safe_name or 'photo'}", content)

    return output.getvalue()


def build_workbooks(payload, photos=(), bid_settings=None):
    data = _normalize(payload, bid_settings)
    source_sheets = [
        {
            "name": sheet["name"],
            "rows": sheet["rows"],
            "flags": [
                ["uncertain" if "?" in str(value) else "" for value in row]
                for row in sheet["rows"]
            ],
            "truncated": False,
        }
        for sheet in data["worksheets"]
    ]
    result = {
        "workbook": _build_import_workbook(data),
        "transcription": _build_raw_workbook(data),
        "remarks": data["remarks"],
        "date": data["date"],
        "date_precision": data["date_precision"],
        "worksheet_count": len(data["worksheets"]),
        "source_sheets": source_sheets,
        "data": {"players": data["players"], "hands": data["hands"]},
    }
    result["archive"] = _build_archive(result, photos)
    return result


def _report_progress(callback, stage, message):
    if callback:
        callback(stage, message)


async def transcribe_images(photos, on_progress=None, bid_settings=None, club_notes=""):
    config = llm_config()
    if not photos:
        raise ValueError("Vælg mindst ét foto")
    if len(photos) > config["max_images"]:
        raise ValueError(f"AI-import understøtter højst {config['max_images']} fotos")
    if sum(len(content) for _, content in photos) > config["max_total_bytes"]:
        raise ValueError("Fotogene er for store til AI-import")
    _report_progress(
        on_progress,
        "configuration",
        f"Konfiguration kontrolleret: model {config['model']} på "
        f"{urlsplit(config['base_url']).hostname or 'LLM-endpoint'}.",
    )

    try:
        prompt = _vision_prompt(bid_settings, club_notes)
    except OSError as exc:
        raise RuntimeError(f"Mangler transskriptionsprompt: {exc}") from exc
    _report_progress(on_progress, "preparation", "Transskriptionsprompten er indlæst"
                     + (" med klubbens AI-noter." if club_notes.strip() else "."))

    content = [{"type": "text", "text": "Transskribér vedlagte scoreark."}]
    for filename, photo in photos:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _photo_data_url(filename, photo)},
            }
        )

    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"

    request = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        # "temperature": 0,
        "response_format": {"type": "json_object"},
        # OpenAI's own API wants the newer name; other compatible providers take max_tokens.
        ("max_completion_tokens" if urlsplit(config["base_url"]).hostname == "api.openai.com"
         else "max_tokens"): config["max_tokens"],
    }

    _report_progress(
        on_progress,
        "request",
        f"Sender {len(photos)} foto/fotos til vision-modellen.",
    )
    try:
        async with httpx.AsyncClient(timeout=config["timeout"]) as client:
            response = await client.post(
                _endpoint(config["base_url"]), headers=headers, json=request
            )
    except httpx.TimeoutException as exc:
        raise ValueError("AI-tjenesten svarede ikke i tide") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Kunne ikke kontakte AI-tjenesten: {exc}") from exc

    _report_progress(
        on_progress,
        "response",
        f"AI-tjenesten svarede med status {response.status_code} ({len(response.content)} bytes).",
    )
    if response.status_code >= 400:
        raise ValueError(_provider_error(response))
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("AI-svaret er for stort")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("AI-tjenesten returnerede ugyldig JSON") from exc

    # Report the exact generated text BEFORE parsing/validation, including on failure.
    # Never include the request headers, credentials, or base64 image payloads.
    reasoning = _model_reasoning(payload)
    if reasoning:
        _report_progress(on_progress, "reasoning", reasoning)
    try:
        generated = _message_text(payload)
    except ValueError:
        _report_progress(on_progress, "model_output", json.dumps(payload, ensure_ascii=False, indent=2))
        raise
    _report_progress(on_progress, "model_output", generated)
    _report_progress(on_progress, "parsing", "Parser modellens JSON-svar.")
    result = build_workbooks(_json_object(generated), photos, bid_settings)
    result["raw_json"] = json.dumps(_json_object(generated), ensure_ascii=False, indent=2)
    result["archive"] = _build_archive(result, photos)
    _report_progress(
        on_progress,
        "workbooks",
        f"Modellen beskrev {len(result['data']['hands'])} hænder og "
        f"{result['worksheet_count']} råark; "
        "Excel-filer og ZIP-arkiv er klar.",
    )
    return result
