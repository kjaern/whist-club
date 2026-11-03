# AGENTS.md

## Commands
- All tests: `uv run --extra test pytest -q` (uses an isolated temp `WHIST_HOME`; no services or prebuilt DB needed).
- One test: `uv run --extra test pytest tests/test_finance_v19.py::test_settlement_order_and_ties -q`
- Lint: `uvx ruff check src tests` (no typecheck configured).
- Build: `uv build` (hatchling; package is `src/whistapp`).
- Run from source: set `WHIST_HOME` to a scratch directory, then `uv run whistapp init --username admin`, then `uv run whistapp serve`. Without `WHIST_HOME`, the current directory is used.
- Container: `docker compose up -d --build whist`; tunnel profile: `docker compose --profile tunnel up -d --build`.

## Architecture and execution
- Single package `src/whistapp`; CLI entrypoint `whistapp.cli:main`; ASGI app `whistapp.main:app`. Startup lifespan calls `init_db()`.
- `main.py` holds only the app, the auth/club middleware and router wiring. Routes live in `routes/` (`accounts`, `clubs`, `sessions`, `club`, `imports`), each an `APIRouter`; shared helpers (templates, `render`, permission checks, `view_data`) live in `web.py`. When adding a route, also check the middleware's path rules in `main.py`.
- FastAPI + server-rendered Jinja/HTMX. Templates and static files are inside `src/whistapp/`; there is no JS build step. Page mutations generally return full or partial HTML, not JSON.
- SSE editor updates at `/sessions/{sid}/editor/events`: the server polls `_results_signature()` every 1.5s; there is no pub/sub layer.
- Runtime home is `WHIST_HOME` or the current directory, containing `config/` and `data/`; `WHIST_DB` overrides the SQLite path. `settings.py` and `database.DB_PATH` resolve at import time, so set env vars before importing the app. `tests/conftest.py` does this deliberately.
- No migration framework. `init_db()` uses additive `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE` checks, and seeds initial club data. Preserve existing IDs and rows; schema changes need explicit migration handling.
- Use `with connect() as db:` for transactional writes. Hand scores and settlement amounts use `Fraction` persisted as numerator/denominator; do not introduce floats for scoring.
- Bid logic is authoritative in `src/whistapp/bids/` and `bids/registry.py`. `config/rules.yaml` is obsolete and not read. Register new bids and update display/import mappings where needed. A bid's club-configurable values are its `options` (name -> (Danish label, default)); `score()` reads them from `config` via `option_values()`. Clubs store enabled bids and values in `club_settings.bid_settings`, plus `money_mode`/`point_rate`; a night snapshots these in `sessions` at creation (NULL = original rules and placement payment).
- Multiple clubs: `clubs` table; club 1 is the original club. `club_members`, `sessions` and `club_member_aliases` carry `club_id`; `club_settings.id` is the club id. A login (`user_accounts`) has at most one member per club via `member_accounts`. The active club is chosen per login session (`/clubs` picker, auto-selected when there is only one) and exposed via `club.active_club_id()`; the middleware 404s night/member URLs from other clubs. Scope every new club-data query by the active club. Active members of club 1 may create clubs.
- Club identity is `club_members.id` via `players.member_id`; `players.club_id` is legacy migration data. Runtime members and rates live in `club_members`/`club_settings` and are edited via `/club`; `config/club.ini` only seeds initial data, so editing it does not rename existing members.
- Auth middleware protects everything except `/login`, `/logout`, and `/static`. SSE returns 401 instead of redirecting. Mutating non-GET requests also require same-origin. Do not weaken these checks.
- Historical import treats recorded per-hand score deltas as authoritative; never invent bids or tricks. The importer expects one worksheet. See `docs/historical-import.md`.

## Conventions and gotchas
- `config/`, `data/`, and `.env` are gitignored and may contain secrets or real data. Never commit them or copy `secrets.ini`.
- Tests set up admin/`a$$word` in a temporary home before the app imports; preserve that import order in `tests/conftest.py`.
- Danish user-facing text is intentional; preserve it unless the task explicitly changes wording.
- Compact style (one-line `if`, semicolons) is intentional. Ruff ignores `E701`, `E702`, and `E402`; do not mass-reformat unrelated code.
- No CI is configured; local tests and ruff are the verification path.
- Production deployment is Docker Compose + Cloudflare Tunnel: UID/GID `10001`, read-only config, writable data. See `DEPLOY.md` before changing runtime paths or startup behavior.


## Import workflows and behavioral contracts
- Preserve existing user-visible behavior when refactoring. Do not replace a working workflow with a technically valid but behaviorally different implementation.
- Before changing import behavior, inspect the relevant existing tests, Git history, routes, and templates. Prefer minimal changes over rewriting working components.
- There are two import sources: structured Excel files and LLM transcription of photographs. Both should lead to a shared review/correction workflow.
- The structured Excel format uses a single worksheet named `Hænder`, with `Hånd`, `Melder`, `Melding`, `Meldt`, `Stik`, `Makker`, player score changes and running totals. Optional `Melder 2` and `Melder 3` columns belong at the far right.
- The LLM's responsibility is literal transcription of the photograph, not interpretation, scoring, correction, or inventing missing results. The importer owns interpretation and validation.
- The vision prompt is the two files in `src/whistapp/prompts/`, the active club's bid definitions with its configured values, and the club's `club_settings.ai_notes` (edited by the server admin on `/admin/ai`, together with the provider, the request limits and which clubs may use AI import). Keep the prompt files generic: no real member names or details from real nights. Club-specific context belongs in the club's AI notes in the database. Design notes are in `docs/historical-import.md`.
- Imported per-hand monetary changes are authoritative. Preserve decimal values in Excel; use the application's existing `Fraction` representation internally.
- Import validation must distinguish between an invalid hand and an uncertain individual value. Hand numbers are green when validation passes and red otherwise; never yellow. Uncertain or problematic individual cells are yellow, even when the hand is valid.
- Arithmetic inconsistencies should be reviewable and correctable rather than immediately aborting an entire import.
- The review interface should allow editing, inserting, deleting, and reordering hands, followed by revalidation.
- Preserve original uploaded photographs, source Excel files, raw transcription output, and remarks alongside the imported evening, including after it is saved.
- Do not silently repair historical scores or reinterpret handwritten values merely to make totals agree.
- Do not change authentication, club permissions, player identities, saved evenings, or existing statistics as a side effect of importer work.
- Add regression tests for behavior being restored or changed, not only for successful parsing.
- When a requested change conflicts with an existing behavior or requirement, explain the conflict before implementing a different workflow.
