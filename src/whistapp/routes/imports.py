"""Historical import from Excel and photographs, and a night's import artefacts."""

import asyncio
import copy
import json
import logging
import re
import shutil

from datetime import datetime
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from whistapp.bids.registry import registered_bids

from ..buildinfo import build_stamp
from ..club import active_club_id, club_settings
from ..database import connect
from ..kasse import ledger as kasse
from ..finance import settlement_entries
from ..rule_versions import club_versions, is_club_night, night_rules, stamp_night
from .rules import version_name
from ..historical_import import (
    decimal_text,
    field_issues,
    infer_date_from_filename,
    MAX_BYTES,
    normalize_workbook,
    number as import_number,
    save_import,
    validate,
    workbook_preview,
)
from ..settings import DATA
from ..vision_import import transcribe_images
from ..web import ai_import_allowed, render

router = APIRouter()


# Historical workbook import: preview, correct, validate, commit.
def import_rule_version(raw):
    """The rule version an imported night is played under: the one chosen on the form, else
    (None) the club's current one. stamp_night checks that it belongs to the club."""
    return int(raw) if str(raw or "").isdigit() else None


def rule_version_choices(db):
    """The club's versions, newest first; the form suggests the newest one."""
    return [dict(id=v["id"], name=version_name(v)) for v in club_versions(db, active_club_id())]


def historical_date(value, precision):
    """Return sortable storage timestamp and human-readable title; precision is stored separately."""
    value = value.strip()
    formats = {
        "minute": ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M"),
        "day": ("%Y-%m-%d", "%Y-%m-%d"),
        "month": ("%Y-%m", "%Y-%m"),
        "year": ("%Y", "%Y"),
    }

    if precision == "unknown":
        if value:
            raise ValueError("Dato skal være tom ved ukendt tidspunkt")
        return "0001-01-01 00:00:00", "Ukendt dato"

    if not value and precision != "unknown":
        precision = "unknown"
        return "0001-01-01 00:00:00", "Ukendt dato"
    if precision not in formats:
        raise ValueError("Ukendt datopræcision")

    fmt, label = formats[precision]
    try:
        parsed = datetime.strptime(value, fmt)
        if parsed.strftime(fmt) != value:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Ugyldig dato: forvent {label}")
    return parsed.strftime("%Y-%m-%d %H:%M:%S"), value


def match_import_players(db, names):
    """Match exact member names and persistent aliases, never guess conflicts."""
    labels = {}
    for row in db.execute("SELECT id,name FROM club_members WHERE club_id=?", (active_club_id(),)):
        labels.setdefault(row["name"].strip().casefold(), set()).add(row["id"])

    for row in db.execute("SELECT member_id,alias FROM club_member_aliases WHERE club_id=?", (active_club_id(),)):
        labels.setdefault(row["alias"].strip().casefold(), set()).add(row["member_id"])

    matched = {
        name: next(iter(labels[name.strip().casefold()]))
        for name in names
        if len(labels.get(name.strip().casefold(), ())) == 1
    }
    ids = list(matched.values())
    return {name: mid for name, mid in matched.items() if ids.count(mid) == 1}


