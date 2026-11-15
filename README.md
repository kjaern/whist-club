# Whist

A small web app for keeping score at our whist club's game nights: hands, settlement, statistics and import of old score sheets. The code is public so club members can see what the app they use actually runs. The interface is in Danish.

FastAPI, HTMX 2, SQLite, SSE (database change checks), Excel export. Python 3.11+.

## Start it

From the app's folder, once it has been [set up](#run-it-on-your-own-computer):

```bash
uv run whistapp serve
```

Then open `http://localhost:8000/login`. Add `--host 0.0.0.0` to let other devices on the network in, and `--port 8001` to use another port. Stop it with Ctrl+C.

While editing the code, this restarts the app whenever a file changes:

```bash
uv run uvicorn whistapp.main:app --reload
```

## Get the code

With git:

```bash
git clone https://github.com/kjaern/whist-club.git
cd whist-club
```

Without git, use **Code → Download ZIP** on GitHub and unpack it. That is enough to run it on your own computer; the Docker setup below needs a git clone.

## Run it on your own computer

The app needs Python. The easiest way is [uv](https://docs.astral.sh/uv/), a small tool that fetches the right Python version and the app's packages by itself.

Install uv once. On Windows, in PowerShell:

```powershell
winget install --id astral-sh.uv -e
```

On macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen the terminal, then run this once from the app's folder:

```bash
uv run whistapp init --username admin
```

It asks for a password for the server administrator, `admin`, and creates two folders: `config/` (settings and secrets) and `data/` (the database). Both are gitignored; never commit or share them.

Now start the app:

```bash
uv run whistapp serve --host 0.0.0.0
```

Open `http://localhost:8000/login` and log in as `admin`. Other devices on the same network can use `http://<this computer's IP>:8000`.

The app keeps `config/` and `data/` in the folder you start it from. To keep them somewhere else, set the environment variable `WHIST_HOME` to that folder before both commands.

If you already have Python 3.11 or newer and prefer not to use uv, use a virtual environment instead:

```bash
python -m venv .venv
.venv/bin/pip install .
.venv/bin/whistapp init --username admin
.venv/bin/whistapp serve --host 0.0.0.0
```

On Windows, use `.venv\Scripts\` instead of `.venv/bin/`.

## Run it with Docker Compose

For a server that is always on, for example a Raspberry Pi. You need Docker with the Compose plugin, plus a git clone of the repository. Run everything from the repository folder.

1. Create the two folders the app keeps its files in, and give them to the user the container runs as (UID 10001):

   ```bash
   mkdir -p config data
   sudo chown 10001:10001 config data
   sudo chmod 750 config data
   ```

2. Build the app and create the server administrator. It asks for the password:

   ```bash
   docker compose build whist
   docker compose run --rm whist whistapp init --username admin
   ```

3. Make it reachable. By default the container is only reachable inside Docker. To open it on your local network, create a file `compose.override.yaml` next to `compose.yaml`:

   ```yaml
   services:
     whist:
       ports:
         - "8000:8000"
   ```

4. Start it, then open `http://<the server's IP>:8000/login`:

   ```bash
   docker compose up -d
   ```

5. To update later:

   ```bash
   git pull
   docker compose up -d --build whist
   ```

Back up the `data/` folder before updates. That is where all the game nights are.

Putting it on the internet through a Cloudflare Tunnel, and where backups go, is described in [DEPLOY.md](DEPLOY.md).

## Configuration

Everything day to day is set in the app:

- **Klubadministration**: clubs, members, logins, aliases, money rules and which bids the club plays (club administrators);
- **Serveradministration → AI-fotoimport**: the AI provider, the limits per import, which clubs may use AI photo import, and each club's AI notes (server administrator only, since AI import uses the server's API key);
- **Serveradministration → Backup**: copies of the database (server administrator only).

The only setting you edit by hand is `secure_cookies = true` in `config/config.ini`, once the app runs behind HTTPS. Don't put it on the internet without HTTPS in front of it.

## AI photo import

Photos uploaded on the **AI-fotoimport** tab are sent to the configured OpenAI-compatible vision provider. The model only transcribes; the importer validates, and nothing is saved until someone has reviewed the hands. The original photos and the raw transcription are kept with the imported evening.

## Commands

All are run as `uv run whistapp <command>` (in Docker: `docker compose run --rm whist whistapp <command>`).

| Command | What it does |
| --- | --- |
| `init --username admin` | Creates `config/` and `data/` and the server administrator. Only once. |
| `serve [--host 0.0.0.0] [--port 8000]` | Starts the app. |
| `set-password` | Sets a new password for the server administrator. |
| `migrate [name]` | Lists the one-off data migrations, or runs one. It backs the database up first. |
| `stamp-night --list` | Lists the nights and the server version each was played on. |

## Development

- Tests: `uv run --extra test pytest -q`
- Lint: `uvx ruff check src tests`
- Bid rules are the Python classes in `src/whistapp/bids/`; the **Vis regler** dialog shows the actual scoring code.
- More notes for contributors and coding assistants are in [AGENTS.md](AGENTS.md).

## How it was made

Most of the code was written by AI coding assistants, from my instructions. Read it with that in mind.

## License and support

MIT licensed, see [LICENSE](LICENSE). The app is made for our own club; there is no support or maintenance for other users.
