# Whist tasks: run `just` to list them. Without just installed: `uvx --from rust-just just <task>`.
# Same commands as AGENTS.md. The app uses WHIST_HOME, or this folder when it is not set.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List the tasks
default:
    @{{just_executable()}} --list --justfile "{{justfile()}}"

# All tests, or pass pytest arguments: just test tests/test_kasse.py -x
test *args:
    uv run --extra test pytest -q {{args}}

# The Hypothesis laws only (zero-sum hands, nights and settlement)
laws *args:
    uv run --extra test pytest -q tests/test_laws_zero_sum.py {{args}}

# Ruff, as in CI
lint:
    uvx ruff check src tests

# Tests and lint, as CI runs them, before committing
check: lint
    uv run --locked --extra test pytest -q

# Create config/ and data/ with a server admin (prompts for the password)
init user="admin":
    uv run whistapp init --username {{user}}

# Run the app on http://127.0.0.1:8000
serve *args:
    uv run whistapp serve {{args}}

# Build the wheel and sdist into dist/
build:
    uv build

# Build and start the Docker container
up:
    docker compose up -d --build whist

# Build and start the container with the Cloudflare Tunnel
tunnel:
    docker compose --profile tunnel up -d --build