def _set_import_money(db, sid, enabled):
    session = db.execute(
        "SELECT origin,is_club FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    if not session or session["origin"] != "imported":
        raise ValueError("Ukendt historisk import")

    db.execute("DELETE FROM club_member_settlements WHERE session_id=?", (sid,))

    if enabled:
        if not session["is_club"] or not is_club_night(db, sid):
            raise ValueError("Alle fire spillere skal være klubmedlemmer på aftenens medlemsliste")

        # The night's rule version decides the money.
        night = night_rules(db, sid)
        entries = settlement_entries(db, sid, night["money_mode"], night["placements"], night["point_rate"])
        db.executemany(
            """INSERT INTO club_member_settlements
            (session_id,member_id,place,amount_numerator,amount_denominator)
            VALUES (?,?,?,?,?)""",
            [(sid, *entry) for entry in entries],
        )

    db.execute(
        "UPDATE sessions SET collect_club_money=? WHERE id=?", (int(enabled), sid)
    )


# Original photographs belong to a saved evening, not to individual hands.
PHOTO_ROOT = DATA / "historical_photos"


AI_ROOT = DATA / "ai_imports"


SOURCE_ROOT = DATA / "historical_sources"


AI_JOBS = {}


SOURCE_MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


MAX_PHOTO_BYTES = 12 * 1024 * 1024


MAX_ARCHIVE_BYTES = 70 * 1024 * 1024


MAX_PHOTOS = 30


PHOTO_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _photo_bytes(name, content):
    ext = Path(name).suffix.lower()
    if ext not in PHOTO_TYPES or not content or len(content) > MAX_PHOTO_BYTES:
        raise ValueError("Foto skal være JPG, PNG eller WebP og højst 12 MB")

    # Verify file signature; never serve untrusted SVG or HTML.
    if ext in (".jpg", ".jpeg") and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("Ugyldig JPEG")
    if ext == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Ugyldig PNG")
    if ext == ".webp" and not (content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
        raise ValueError("Ugyldig WebP")
    return ext


def _save_photos(db, sid, photos):
    count = db.execute(
        "SELECT COUNT(*) FROM session_photos WHERE session_id=?", (sid,)
    ).fetchone()[0]
    if count + len(photos) > MAX_PHOTOS:
        raise ValueError("Højst 30 fotos pr. spilaften")

    directory = PHOTO_ROOT / str(sid)
    directory.mkdir(parents=True, exist_ok=True)
    created = []

    try:
        for filename, content in photos:
            ext = _photo_bytes(filename, content)
            stored = uuid4().hex + ext
            dest = directory / stored
            dest.write_bytes(content)
            created.append(dest)

            db.execute(
                "INSERT INTO session_photos(session_id,stored_name,original_name) VALUES (?,?,?)",
                (sid, stored, Path(filename).name[:200]),
            )
    except Exception:
        for dest in created:
            dest.unlink(missing_ok=True)
        raise


def _photo_list(db, sid):
    return [
        dict(row)
        for row in db.execute(
            "SELECT id,original_name FROM session_photos WHERE session_id=? ORDER BY id",
            (sid,),
        )
    ]


async def _read_photos(uploads):
    photos = []
    for upload in uploads:
        if not getattr(upload, "filename", ""):
            continue

        content = await upload.read(MAX_PHOTO_BYTES + 1)
        _photo_bytes(upload.filename, content)
        photos.append((upload.filename, content))

    if len(photos) > MAX_PHOTOS:
        raise ValueError("Højst 30 fotos")

    return photos


def _stage_photos(photos):
    if not photos:
        return ""

    token = uuid4().hex
    directory = PHOTO_ROOT / "_staging" / token
    directory.mkdir(parents=True, exist_ok=False)
    manifest = []

    for index, (name, content) in enumerate(photos):
        stored_name = f"{index:02d}{Path(name).suffix.lower()}"
        (directory / stored_name).write_bytes(content)
        manifest.append(
            {
                "stored_name": stored_name,
                "original_name": _source_basename(name),
            }
        )
    (directory / ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    return token


def _staged_photos(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token)):
        return []

    directory = PHOTO_ROOT / "_staging" / token
    if not directory.is_dir():
        return []

    try:
        manifest = json.loads(
            (directory / ".manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        manifest = []
    if manifest:
        return [
            (item["original_name"], (directory / item["stored_name"]).read_bytes())
            for item in manifest
            if isinstance(item, dict)
            and re.fullmatch(r"\d{2}\.(jpg|jpeg|png|webp)", item.get("stored_name", ""))
            and (directory / item["stored_name"]).is_file()
        ]
    return [
        (path.name, path.read_bytes())
        for path in sorted(directory.iterdir())
        if path.is_file() and re.fullmatch(r"\d{2}\.(jpg|jpeg|png|webp)", path.name)
    ]


def _staged_photo_names(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token)):
        return []
    directory = PHOTO_ROOT / "_staging" / str(token)
    return [
        path.name
        for path in sorted(directory.glob("*"))
        if path.is_file() and re.fullmatch(r"\d{2}\.(jpg|jpeg|png|webp)", path.name)
    ]


def _source_basename(name):
    return Path(str(name).replace("\\", "/")).name[:200] or "source-file"


def _source_media_type(name):
    return SOURCE_MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _stage_source_files(files):
    if not files:
        return ""
    token = uuid4().hex
    directory = SOURCE_ROOT / "_staging" / token
    directory.mkdir(parents=True, exist_ok=False)
    manifest = []
    for index, (name, content, kind) in enumerate(files):
        original_name = _source_basename(name)
        suffix = Path(original_name).suffix.lower()
        stored_name = f"{index:03d}{suffix or '.bin'}"
        (directory / stored_name).write_bytes(content)
        manifest.append(
            {
                "stored_name": stored_name,
                "original_name": original_name,
                "kind": kind,
                "media_type": _source_media_type(original_name),
            }
        )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return token


def _staged_source_manifest(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token)):
        return []
    directory = SOURCE_ROOT / "_staging" / str(token)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [
        item
        for item in manifest
        if isinstance(item, dict)
        and re.fullmatch(r"\d{3}\.[a-z0-9]{1,8}", str(item.get("stored_name", "")))
        and (directory / item["stored_name"]).is_file()
    ]


def _staged_source_files(token):
    directory = SOURCE_ROOT / "_staging" / str(token)
    return [
        {**item, "content": (directory / item["stored_name"]).read_bytes()}
        for item in _staged_source_manifest(token)
    ]


def _source_file_preview(item, path):
    view = {
        **{key: value for key, value in item.items() if key != "content"},
    }
    try:
        suffix = Path(item["original_name"]).suffix.lower()
        if suffix == ".xlsx" and path.stat().st_size <= 10 * 1024 * 1024:
            view["sheets"] = workbook_preview(path.read_bytes())
        elif suffix == ".json" and path.stat().st_size <= 2 * 1024 * 1024:
            view["json_text"] = json.dumps(
                json.loads(path.read_text(encoding="utf-8-sig")),
                ensure_ascii=False, indent=2,
            )
        elif suffix == ".md" and path.stat().st_size <= 100_000:
            view["text"] = path.read_text(encoding="utf-8")
        elif suffix == ".zip":
            with ZipFile(path) as archive:
                zip_texts = []
                view["zip_entries"] = [
                    {"name": entry.filename, "size": entry.file_size}
                    for entry in archive.infolist()[:200]
                    if not entry.is_dir()
                ]
                for entry in archive.infolist()[:200]:
                    if (
                        not entry.is_dir()
                        and Path(entry.filename).suffix.lower() == ".md"
                        and entry.file_size <= 100_000
                    ):
                        try:
                            zip_texts.append(
                                {
                                    "name": entry.filename,
                                    "text": archive.read(entry).decode("utf-8"),
                                }
                            )
                        except (UnicodeDecodeError, BadZipFile):
                            continue
                view["zip_texts"] = zip_texts
    except (OSError, BadZipFile, ValueError):
        view["preview_error"] = "Forhåndsvisning kunne ikke indlæses."
    return view


def _staged_source_views(token):
    directory = SOURCE_ROOT / "_staging" / str(token)
    return [
        _source_file_preview(
            {
                **item,
                "url": f"/import/source-staged/{token}/{item['stored_name']}",
            },
            directory / item["stored_name"],
        )
        for item in _staged_source_manifest(token)
    ]


def _remove_staged_source_files(token):
    if re.fullmatch(r"[0-9a-f]{32}", str(token)):
        shutil.rmtree(SOURCE_ROOT / "_staging" / str(token), ignore_errors=True)


def _ai_source_files(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token)):
        return []
    directory = AI_ROOT / str(token)
    definitions = (
        ("transcription.json", "AI-rå-JSON"),
        ("transcription.xlsx", "AI-råtransskription"),
        ("import.xlsx", "AI-importark"),
        ("remarks.md", "LLM-remarks"),
        ("source.zip", "AI-sourcearkiv"),
    )
    sources = []
    for filename, kind in definitions:
        path = directory / filename
        if path.is_file():
            sources.append(
                {
                    "original_name": filename,
                    "kind": kind,
                    "media_type": "application/json" if filename.endswith(".json") else _source_media_type(filename),
                    "content": path.read_bytes(),
                }
            )
    return sources


def _save_source_files(db, sid, files):
    if not files:
        return []
    directory = SOURCE_ROOT / str(sid)
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    ids = []
    try:
        for item in files:
            original_name = _source_basename(item["original_name"])
            suffix = Path(original_name).suffix.lower()
            stored_name = f"{uuid4().hex}{suffix or '.bin'}"
            path = directory / stored_name
            path.write_bytes(item["content"])
            created.append(path)
            cursor = db.execute(
                """INSERT INTO session_source_files
                   (session_id,stored_name,original_name,kind,media_type)
                   VALUES (?,?,?,?,?)""",
                (
                    sid,
                    stored_name,
                    original_name,
                    item["kind"],
                    item.get("media_type") or _source_media_type(original_name),
                ),
            )
            ids.append(cursor.lastrowid)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return ids


def _session_source_view(db, sid):
    records = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM session_source_files WHERE session_id=? ORDER BY id", (sid,)
        )
    ]
    previews = [
        _source_file_preview(
            {**record, "url": f"/sessions/{sid}/source-files/{record['id']}"},
            SOURCE_ROOT / str(sid) / record["stored_name"],
        )
        for record in records
    ]
    return records, previews


