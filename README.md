# mfp-mcp

A small, self-hosted [MCP](https://modelcontextprotocol.io) server that lets **Claude log food to your MyFitnessPal diary** — from the phone, in plain language ("5 eggs for breakfast").

- **One setup command**, then paste your MyFitnessPal cookies once in a web page.
- **No monthly cookie copying.** The server renews its own MyFitnessPal session and saves the renewed cookies.
- **Stays logged in.** Claude's connector login survives restarts and deploys (no passcode every time).
- **"My Foods" library.** Save your standard meals with exact numbers once; log them in one step.

> **Unofficial.** MyFitnessPal has no public API for this. The server uses the same private web/JSON endpoints as the MyFitnessPal website, with your own login. That may go against MyFitnessPal's terms of service, it can break whenever they change their site, and you use it at your own risk. Only run it for your own account.

## What you need

- A [Fly.io](https://fly.io) account (needs a payment card; this setup costs roughly $3–4/month — check Fly's current pricing)
- A MyFitnessPal account
- Claude with custom connectors (Pro, Max, Team or Enterprise on claude.ai)
- macOS or Linux with `bash` and `openssl`

## Setup

```bash
git clone <this repo> && cd mfp-mcp
./setup.sh
```

The script installs/uses `flyctl`, logs you in, asks for an app name, region and time zone, creates the app, a 1 GB volume and two secrets, deploys, and prints:

- the **connector URL** (`https://<app>.fly.dev/mcp`)
- your **passcode** (shown once — save it)
- the **setup link** (`https://<app>.fly.dev/setup`)

Then:

1. Open the setup link. In a desktop browser logged in to myfitnesspal.com: DevTools (F12) → **Network** → reload → click a `www.myfitnesspal.com` request → under *Request Headers* copy the value of `cookie:`. Paste it into the page with your passcode. It shows "Connected as <you>" when it works.
2. In Claude: **Settings → Connectors → Add custom connector**, paste the connector URL, and enter the passcode when asked.
3. Optional: put [`claude-project-instructions.md`](claude-project-instructions.md) in a Claude Project so it follows a "draft, confirm, then log" routine.

**Claude Code** works too: `claude mcp add --transport http mfp https://<app>.fly.dev/mcp`, then run `/mcp` to log in.

## Tools

| Tool | What it does |
|---|---|
| `get_day(date?)` | Diary by meal with entry ids, totals, your MyFitnessPal goals and what remains |
| `search_food(query)` | Your **My Foods** first, then MyFitnessPal's database |
| `food_info(food_id)` | Serving sizes of a MyFitnessPal food (pick one with `serving` in `log_food`) |
| `log_food(meal, items[], date?)` | Log one or more foods. `food` = a My Foods name/alias, or a MyFitnessPal food id. All-or-nothing |
| `edit_entry` / `delete_entry` | Change servings of, or delete, a diary entry |
| `my_foods_list` / `my_foods_save` / `my_foods_delete` | Manage your standard foods. Saving also creates a custom food in MyFitnessPal, so the diary entry has exactly your numbers |
| `status` | Connection OK? Username, session expiry, last keep-alive |

## How it stays logged in

MyFitnessPal's site logs you in with cookies. The server calls `/user/auth_token` every 6 hours with them; MyFitnessPal answers with renewed cookies (valid another 30 days), which the server stores (encrypted) and uses next time. A used session therefore doesn't expire.

Not guaranteed: MyFitnessPal might enforce a maximum session age. `status` shows when the session ends and warns 7 days ahead; if it is lost, every tool says *"MyFitnessPal login lost – open …/setup"* and pasting fresh cookies takes a minute. If the server is down for more than ~30 days the session expires too.

## Security notes

- Everything is behind your passcode (OAuth login for Claude, the same passcode for `/setup`); wrong guesses are rate-limited.
- MyFitnessPal cookies are stored **encrypted** (Fernet, key from the `SECRET_KEY` secret) on the volume. OAuth tokens are stored as hashes. Nothing sensitive is logged.
- Anyone with your passcode and the URL can read and edit your diary. Use a long passcode (the script generates one). Rotate with `fly secrets set MCP_PASSCODE=… --app <app>`.
- Run **one machine only** (the script does: `--ha=false`); state lives in SQLite on a single volume.

## Run locally

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest

export PUBLIC_URL=http://localhost:8000 MCP_PASSCODE=test SECRET_KEY=$(openssl rand -base64 32) DATA_DIR=./data
python -m mfp_mcp.server
# open http://localhost:8000/setup, then try it with:  npx @modelcontextprotocol/inspector   (URL http://localhost:8000/mcp)
```

Tests use a fake MyFitnessPal; no real account or cookies are involved. Lint and types: `ruff check . && ruff format --check . && mypy src`.

When MyFitnessPal changes something, run the opt-in live smoke test (it only writes to the date 2001-01-01 and cleans up):

```bash
MFP_LIVE_COOKIES=~/mfp-cookie.txt pytest tests/live -v   # file = your browser's cookie header
```

How the private MyFitnessPal endpoints behave is documented in [docs/how-mfp-works.md](docs/how-mfp-works.md).

Dependencies are pinned with hashes in `requirements.lock` (used by the Docker image). After changing `pyproject.toml`:
`uv lock && uv export --frozen --no-dev --no-emit-project --no-header -o requirements.lock`.

## Troubleshooting

- **Claude skips the passcode page and shows 0 tools** — Claude remembers a login for that exact URL from an earlier server (for example you deleted an app and created a new one with the same name). Remove the connector in Claude completely and add it again.
- **"Client ID not found" / asked for the passcode again and again** — make sure only one machine runs (`fly scale count 1`) and the volume is mounted at `/data`.
- **"MyFitnessPal login lost"** — paste fresh cookies at `/setup`.
- **Search finds nothing / odd results** — try an English name, or save the food in My Foods.
- **Update the server** — `git pull && fly deploy --app <app> --ha=false`.
- **Restore data** — Fly snapshots the volume daily (kept 5 days by default): `fly volumes snapshots list <volume-id>`, then create a volume from a snapshot. Losing the volume only means pasting cookies on `/setup` again, reconnecting Claude and re-saving My Foods.

## License

MIT — see [LICENSE](LICENSE).
