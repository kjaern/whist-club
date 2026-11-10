# Raspberry Pi deployment: Docker Compose + Cloudflare Tunnel

Whist runs in the `whist` container on the private `whist_private` Docker network.
The `tunnel` service connects Cloudflare to `http://whist:8000`. Neither service
publishes a host port, and Caddy is not needed for this deployment.

Run the following commands from the Whist repository directory on the Pi.

## Initial setup

Create the host directories, give them to the container's UID/GID `10001`,
and initialize the application config with a unique admin password from
inside the container:

```sh
mkdir -p config data
sudo chown 10001:10001 config data
sudo chmod 750 config data
docker compose build whist
docker compose run --rm whist whistapp init --username admin
```

Initialization creates `config/config.ini`, `config/club.ini`, and
`config/secrets.ini` (owner-only). Do not rerun initialization against existing
credentials: it intentionally refuses to overwrite `secrets.ini`. The
container needs write access to `data/`, including SQLite journal files.

AI photo import is configured in the app, under **Serveradministration →
AI-fotoimport** (`/admin/ai`): provider, model and key, the limits per import,
and which clubs may use it. Those settings are saved in the writable
`data/llm.ini` with owner-only permissions; the API key is never shown after
saving. Uploaded score-sheet images are sent to the configured provider.

If the Pi uses a different ownership arrangement, set `WHIST_UID` and
`WHIST_GID` in `.env` instead and adjust directory permissions accordingly.
The files in `config` must be readable by the configured container UID/GID. Do not change the permissions of secrets
to `644` just to make the container start.

For HTTPS access through Cloudflare, set `secure_cookies = true` in
`config/config.ini`.

## Cloudflare Tunnel

Create a named Cloudflare Tunnel and configure its public hostname to point to
`http://whist:8000`. Put its token in a private `.env` file in the repository:

```dotenv
TUNNEL_TOKEN=your-tunnel-token
```

The tunnel service is under the `tunnel` Compose profile. Start both services:

```sh
docker compose --profile tunnel up -d --build
docker compose --profile tunnel ps
```

The application is not reachable through a published Pi port. For local
development on a PC, use `whistapp serve --host 0.0.0.0` instead.

## Updating the Pi

Pull the desired application revision, then rebuild the Whist service without
replacing the persistent `config/` or `data/` directories:

```sh
git pull
docker compose up -d --build whist
docker compose logs --tail=100 whist
```

The running tunnel does not need rebuilding for ordinary application changes.
To start it on a fresh deployment, use `--profile tunnel` as above.

## Stamping nights created before version tracking

Each night records the server version and commit that created it, shown on the
night's page. Nights created before this existed have no stamp. Starting the
new build adds the columns; stamping an old night is a manual, one-time step,
because only you know which build it was played on.

Back up first (the online backup is safe while the server runs):

```sh
docker compose exec whist python -c "import sqlite3; s=sqlite3.connect('/var/lib/whist/data/whist.sqlite3'); d=sqlite3.connect('/var/lib/whist/data/whist-backup-before-stamp.sqlite3'); s.backup(d); d.close()"
```

List the nights, then stamp the right one by its id (it refuses to overwrite an
existing stamp unless `--force` is given):

```sh
docker compose exec whist whistapp stamp-night --list
docker compose exec whist whistapp stamp-night --night <id> --version 1.0.0 --commit 338dfb5
```

## One-off migrations

Starting a new build only adds missing tables and columns. Changes that rewrite or
remove existing data are separate, one-off migrations that you run by hand. List
them and their state:

```sh
docker compose exec whist whistapp migrate
```

Run one by name. It refuses if its preconditions are not met, and it first saves a
copy of the database next to it (`data/whist-before-<name>-<time>.sqlite3`):

```sh
docker compose exec whist whistapp migrate drop-night-rule-columns
```

### kasse-post-history (Klubkasse: Bogført and Afventer)

Since the Klubkasse splits each saldo into Bogført and Afventer, a night's charges are posted
12 hours after it was last closed. The app does this by itself for live nights. Imported nights
saved before this release have no record of when they were saved, so they stay in Afventer
until you run:

```sh
docker compose exec whist whistapp migrate kasse-post-history
```

It posts each such night once, with the amounts the old saldo already counted, so every member's
I alt stays the same. Nights it cannot classify (fx an unreadable date) are listed, not posted, and
stay in Afventer. Running it again posts nothing twice.

### Removing a club

There is no club deletion in the app. If a test club is ever removed by hand, its Klubkasse rows
(`kasse_entries`, `kasse_night_postings`, `kasse_night_charges`, `kasse_buckets`, `kasse_log`,
`kasse_alerts`) belong to it and must be removed with it, after checking it holds no real money.
`kasse_entries` and `kasse_night_charges` reference `club_members`, so delete those rows first.

## Reset the test database

**Destructive:** this removes all game nights, hands, scores, settlements, and
other data stored in SQLite. Only use this while the database contains test
data. It does not remove the admin credentials or club configuration.

Stop the app first, then remove the SQLite database and its journal files from
the bind-mounted host `data/` directory. The database filename is
`whist.sqlite3` by default (unless `WHIST_DB` was overridden):

```sh
docker compose stop whist
rm -f -- data/whist.sqlite3 data/whist.sqlite3-wal data/whist.sqlite3-shm
docker compose up -d whist
docker compose logs --tail=50 whist
```

**Do not delete or recreate `data/` itself.** Its ownership and permissions
are important: the container runs as an unprivileged user. On startup,
Whist's `init_db()` creates the database tables again.

If the app fails with `attempt to write a readonly database` or
`unable to open database file`, check the directory owner and the effective
container UID/GID before changing permissions.

## Backups before real games

Once the database contains real games, stop using the reset procedure.
Back up `config/` (especially `secrets.ini`) and `data/` securely before
deployments that change the schema. SQLite may use WAL files, so do not
copy only the `.sqlite3` file while the application is writing to it.
A simple consistent backup is to stop `whist` before copying the database
and its companion files; alternatively use SQLite's online backup mechanism.
Store backups outside the repository and restrict access to secrets.

The application currently performs additive schema initialization at startup.
Before production data needs preservation across incompatible schema changes,
introduce explicit, tested database migrations.