def _record_ai_progress(job_id, stage, message, status=None, review_url=None):
    job = AI_JOBS.get(job_id)
    if job is None:
        return
    if status:
        job["status"] = status
    job["sequence"] += 1
    job["events"].append(
        {
            "id": job["sequence"],
            "stage": stage,
            "message": message,
            "status": job["status"],
            "review_url": review_url or "",
        }
    )


def _club_ai_context():
    """The active club's bid settings and AI notes for a photo transcription."""
    with connect() as db:
        settings = club_settings(db)
    if not settings:
        return {}, ""
    return json.loads(settings["bid_settings"] or "{}"), settings["ai_notes"] or ""


async def _run_ai_import_job(job_id, photos, photo_token, bid_settings=None, club_notes=""):
    _record_ai_progress(
        job_id,
        "start",
        f"Klargjort {len(photos)} foto til transskription.",
    )
    try:
        result = await transcribe_images(
            photos,
            on_progress=lambda stage, message: _record_ai_progress(
                job_id, stage, message
            ),
            bid_settings=bid_settings,
            club_notes=club_notes,
        )
        artifact_token = _stage_ai_artifacts(
            result["workbook"],
            result["transcription"],
            result["remarks"],
            result["archive"],
            result.get("raw_json", ""),
        )
        data = result["data"]
        data["pending_photos"] = photo_token
        data["ai_token"] = artifact_token
        data["ai_job_id"] = job_id
        job = AI_JOBS[job_id]
        job["data"] = data
        _write_draft(job_id, {
            "data": data, "title": "", "game_date": result["date"],
            "date_precision": result["date_precision"], "mapping": {},
        })
        job["game_date"] = result["date"]
        job["date_precision"] = result["date_precision"]
        job["source_workbooks"] = [
            {"title": "Raw AI-transskription", "sheets": result["source_sheets"]},
            {
                "title": "Hænder-importark",
                "sheets": workbook_preview(result["workbook"]),
            },
        ]
        _record_ai_progress(
            job_id,
            "complete",
            "Transskriptionen er klar til gennemgang og validering.",
            status="complete",
            review_url=f"/import/ai/jobs/{job_id}/review",
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("AI import job %s failed", job_id)
        _record_ai_progress(
            job_id,
            "error",
            str(exc)
            if isinstance(exc, (ValueError, RuntimeError))
            else "Transskriptionen fejlede; se serverloggen.",
            status="failed",
        )


def _draft_path(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
        raise HTTPException(404)
    return AI_ROOT / str(job_id) / "review.json"


def _write_draft(job_id, payload):
    path = _draft_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _read_draft(job_id):
    path = _draft_path(job_id)
    if not path.is_file():
        raise HTTPException(404)
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/import/current", response_class=HTMLResponse)
def current_imports(request: Request):
    drafts = []
    if AI_ROOT.is_dir():
        for path in AI_ROOT.glob("*/review.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                drafts.append({
                    "id": path.parent.name,
                    "title": item.get("title") or "AI-import",
                    "date": item.get("game_date") or "Ukendt dato",
                    "hands": len(item.get("data", {}).get("hands", [])),
                    "updated": path.stat().st_mtime,
                })
            except (OSError, ValueError, TypeError):
                continue
    drafts.sort(key=lambda item: item["updated"], reverse=True)
    return render(request, "current_imports.html", drafts=drafts)


@router.get("/import/current/{job_id}", response_class=HTMLResponse)
def resume_import(request: Request, job_id: str):
    draft = _read_draft(job_id)
    return import_review(
        request, draft["data"], draft.get("title", ""),
        draft.get("game_date", ""), draft.get("replace_id", ""),
        draft.get("mapping", {}), draft.get("date_precision", "unknown"),
    )


@router.post("/import/current/{job_id}/save")
async def save_import_draft(request: Request, job_id: str):
    draft = _read_draft(job_id)
    payload = await request.json()
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("hands"), list) or len(data["hands"]) > 500:
        raise HTTPException(400, "Ugyldige hånddata")
    if data.get("ai_job_id") != job_id:
        raise HTTPException(400, "Forkert import")
    draft.update({
        "data": data,
        "title": str(payload.get("title", ""))[:100],
        "game_date": str(payload.get("game_date", ""))[:32],
        "date_precision": str(payload.get("date_precision", "unknown"))[:16],
    })
    _write_draft(job_id, draft)
    return {"saved": True}


def _stage_ai_artifacts(workbook, transcription, remarks, archive, raw_json=""):
    token = uuid4().hex
    directory = AI_ROOT / token
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "import.xlsx").write_bytes(workbook)
    (directory / "transcription.xlsx").write_bytes(transcription)
    (directory / "remarks.md").write_text(remarks, encoding="utf-8")
    (directory / "transcription.json").write_text(raw_json, encoding="utf-8")
    (directory / "source.zip").write_bytes(archive)

    return token


def _ai_artifact_view(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token)):
        return None

    directory = AI_ROOT / str(token)
    if not directory.is_dir():
        return None

    files = [
        {"name": "transcription.json", "label": "Vis AI JSON"}
        if (directory / "transcription.json").is_file()
        else None,
        {"name": "import.xlsx", "label": "Download importark (.xlsx)"}
        if (directory / "import.xlsx").is_file()
        else None,
        {"name": "transcription.xlsx", "label": "Download transskription (.xlsx)"}
        if (directory / "transcription.xlsx").is_file()
        else None,
        {"name": "remarks.md", "label": "Download bemærkninger (.md)"}
        if (directory / "remarks.md").is_file()
        else None,
        {"name": "source.zip", "label": "Download alle filer inkl. fotos (.zip)"}
        if (directory / "source.zip").is_file()
        else None,
    ]
    files = [entry for entry in files if entry]
    remarks = ""
    if (directory / "remarks.md").is_file():
        remarks = (directory / "remarks.md").read_text(encoding="utf-8")
    workbooks = []
    for filename, title in (
        ("transcription.xlsx", "Raw AI-transskription"),
        ("import.xlsx", "Hænder-importark"),
    ):
        path = directory / filename
        if path.is_file():
            view = _source_file_preview(
                {
                    "original_name": filename,
                    "kind": "llm_output",
                    "media_type": SOURCE_MEDIA_TYPES[".xlsx"],
                },
                path,
            )
            view["title"] = title
            workbooks.append(view)
    zip_entries = []
    if (directory / "source.zip").is_file():
        try:
            with ZipFile(directory / "source.zip") as archive:
                zip_entries = [
                    {"name": entry.filename, "size": entry.file_size}
                    for entry in archive.infolist()[:200]
                    if not entry.is_dir()
                ]
        except BadZipFile:
            zip_entries = []
    if not files and not remarks:
        return None

    return {
        "token": str(token),
        "files": files,
        "workbooks": workbooks,
        "zip_entries": zip_entries,
        "remarks": remarks,
        "remarks_truncated": False,
    }


