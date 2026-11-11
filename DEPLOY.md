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

### First update with CSRF tokens

From this release every change needs the login session's security token, which pages opened
before the update do not have. Update when no night is being played, or tell players to reload
every open page afterwards. A change sent from an old page is refused with "Sikkerhedsnøglen
mangler eller er forældet. Genindlæs siden, og prøv igen." and nothing is saved: reload the page
and enter it again (fx the hand that was being typed). Logging in again also gives a new token,
after which other open tabs in the same browser need a reload too.

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

Run one by name. It refuses if its preconditions are not met, and it first makes a
backup in the backup folder (`whist-migration-<time>.sqlite3`, listed as "Før migrering"
under Serveradministration → Backup). If that backup cannot be written, nothing is changed:

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
I alt stays the same. Nights it cannot classify or work out (fx an unreadable date or no rule
version) are listed, not posted, and stay in Afventer; while such a night is left, the migration
shows as needed and lists it again on every run. Running it again posts nothing twice.

### backfill-dealers (Giver and rounds on early nights)

Live nights saved before 7 Nov 2026 have no dealer, so they show no Giver and no round lines.
This fills them in with the app's dealer rule: the first seat deals the first hand, and the deal
passes to the next player at the table after the last dealer:

```sh
docker compose exec whist whistapp migrate backfill-dealers
```

Imported nights are left alone, and so are nights where only some hands lack a dealer; those
are listed. Running it again changes nothing.

The first version of `backfill-dealers` (commit 3b9d2a2) counted the dealer from the first seat
on every hand, which goes wrong once a player has left. If you ran that version, correct those
nights with:

```sh
docker compose exec whist whistapp migrate fix-backfilled-dealers
```

It only changes early nights whose dealers are exactly what that version wrote.

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

## Database backups in the app

**Serveradministration → Backup** (`/admin/backups`) makes a backup of the
database on demand, lists the backups, and downloads either a listed backup or
a fresh copy of the live database. A monthly backup
(`whist-monthly-YYYY-MM.sqlite3`) is made automatically: the server checks at
startup and every hour, so a month whose backup is missing gets one as soon as
the server runs. Backups use SQLite's online backup, so they are safe while the
server runs, and the app never deletes or rewrites one; remove old ones by hand.

Backups go in their own bind mount, `/var/lib/whist/backups`, which defaults to
`./backups` next to `compose.yaml`. To keep them on an external disk, set the
host path in `.env`:

```sh
WHIST_BACKUP_PATH=/mnt/external/whist-backups
```

Create the folder and give it to the container's UID/GID before starting:

```sh
mkdir -p /mnt/external/whist-backups
sudo chown 10001:10001 /mnt/external/whist-backups
sudo chmod 750 /mnt/external/whist-backups
docker compose up -d whist
```

A disk formatted as exFAT or NTFS has no Unix owners, so `chown` does nothing
there; mount it with `uid=10001,gid=10001` instead (for example in
`/etc/fstab`). Make sure the disk is mounted before Docker starts, or the
backups land on the SD card under the empty mount point. If the folder is not
writable, the backup page shows the error. Outside Docker the folder is
`WHIST_BACKUP_DIR` (a relative path is inside `WHIST_HOME`), or `backups/` in
`WHIST_HOME`. Two backups made in the same second get `-2`, `-3` ... added to the
name.

A backup contains every club, night, login and password hash. It does not
contain `config/` (`secrets.ini`) or `data/llm.ini`; back those up separately.

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
