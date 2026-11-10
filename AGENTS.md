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
- `init_db()` only creates what is missing (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` checks) and seeds a brand-new database, including the first club's first rule version. It never rewrites or removes existing data. Anything that does is a one-off migration in `src/whistapp/migrations/`, run by hand with `whistapp migrate <name>` (it backs the database up first and refuses unsafe states); app code and `init_db()` never import it. Preserve existing IDs and rows.
- Use `with connect() as db:` for transactional writes. Hand scores and settlement amounts use `Fraction` persisted as numerator/denominator; do not introduce floats for scoring.
- Bid logic is authoritative in `src/whistapp/bids/` and `bids/registry.py`. `config/rules.yaml` is obsolete and not read. Register new bids and update display/import mappings where needed. A bid's club-configurable values are its `options` (name -> (Danish label, default)); `score()` reads them from `config` via `option_values()`. Clubs store enabled bids and values in `club_settings.bid_settings`, plus `money_mode`/`point_rate`, `placements` and `fejl40`. These with the active members form a rule version (`club_rule_versions`, `rule_versions.py`): a club's newest version is its active one, versions never change, and only saving `/club` (or founding a club) makes a new one. Every night points to its version via `sessions.rule_version_id` and reads its rules from it with `night_rules()`; creating or importing a night never makes a version.
- Multiple clubs: `clubs` table; club 1 is the original club. `club_members`, `sessions` and `club_member_aliases` carry `club_id`; `club_settings.id` is the club id. A login (`user_accounts`) has at most one member per club via `member_accounts`. The active club is chosen per login session (`/clubs` picker, auto-selected when there is only one) and exposed via `club.active_club_id()`; the middleware 404s night/member URLs from other clubs. Scope every new club-data query by the active club. Active members of club 1 may create clubs.
- Club identity is `club_members.id` via `players.member_id`; `players.club_id` is legacy migration data. Runtime members and rates live in `club_members`/`club_settings` and are edited via `/club`; `config/club.ini` only seeds initial data, so editing it does not rename existing members.
- Auth middleware protects everything except `/login`, `/logout`, and `/static`. SSE returns 401 instead of redirecting. Mutating non-GET requests also require same-origin and the login session's CSRF token (`auth.verify_csrf`, an app-wide dependency): every `method="post"` form renders `{{ csrf_field(request) }}`, and htmx/fetch send the `X-CSRF-Token` header from `base.html`. Tests get the header automatically from `tests/conftest.py`; send `X-Test-No-Csrf` to opt out. Do not weaken these checks.
- Klubkasse (`kasse/`): a saldo is negative when the member owes the club. Bogført = entries + posted nights (`kasse_night_postings`/`kasse_night_charges`, written once and never changed); Afventer = open nights and closed nights not yet posted (`aftener.pending_nights`); I alt = both. A night is posted `REOPEN_HOURS` after it was last closed (live: `ended_at`; imported: `saved_at`) by `ledger.finalize_due`, which pages showing balances call in their own `BEGIN IMMEDIATE`; there are no timers. Nights never change the club's cash. An entry kind no file defines raises `UnknownEntryType`.
- Historical import treats recorded per-hand score deltas as authoritative; never invent bids or tricks. The importer expects one worksheet. See `docs/historical-import.md`.
- Statistics live in `src/whistapp/stats/`: `data.load(db, Scope)` loads the club's lifetime, a year or a night once; each statistic is a `Statistic` in its own file returning a `Table`, listed under a topic tab in `stats/registry.py`, and `partials/stat_table.html` renders every table on `/statistics`, `/statistics/{year}` and `/sessions/{sid}/statistics`. Statistics never query the database. `statistics.club_statistics()` is a compatibility wrapper over the Overblik rows.

## Conventions and gotchas
- `config/`, `data/`, and `.env` are gitignored and may contain secrets or real data. Never commit them or copy `secrets.ini`.
- Tests set up admin/`a$$word` in a temporary home before the app imports; preserve that import order in `tests/conftest.py`.
- Danish user-facing text is intentional; preserve it unless the task explicitly changes wording.
- Compact style (one-line `if`, semicolons) is intentional. Ruff ignores `E701`, `E702`, and `E402`; do not mass-reformat unrelated code.
- CI (`.github/workflows/ci.yml`) runs the tests on Python 3.11 and 3.13 and ruff on every push and pull request. Run them locally too before committing.
- Production deployment is Docker Compose + Cloudflare Tunnel: UID/GID `10001`, read-only config, writable data. See `DEPLOY.md` before changing runtime paths or startup behavior.

## Before merging a branch
Review the branch against `main` locally with `/code-review` (there is no PR bot). Besides correctness, check what is easy to get wrong here:
- Scores, totals and money stay `Fraction` (stored as numerator/denominator), never float: ties and zero sums decide settlements. Floats are fine only for display, such as percentages and averages in statistics.
- Every query on club data is scoped by `active_club_id()`; new night or member URLs are covered by the middleware's club check.
- New routes are checked against the path rules in `main.py` and `member_operation_allowed`/`member_read_allowed` in `web.py`; auth and same-origin checks are not weakened.
- Schema changes are additive in `init_db()`, safe to run twice, and keep existing ids and rows.
- A night's rules come from its rule version (`night_rules`), never from today's `club_settings`; only display text such as member names and short bid names is read live.
- Writes that must agree with what the user saw use `BEGIN IMMEDIATE` and the night state token.
- Danish user-facing text is preserved, the repo stays LF, and changed behavior has a regression test.


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