def _source_workbooks_for_data(data):
    if data.get("ai_token"):
        artifacts = _ai_artifact_view(data["ai_token"])
        return artifacts.get("workbooks", []) if artifacts else []
    result = []
    for item in _staged_source_files(data.get("pending_source_files", "")):
        if item["kind"] not in {"original_upload", "uploaded_workbook"}:
            continue
        if Path(item["original_name"]).suffix.lower() != ".xlsx":
            continue
        try:
            result.append(
                {
                    "title": item["original_name"],
                    "sheets": workbook_preview(item["content"]),
                }
            )
        except (ValueError, BadZipFile):
            continue
    return result


def _remove_ai_artifacts(token):
    if re.fullmatch(r"[0-9a-f]{32}", str(token)):
        shutil.rmtree(AI_ROOT / str(token), ignore_errors=True)


def _unpack_import(filename, content):
    if filename.lower().endswith(".xlsx"):
        if len(content) > MAX_BYTES:
            raise ValueError("Excel-filen er for stor (maks. 2 MB)")
        return content, []

    if not filename.lower().endswith(".zip"):
        raise ValueError("Vælg en .xlsx- eller .zip-fil")
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP-filen er for stor")

    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = [
                e
                for e in archive.infolist()
                if not e.is_dir() and not e.filename.startswith("__MACOSX/")
            ]
            if len(entries) > 80:
                raise ValueError("For mange filer i ZIP")
            if sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIP indeholder for mange data")

            sheets = [
                e
                for e in entries
                if e.filename.lower().endswith(".xlsx")
                and not Path(e.filename).name.startswith("~$")
            ]
            if len(sheets) != 1:
                raise ValueError("ZIP skal indeholde præcis én Excel-fil")

            photos = []
            for entry in entries:
                if entry.flag_bits & 1:
                    raise ValueError("Krypteret ZIP understøttes ikke")
                if (
                    entry.file_size > MAX_PHOTO_BYTES
                    and Path(entry.filename).suffix.lower() in PHOTO_TYPES
                ):
                    raise ValueError("Et foto er for stort")

                if Path(entry.filename).suffix.lower() in PHOTO_TYPES:
                    if len(photos) >= MAX_PHOTOS:
                        raise ValueError("For mange fotos")

                    data = archive.read(entry)
                    _photo_bytes(entry.filename, data)
                    photos.append((Path(entry.filename).name, data))

            if sheets[0].file_size > MAX_BYTES:
                raise ValueError("Excel-filen er for stor")

            return archive.read(sheets[0]), photos
    except BadZipFile as exc:
        raise ValueError("Ugyldig ZIP-fil") from exc


def _render_import_page(
    request,
    errors=None,
    active_tab="excel",
    ai_artifacts=None,
    pending_photos="",
    staged_photo_names=None,
    source_workbooks=None,
    source_files=None,
):
    with connect() as db:
        members = [
            dict(r)
            for r in db.execute("SELECT id,name FROM club_members WHERE club_id=? ORDER BY name", (active_club_id(),))
        ]
        previous = [
            dict(r)
            for r in db.execute(
                "SELECT id,title FROM sessions WHERE origin='imported' AND club_id=? ORDER BY id DESC",
                (active_club_id(),),
            )
        ]
        rule_versions = rule_version_choices(db)

    return render(
        request,
        "historical_import.html",
        members=members,
        previous=previous,
        rule_versions=rule_versions,
        data=None,
        errors=errors or [],
        title="",
        game_date="",
        date_precision="unknown",
        active_tab=active_tab,
        ai_artifacts=ai_artifacts,
        pending_photos=pending_photos,
        staged_photo_names=staged_photo_names or [],
        source_workbooks=source_workbooks or [],
        source_files=source_files or [],
        ai_allowed=ai_import_allowed(),
    )


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return _render_import_page(request)


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(request: Request):
    form = await request.form()
    upload = form.get("workbook")
    source_token = ""
    photo_token = ""
    source_workbooks = []

    try:
        if not upload or not getattr(upload, "filename", ""):
            raise ValueError("Vælg en .xlsx- eller .zip-fil")

        content = await upload.read(MAX_ARCHIVE_BYTES + 1)
        workbook, photos = _unpack_import(upload.filename, content)
        staged = [(upload.filename, content, "original_upload")]
        if upload.filename.lower().endswith(".zip"):
            staged.append(("workbook.xlsx", workbook, "uploaded_workbook"))
        source_token = _stage_source_files(staged)
        photo_token = _stage_photos(photos)
        data = normalize_workbook(workbook)
        source_sheets = data.pop("source_sheets")
        source_workbooks = [{"title": upload.filename, "sheets": source_sheets}]
        data["pending_photos"] = photo_token
        data["pending_source_files"] = source_token

        inferred = infer_date_from_filename(upload.filename)
        if inferred:
            data["game_date"], data["date_precision"] = inferred
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        if not source_workbooks and "workbook" in locals():
            try:
                source_workbooks = [
                    {"title": upload.filename, "sheets": workbook_preview(workbook)}
                ]
            except (ValueError, BadZipFile, OSError):
                source_workbooks = []
        return _render_import_page(
            request,
            errors=[str(exc)],
            pending_photos=photo_token,
            staged_photo_names=_staged_photo_names(photo_token),
            source_workbooks=source_workbooks,
            source_files=_staged_source_views(source_token),
        )

    return import_review(
        request,
        data,
        "",
        data.get("game_date", ""),
        "",
        {},
        data.get("date_precision", "unknown"),
        source_workbooks=source_workbooks,
        source_files=_staged_source_views(source_token),
    )


@router.post("/import/json/preview", response_class=HTMLResponse)
async def import_json_preview(request: Request):
    """Review a saved vision transcription without another model request."""
    form = await request.form()
    upload = form.get("transcription_json")
    try:
        if not upload or not str(getattr(upload, "filename", "")).lower().endswith(".json"):
            raise ValueError("Vælg en .json-fil")
        raw = await upload.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("JSON-filen er for stor")
        payload = json.loads(raw.decode("utf-8-sig"))
        from ..vision_import import build_workbooks
        result = build_workbooks(payload, bid_settings=_club_ai_context()[0])
        data = result["data"]
        source_token = _stage_source_files([
            ("transcription.json", raw, "original_upload"),
        ])
        data["pending_source_files"] = source_token
        return import_review(
            request, data, "", result.get("date", ""), "", {},
            result.get("date_precision", "unknown"),
            source_files=_staged_source_views(source_token),
        )
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _render_import_page(request, errors=[f"JSON-import: {exc}"])


@router.post("/import/ai", response_class=HTMLResponse)
async def import_ai(request: Request):
    form = await request.form()
    photo_token = ""

    try:
        uploads = form.getlist("photos")
        if any(getattr(upload, "filename", "") for upload in uploads):
            photos = await _read_photos(uploads)
            photo_token = _stage_photos(photos)
        else:
            photo_token = str(form.get("pending_photos", ""))
            photos = _staged_photos(photo_token)
        if not photos:
            raise ValueError("Vælg mindst ét foto")

        job_id = uuid4().hex
        AI_JOBS[job_id] = {
            "status": "running",
            "sequence": 0,
            "events": [],
            "photo_token": photo_token,
            "data": None,
        }
        _record_ai_progress(
            job_id, "photos", f"Gemte {len(photos)} foto til transskription."
        )
        # Read the club's rules and notes now; the background job runs outside this request.
        bid_settings, club_notes = _club_ai_context()
        asyncio.create_task(_run_ai_import_job(job_id, photos, photo_token, bid_settings, club_notes))
        return RedirectResponse(f"/import/ai/jobs/{job_id}", status_code=303)
    except (ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:
        return _render_import_page(
            request,
            errors=[str(exc)],
            active_tab="ai",
            pending_photos=photo_token,
            staged_photo_names=_staged_photo_names(photo_token),
        )


@router.get("/import/ai/jobs/{job_id}", response_class=HTMLResponse)
def ai_import_progress(request: Request, job_id: str):
    job = AI_JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    return render(
        request,
        "ai_import_progress.html",
        job_id=job_id,
        pending_photos=job["photo_token"],
        staged_photo_names=_staged_photo_names(job["photo_token"]),
    )


@router.get("/import/ai/jobs/{job_id}/events")
async def ai_import_events(request: Request, job_id: str):
    if job_id not in AI_JOBS:
        raise HTTPException(404)
    try:
        last_id = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last_id = 0

    async def stream_events():
        nonlocal last_id
        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()
        while True:
            job = AI_JOBS.get(job_id)
            if job is None:
                return
            for event in job["events"]:
                if event["id"] <= last_id:
                    continue
                yield f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                last_id = event["id"]
                last_heartbeat = loop.time()
            if job["status"] in ("complete", "failed") and last_id >= job["sequence"]:
                return
            if await request.is_disconnected():
                return
            if loop.time() - last_heartbeat >= 10:
                yield ": keep-alive\n\n"
                last_heartbeat = loop.time()
            await asyncio.sleep(0.35)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/import/ai/jobs/{job_id}/review", response_class=HTMLResponse)
def ai_import_review(request: Request, job_id: str):
    job = AI_JOBS.get(job_id)
    if not job:
        return resume_import(request, job_id)
    if job["status"] != "complete" or not job["data"]:
        return RedirectResponse(f"/import/ai/jobs/{job_id}", status_code=303)
    return import_review(
        request,
        job["data"],
        "",
        job["game_date"],
        "",
        {},
        job["date_precision"],
        source_workbooks=job.get("source_workbooks", []),
    )


@router.get("/import/ai/staged/{token}/{filename}")
def import_ai_artifact(token: str, filename: str):
    media_types = {
        "import.xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ai-import.xlsx",
        ),
        "transcription.json": ("application/json; charset=utf-8", "transcription.json"),
        "transcription.xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ai-transcription.xlsx",
        ),
        "remarks.md": ("text/markdown; charset=utf-8", "ai-remarks.md"),
        "source.zip": ("application/zip", "ai-import-with-photos.zip"),
    }
    if filename not in media_types or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(404)

    path = AI_ROOT / token / filename
    if not path.is_file():
        raise HTTPException(404)

    media_type, download_name = media_types[filename]
    return FileResponse(path, media_type=media_type, filename=download_name)


def _import_snapshot(data):
    return {
        "players": list(data.get("players", [])),
        "hands": [
            {
                key: copy.deepcopy(hand.get(key))
                for key in (
                    "source_id",
                    "number",
                    "bidder",
                    "bid",
                    "target",
                    "actual",
                    "partner",
                    "extra",
                    "scores",
                )
            }
            for hand in data.get("hands", [])
        ],
    }


def _short_value(value):
    text = str(value or "—").strip()
    return text[:60] + ("…" if len(text) > 60 else "")


def _import_change_descriptions(snapshot, data):
    descriptions = []
    original_players = snapshot.get("players", [])
    players = data.get("players", [])
    for old_name, new_name in zip(original_players, players):
        if old_name != new_name:
            descriptions.append(
                f"Renamed player {_short_value(old_name)} to {_short_value(new_name)}."
            )

    original_hands = snapshot.get("hands", [])
    current_hands = data.get("hands", [])
    original_by_id = {
        hand.get("source_id", f"original-{index}"): (index, hand)
        for index, hand in enumerate(original_hands)
    }
    current_by_id = {
        hand.get("source_id", f"original-{index}"): (index, hand)
        for index, hand in enumerate(current_hands)
    }
    original_order = list(original_by_id)
    current_order = list(current_by_id)
    for source_id, (index, hand) in original_by_id.items():
        if source_id not in current_by_id:
            descriptions.append(f"Deleted hand {hand.get('number', index + 1)}.")
    for source_id, (index, hand) in current_by_id.items():
        if source_id not in original_by_id:
            descriptions.append(f"Inserted hand {index + 1}.")
            continue

        old_index, original = original_by_id[source_id]
        changes = []
        for field, label in (
            ("bidder", "Melder"),
            ("bid", "Melding"),
            ("target", "Meldt"),
            ("actual", "Stik"),
            ("partner", "Makker"),
        ):
            before, after = original.get(field, ""), hand.get(field, "")
            if before != after:
                changes.append(
                    f"{label} {_short_value(before)} → {_short_value(after)}"
                )

        old_scores = original.get("scores", {})
        new_scores = hand.get("scores", {})
        score_changes = []
        for player_index, new_name in enumerate(players):
            old_name = (
                original_players[player_index]
                if player_index < len(original_players)
                else new_name
            )
            before = old_scores.get(old_name, "")
            after = new_scores.get(new_name, "")
            if before != after:
                score_changes.append(
                    f"{_short_value(new_name)} {_short_value(before)} → {_short_value(after)}"
                )
        if score_changes:
            changes.append("score: " + ", ".join(score_changes))
        if original.get("extra", []) != hand.get("extra", []):
            changes.append("ekstra meldere ændret")
        if changes:
            descriptions.append(
                f"Corrected hand {index + 1}: " + "; ".join(changes) + "."
            )
    original_survivors = [key for key in original_order if key in current_by_id]
    current_survivors = [key for key in current_order if key in original_by_id]
    if original_survivors != current_survivors:
        for source_id in current_survivors:
            old_position = original_survivors.index(source_id)
            new_position = current_survivors.index(source_id)
            if old_position != new_position:
                descriptions.append(
                    f"Moved hand from position {old_position + 1} to {new_position + 1}."
                )
    return descriptions


def import_review(
    request,
    data,
    title,
    game_date,
    replace_id,
    mapping,
    date_precision="unknown",
    source_workbooks=None,
    form_errors=None,
    player_form_values=None,
    source_files=None,
):
    for index, hand in enumerate(data["hands"], 1):
        hand.setdefault("number", index)
        hand.setdefault("source_id", f"import-{index}")
        hand.setdefault("source_notes", [])
        hand.setdefault("actual_explicit", bool(hand.get("actual")))
        hand.setdefault("partner_explicit", bool(hand.get("partner")))
    if "source_snapshot" not in data:
        data["source_snapshot"] = _import_snapshot(data)

    with connect() as db:
        members = [
            dict(r)
            for r in db.execute("SELECT id,name FROM club_members WHERE club_id=? ORDER BY name", (active_club_id(),))
        ]
        previous = [
            dict(r)
            for r in db.execute(
                "SELECT id,title FROM sessions WHERE origin='imported' AND club_id=? ORDER BY id DESC",
                (active_club_id(),),
            )
        ]
        rule_versions = rule_version_choices(db)

    # Read-only inferred fields must not constrain score-based review validation.
    for hand in data["hands"]:
        hand["actual"] = ""
        hand["partner"] = ""
        hand["actual_explicit"] = False
        hand["partner_explicit"] = False
    errors = validate(data)

    def math_valid(hand):
        try:
            values = [
                import_number(hand["scores"].get(name)) for name in data["players"]
            ]
            return all(value is not None for value in values) and sum(values) == 0
        except (ValueError, TypeError, ZeroDivisionError):
            return False

    math_ok = [math_valid(hand) for hand in data["hands"]]
    hand_valid = [math_ok[i] and not errors[i] for i in range(len(data["hands"]))]
    hand_field_issues = [
        field_issues(hand, data["players"], errors[index])
        for index, hand in enumerate(data["hands"])
    ]

    with connect() as db:
        auto_mapping = match_import_players(db, data["players"])
    auto_mapping.update(mapping)

    return render(
        request,
        "historical_import.html",
        members=members,
        previous=previous,
        data=data,
        issues=errors,
        math_ok=math_ok,
        hand_valid=hand_valid,
        hand_field_issues=hand_field_issues,
        errors=form_errors or [],
        title=title,
        game_date=game_date,
        date_precision=date_precision,
        replace_id=replace_id,
        rule_versions=rule_versions,
        # The commit route keeps the posted choice here when it shows the review again.
        rule_version=str(getattr(request.state, "rule_version", "")),
        mapping=auto_mapping,
        bid_options=list(registered_bids()),
        staged_photo_names=[
            p.name
            for p in sorted(
                (PHOTO_ROOT / "_staging" / data.get("pending_photos", "")).glob("*")
            )
            if p.is_file() and re.fullmatch(r"\d{2}\.(jpg|jpeg|png|webp)", p.name)
        ]
        if data.get("pending_photos")
        else [],
        active_tab="ai" if data.get("ai_token") else "excel",
        ai_artifacts=_ai_artifact_view(data.get("ai_token", "")),
        source_workbooks=source_workbooks or _source_workbooks_for_data(data),
        source_files=source_files
        or _staged_source_views(data.get("pending_source_files", "")),
        player_form_values=player_form_values or {},
    )


@router.post("/import/check-hand")
async def import_check_hand(request: Request):
    payload = await request.json()
    players = payload.get("players")
    hand = payload.get("hand")
    if (
        not isinstance(players, list)
        or len(players) != 4
        or len(set(players)) != 4
        or not isinstance(hand, dict)
    ):
        raise HTTPException(400, "Ugyldige hånddata")

    try:
        scores = hand["scores"]
        if set(scores) != set(players):
            raise ValueError("Ugyldige spillere")

        hand["scores"] = {name: str(scores[name]).strip() for name in players}
        if any(import_number(scores[name]) is None for name in players):
            raise ValueError("Score mangler")

        total = sum(import_number(hand["scores"][name]) for name in players)
        issues = (
            [] if total == 0 else [f"Scoreændringer summerer til {total} kr, ikke 0"]
        )
        # These fields are read-only and always reconstructed from the scores.
        hand["actual"] = ""
        hand["partner"] = ""

        # Keep score arithmetic separate from bid/contract validation for the review UI.
        contract_issues = validate({"players": players, "hands": [hand]})[0]
        return {
            "issues": issues,
            "contract_issues": contract_issues,
            "field_issues": field_issues(hand, players, issues + contract_issues),
            "hand": hand,
        }
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        issues = [f"Ugyldig score: {exc}"]
        return {
            "issues": issues,
            "field_issues": field_issues(hand, players, issues),
            "hand": hand,
        }


@router.post("/import/commit", response_class=HTMLResponse)
async def import_commit(request: Request):
    form = await request.form()
    request.state.rule_version = str(form.get("rule_version", ""))

    try:
        data = json.loads(str(form["data"]))
        original_names = list(data["players"])
        source_snapshot = data.get("source_snapshot") or _import_snapshot(data)
        names = [
            str(form.get(f"player_name_{i}", name)).strip()[:80]
            for i, name in enumerate(original_names)
        ]
        hands = data["hands"]
        if len(names) != 4 or len(hands) > 500 or not hands:
            return import_review(
                request,
                data,
                str(form.get("title", "")).strip()[:100],
                str(form.get("game_date", "")).strip(),
                str(form.get("replace_id", "")),
                {},
                str(form.get("date_precision", "unknown")),
                form_errors=["Angiv fire spillere og mindst én hånd (maks. 500)."],
                player_form_values={
                    f"player_name_{i}": str(form.get(f"player_name_{i}", name))
                    for i, name in enumerate(original_names)
                },
            )
        if (
            any(not name for name in names)
            or len({name.casefold() for name in names}) != 4
        ):
            return import_review(
                request,
                data,
                str(form.get("title", "")).strip()[:100],
                str(form.get("game_date", "")).strip(),
                str(form.get("replace_id", "")),
                {},
                str(form.get("date_precision", "unknown")),
                form_errors=["Angiv fire forskellige spillernavne."],
                player_form_values={
                    f"player_name_{i}": str(form.get(f"player_name_{i}", name))
                    for i, name in enumerate(original_names)
                },
            )

        for hand in hands:
            old_scores = hand.get("scores", {})
            hand["scores"] = {
                new_name: old_scores.get(new_name, old_scores.get(old_name, ""))
                for old_name, new_name in zip(original_names, names)
            }
        data["players"] = names

        for i, h in enumerate(hands):
            h["number"] = i + 1
            h["bidder"] = str(form.get(f"bidder_{i}", "")).strip()
            h["bid"] = str(form.get(f"bid_{i}", "")).strip()
            h["target"] = str(form.get(f"target_{i}", "")).strip()
            # Never trust posted values for read-only, inferred fields.
            h["actual"] = ""
            h["partner"] = ""
            h["actual_explicit"] = False
            h["partner_explicit"] = False
            h["extra"] = [
                str(v).strip() for v in form.getlist(f"extra_{i}") if str(v).strip()
            ]

            for j, name in enumerate(names):
                value = form.get(f"score_{i}_{j}")
                if value is not None:
                    h["scores"][name] = decimal_text(str(value).strip())

        title = str(form.get("title", "")).strip()[:100]
        game_date = str(form.get("game_date", "")).strip()
        date_precision = str(form.get("date_precision", "unknown"))
        try:
            timestamp, default_title = historical_date(game_date, date_precision)
        except ValueError as exc:
            return import_review(
                request,
                data,
                title,
                game_date,
                str(form.get("replace_id", "")),
                {},
                date_precision,
                form_errors=[str(exc)],
            )
        if not title:
            title = default_title

        mapping = {}
        with connect() as db:
            valid = {r[0] for r in db.execute("SELECT id FROM club_members WHERE club_id=?", (active_club_id(),))}
        for i, name in enumerate(names):
            value = str(form.get(f"member_{i}", "")).strip()
            mapping[name] = int(value) if value else None
            if mapping[name] is not None and mapping[name] not in valid:
                return import_review(
                    request,
                    data,
                    title,
                    game_date,
                    str(form.get("replace_id", "")),
                    mapping,
                    date_precision,
                    form_errors=["Ukendt klubmedlem; vælg et kendt medlem eller gæst."],
                )

        with connect() as db:
            for name, mid in mapping.items():
                if mid is None:
                    continue
                row = db.execute(
                    "SELECT id FROM club_members WHERE name=? COLLATE NOCASE AND club_id=?",
                    (name, active_club_id()),
                ).fetchone()
                alias = db.execute(
                    "SELECT member_id FROM club_member_aliases WHERE alias=? COLLATE NOCASE AND club_id=?",
                    (name, active_club_id()),
                ).fetchone()
                if (row and row["id"] != mid) or (alias and alias["member_id"] != mid):
                    return import_review(
                        request,
                        data,
                        title,
                        game_date,
                        str(form.get("replace_id", "")),
                        mapping,
                        date_precision,
                        form_errors=[
                            f"Navnet {name} er allerede knyttet til et andet klubmedlem."
                        ],
                    )
        mids = [x for x in mapping.values() if x is not None]
        if len(mids) != len(set(mids)):
            return import_review(
                request,
                data,
                title,
                game_date,
                str(form.get("replace_id", "")),
                mapping,
                date_precision,
                form_errors=["To spillere er knyttet til samme klubmedlem."],
            )

        replace_id = int(form.get("replace_id") or 0) or None
        problems = validate(data)
        if any(problems):
            return import_review(
                request,
                data,
                title,
                game_date,
                replace_id or "",
                mapping,
                date_precision,
                form_errors=[
                    f"Hånd {index + 1}: {'; '.join(problem_list)}"
                    for index, problem_list in enumerate(problems)
                    if problem_list
                ],
            )

        change_descriptions = _import_change_descriptions(source_snapshot, data)
        permanent_sources = _staged_source_files(data.get("pending_source_files", ""))
        permanent_sources.extend(_ai_source_files(data.get("ai_token", "")))
        with connect() as db:
            version = import_rule_version(form.get("rule_version"))
            if replace_id:
                # Saved over an existing night: what it charged before is compared with now.
                with kasse.watch(db, replace_id, "import"):
                    sid = save_import(db, data, title, timestamp, mapping, replace_id, version)
                    _set_import_money(db, sid, bool(form.get("collect_club_money")))
            else:
                sid = save_import(db, data, title, timestamp, mapping, replace_id, version)
                _set_import_money(db, sid, bool(form.get("collect_club_money")))
                kasse.add_alert(db, sid, "import", [], kasse.snapshot(db, sid))
            _save_photos(db, sid, _staged_photos(data.get("pending_photos", "")))
            db.execute(
                "UPDATE sessions SET date_precision=? WHERE id=?", (date_precision, sid)
            )
            for name, mid in mapping.items():
                if mid is not None:
                    existing = db.execute(
                        "SELECT member_id FROM club_member_aliases WHERE alias=? COLLATE NOCASE AND club_id=?",
                        (name, active_club_id()),
                    ).fetchone()
                    # A name that is already the member's own name needs no alias.
                    own_name = db.execute(
                        "SELECT 1 FROM club_members WHERE id=? AND name=? COLLATE NOCASE", (mid, name)
                    ).fetchone()
                    if existing is None and own_name is None:
                        db.execute(
                            "INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,?,?)",
                            (mid, active_club_id(), name),
                        )
            db.executemany(
                "INSERT INTO evening_log(evening_id,user_id,description) VALUES (?,?,?)",
                [
                    (sid, str(request.session.get("user", "admin")), description)
                    for description in change_descriptions
                ],
            )
            _save_source_files(db, sid, permanent_sources)
        token = data.get("pending_photos", "")
        if token:
            shutil.rmtree(PHOTO_ROOT / "_staging" / token, ignore_errors=True)
        _remove_staged_source_files(data.get("pending_source_files", ""))
        _remove_ai_artifacts(data.get("ai_token", ""))
        job_id = data.get("ai_job_id", "")
        if re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
            _draft_path(job_id).unlink(missing_ok=True)
        if re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
            AI_JOBS.pop(str(job_id), None)

        return RedirectResponse(f"/sessions/{sid}", status_code=303)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Import afvist: {exc}")


@router.post("/import/final", response_class=HTMLResponse)
async def import_final(request: Request):
    # Store only known final scores; never fabricate bids.
    form = await request.form()
    title = str(form.get("title", "")).strip()[:100]
    game_date = str(form.get("game_date", "")).strip()
    date_precision = str(form.get("date_precision", "day"))
    try:
        timestamp, default_title = historical_date(game_date, date_precision)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not title:
        title = default_title

    names = [str(form.get(f"name_{i}", "")).strip()[:80] for i in range(4)]
    if any(not n for n in names) or len(set(n.casefold() for n in names)) != 4:
        raise HTTPException(400, "Angiv fire forskellige spillere")

    try:
        values = [Fraction(str(form[f"score_{i}"]).replace(",", ".")) for i in range(4)]
        if sum(values, Fraction()):
            raise ValueError("Resultaterne skal summere til nul")
    except (ValueError, KeyError, ZeroDivisionError) as exc:
        raise HTTPException(400, f"Ugyldige resultater: {exc}")

    with connect() as db:
        members = {r[0] for r in db.execute("SELECT id FROM club_members WHERE club_id=?", (active_club_id(),))}
        mapping = []
        for i in range(4):
            mid = int(form.get(f"member_{i}") or 0) or None
            if mid is not None and mid not in members:
                raise HTTPException(400, "Ukendt klubmedlem")
            mapping.append(mid)
        if len([x for x in mapping if x]) != len(set(x for x in mapping if x)):
            raise HTTPException(400, "Dublerede klubmedlemmer")

        sid = db.execute(
            """INSERT INTO sessions(title,created_at,ended_at,origin,is_club,collect_club_money,club_id,
              server_version,server_commit) VALUES (?,?,?,'imported',?,0,?,?,?)""",
            (title, timestamp, timestamp, int(all(mapping)), active_club_id(), *build_stamp()),
        ).lastrowid
        try:
            stamp_night(db, sid, import_rule_version(form.get("rule_version")))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        db.execute(
            "UPDATE sessions SET date_precision=? WHERE id=?", (date_precision, sid)
        )
        for i, (name, score) in enumerate(zip(names, values)):
            pid = db.execute(
                "INSERT INTO players(session_id,name,seat_order,member_id) VALUES (?,?,?,?)",
                (sid, name, i, mapping[i]),
            ).lastrowid
            db.execute(
                "INSERT INTO historical_final_scores(session_id,player_id,numerator,denominator) VALUES (?,?,?,?)",
                (sid, pid, score.numerator, score.denominator),
            )

        db.execute("UPDATE sessions SET is_club=? WHERE id=?", (int(is_club_night(db, sid)), sid))
        _set_import_money(db, sid, bool(form.get("collect_club_money")))
        kasse.add_alert(db, sid, "import", [], kasse.snapshot(db, sid))
    return RedirectResponse(f"/sessions/{sid}/live", status_code=303)


@router.get("/sessions/{sid}/photos/{photo_id}")
def session_photo(sid: int, photo_id: int):
    with connect() as db:
        row = db.execute(
            "SELECT stored_name FROM session_photos WHERE id=? AND session_id=?",
            (photo_id, sid),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Foto findes ikke")
    path = PHOTO_ROOT / str(sid) / row["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Foto findes ikke")
    return FileResponse(
        path,
        media_type=PHOTO_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/sessions/{sid}/source-files/{file_id}")
def session_source_file(sid: int, file_id: int):
    with connect() as db:
        row = db.execute(
            "SELECT stored_name,original_name,media_type FROM session_source_files WHERE id=? AND session_id=?",
            (file_id, sid),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Kildefil findes ikke")
    path = SOURCE_ROOT / str(sid) / row["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Kildefil findes ikke")
    return FileResponse(
        path,
        media_type=row["media_type"],
        filename=row["original_name"],
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/sessions/{sid}/photos")
async def add_session_photos(request: Request, sid: int):
    form = await request.form()
    photos = await _read_photos(form.getlist("photos"))

    with connect() as db:
        if not db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404)
        _save_photos(db, sid, photos)
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/photos/{photo_id}/delete")
def remove_session_photo(sid: int, photo_id: int):
    with connect() as db:
        row = db.execute(
            "SELECT stored_name FROM session_photos WHERE id=? AND session_id=?",
            (photo_id, sid),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        db.execute(
            "DELETE FROM session_photos WHERE id=? AND session_id=?", (photo_id, sid)
        )

    (PHOTO_ROOT / str(sid) / row["stored_name"]).unlink(missing_ok=True)
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/money")
async def update_session_money(request: Request, sid: int):
    form = await request.form()
    enabled = bool(form.get("collect_club_money"))

    with connect() as db:
        session = db.execute(
            "SELECT origin,is_club FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        if session["origin"] != "imported":
            raise HTTPException(400, "Kun historiske importer kan ændres her")
        try:
            with kasse.watch(db, sid, "money"):
                _set_import_money(db, sid, enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.get("/import/staged/{token}/{filename}")
def staged_import_photo(token: str, filename: str):
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not re.fullmatch(
        r"\d{2}\.(jpg|jpeg|png|webp)", filename
    ):
        raise HTTPException(404)
    path = PHOTO_ROOT / "_staging" / token / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type=PHOTO_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/import/source-staged/{token}/{filename}")
def staged_import_source(token: str, filename: str):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(404)
    item = next(
        (
            source
            for source in _staged_source_manifest(token)
            if source["stored_name"] == filename
        ),
        None,
    )
    if not item:
        raise HTTPException(404)
    path = SOURCE_ROOT / "_staging" / token / filename
    return FileResponse(
        path,
        media_type=item["media_type"],
        filename=item["original_name"],
        headers={"Cache-Control": "private, no-store"},
    )
