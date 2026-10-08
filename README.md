# mfp-mcp – log food to MyFitnessPal by chatting with Claude

[![ci](https://github.com/jepperonn/mfp-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/jepperonn/mfp-mcp/actions/workflows/test.yml) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Tell Claude what you ate in plain language, on your phone or computer, and it logs it to your MyFitnessPal diary. It looks up the foods, shows you a draft, and logs only after you say yes. You run your own small server (on [Fly.io](https://fly.io), about $3–4/month) and connect it to Claude as a *custom connector*. Under the hood this is an [MCP](https://modelcontextprotocol.io) server, but you don't need to know what that is to use it.

> **You:** 3 eggs and a slice of rye bread for breakfast
>
> **Claude:** Draft for **breakfast**:
> - Egg, whole, boiled – 3 large – 234 kcal, 19 g protein
> - Rye bread – 1 slice – 83 kcal, 3 g protein
>
> Total 317 kcal, 22 g protein. Log it?
>
> **You:** yes
>
> **Claude:** Logged ✅ Today: 1,240 / 2,300 kcal, 1,060 kcal and 74 g protein left.

<!-- SCREENSHOT: add a phone screenshot or short GIF of a conversation like the one above here, e.g.
     save it as docs/images/demo.png and replace this comment with:
     <p align="center"><img src="docs/images/demo.png" alt="Logging breakfast with Claude on an iPhone" width="320"></p>
     Crop out your name, account and anything else personal. -->

> [!IMPORTANT]
> **Unofficial.** This project is not affiliated with, endorsed by or supported by MyFitnessPal or Anthropic. MyFitnessPal has no public API for this. The server uses the same undocumented interface as the MyFitnessPal website, logged in as you. It can stop working whenever MyFitnessPal changes their site, and using it may go against [MyFitnessPal's terms](https://www.myfitnesspal.com/terms-of-service). Use it only with your own account, never share your server with anyone, and you are responsible for how you use it.

**Contents:** [What you need](#what-you-need) · [Setup](#setup) · [Use it on your phone](#use-it-on-your-phone) · [Recommended Claude instructions](#recommended-claude-instructions) · [Maintenance](#maintenance) · [Troubleshooting](#troubleshooting) · [FAQ](#faq)

## What you need

| | |
|---|---|
| **MyFitnessPal** | Any account (free works). |
| **Claude** | Any plan with custom connectors. The **Free** plan allows **one** custom connector, which is enough. On **Team/Enterprise** only an organization owner can add custom connectors, so use a personal account unless your admin adds it for you. |
| **Fly.io** | An account with a payment card. One small machine + a 1 GB volume is roughly **$3–4/month** at the time of writing. [Check Fly.io's current pricing.](https://fly.io/docs/about/pricing/) |
| **A computer** | macOS or Linux with a terminal (Windows: use [WSL](https://learn.microsoft.com/windows/wsl/install); not tested). You need `git`. You do **not** need Docker or Python: Fly.io builds the server for you. |
| **A desktop browser** | Chrome, Edge or Firefox, to copy your MyFitnessPal login once. |
| **Time** | About 20 minutes. |

## Setup

You run two commands. The script does the Fly.io work for you. You then do three things in your browser.

### 1. Download the code

```bash
git clone https://github.com/jepperonn/mfp-mcp.git
```

```bash
cd mfp-mcp
```

### 2. Run the setup script

```bash
./setup.sh
```

**What the script does:**

- Installs the Fly.io command-line tool if it's missing (it asks first).
- Opens a browser so you can log in to Fly.io, or create an account.
- Asks three questions. Press Enter to accept the suggestion:
  - **app name**, which becomes your address `https://<app-name>.fly.dev`
  - **region**, the data center nearest you (e.g. `arn` Stockholm, `iad` Virginia)
  - **time zone**, which decides what "today" means
- Shows the cost and asks you to confirm.
- Creates the app, a small storage volume and two secrets: an encryption key and your **passcode**.
- Deploys the server and checks that it answers.

At the end it prints three things. **Keep this window open:**

```
Connector URL : https://<app-name>.fly.dev/mcp
Passcode      : <20 random letters and digits>
Next steps    : open https://<app-name>.fly.dev/setup ...
```

**Save the passcode in a password manager now.** It is shown only once. Anyone who has your passcode and your URL can read and change your food diary.

### 3. Connect the server to MyFitnessPal (once)

The server logs in to MyFitnessPal with your browser's login cookies. You copy them once, and the server keeps them fresh after that.

1. On a computer, open [myfitnesspal.com](https://www.myfitnesspal.com) in Chrome, Edge or Firefox and log in.
2. Open the developer tools: press **F12**, or **⌥⌘I** on a Mac. Go to the **Network** tab and reload the page.
3. Click the first request to `www.myfitnesspal.com` in the list.
4. Under **Request Headers**, find **`cookie:`**, right-click its value and choose **Copy value**. In Firefox, turn on **Raw** first. Copy the **whole** value. It is long (about 25 cookies). A single cookie is not enough.
5. Open `https://<app-name>.fly.dev/setup`, paste the value, enter your passcode and click **Connect to MyFitnessPal**.
6. You should see **"Connected as &lt;your username&gt;"**.

> [!CAUTION]
> Your cookie value is a key to your MyFitnessPal account, and your passcode is a key to your server. Paste them **only** into your own `/setup` page. **Never** paste them into a Claude chat, a GitHub issue, a screenshot, Discord, or anywhere else. If you leaked one by mistake: for cookies, log out of MyFitnessPal in the browser, which ends that session; for the passcode, run `./setup.sh --new-passcode`.

**Where your secrets live:**

| Secret | Where it is stored | Who sees it |
|---|---|---|
| Passcode (`MCP_PASSCODE`) | Fly.io secret + your password manager | You. You type it when connecting Claude and on `/setup`. |
| Encryption key (`SECRET_KEY`) | Fly.io secret only | Nobody needs to see it. |
| MyFitnessPal cookies | On your server's volume, **encrypted** with the key | Only your server. |
| Claude's login tokens | On your server's volume, stored as hashes | Only your server. |

Nothing secret is stored in this repository or in `fly.toml`.

### 4. Add the connector in Claude

Do this on **claude.ai in a browser** or in the **Claude desktop app**. You can't add connectors from the phone app.

1. Go to **Customize → Connectors**.
2. Click **+ Add → Add custom connector**.
3. Name it `MyFitnessPal` and paste the **connector URL** (`https://<app-name>.fly.dev/mcp`). Click **Continue** and keep the detected sign-in settings.
4. A page from your server asks for your **passcode**. Enter it and click **Connect**.
5. In a new chat, click **+** in the lower left → **Connectors**, and make sure **MyFitnessPal** is switched on.

Try it: *"What have I eaten today?"*

**Claude Code** works too:

```bash
claude mcp add --transport http mfp https://<app-name>.fly.dev/mcp
```

Then run `/mcp` inside Claude Code and log in with your passcode.

## Use it on your phone

When the connector is added on the web or desktop, it also shows up in the **Claude app for iPhone and Android**, signed in to the same account. Start a new chat and write what you ate. You can also send a photo of your meal. Claude estimates the amounts and shows a draft first. If the food tools aren't used, check that the connector is switched on for the chat (see [Troubleshooting](#troubleshooting)).

## Recommended Claude instructions

Claude works best with a fixed routine: find the foods, **show a draft, and log only after you confirm**. Copy the text in [`claude-project-instructions.md`](claude-project-instructions.md) into a **Claude Project's instructions** and chat inside that project. On plans without Projects, you can put it in **Settings → Profile → personal preferences** instead.

You can also save your standard meals once ("My Foods") with exact numbers. Then *"my usual breakfast"* is logged in one step, with your numbers, not a guess.

## What Claude can do

| Tool | What it does |
|---|---|
| `get_day` | Your diary for a day: entries per meal, totals, your MyFitnessPal goals and what's left |
| `search_food` | Searches your **My Foods** first, then MyFitnessPal's food database |
| `food_info` | Serving sizes of a MyFitnessPal food |
| `log_food` | Logs one or more foods to a meal. If any item fails, nothing is logged |
| `edit_entry` / `delete_entry` | Changes the servings of a diary entry, or deletes it |
| `my_foods_list` / `my_foods_save` / `my_foods_delete` | Manages your standard foods. Saving also creates a private custom food in MyFitnessPal, so diary entries use exactly your numbers |
| `status` | Is the MyFitnessPal login OK? Username, session expiry, last keep-alive |

## Maintenance

Normally there is nothing to do.

- **Staying logged in:** every 6 hours the server renews its MyFitnessPal session, which extends it by 30 days, and saves the renewed cookies. As long as the server runs, the session keeps going. MyFitnessPal may still log it out some day. Then Claude says *"MyFitnessPal login lost – open …/setup"*, and you repeat [step 3](#3-connect-the-server-to-myfitnesspal-once) (a minute). If the server is stopped for more than about 30 days, you also have to repeat step 3.
- **Restarts and updates:** your MyFitnessPal login, Claude's connection and your My Foods are stored on the volume. They survive restarts and deploys, so you don't type the passcode again.
- **Check that it works:** ask Claude *"check the MyFitnessPal status"*, or open `https://<app-name>.fly.dev/health`.
- **See the server's logs** (run these in the `mfp-mcp` folder):

  ```bash
  fly logs
  ```

  ```bash
  fly status
  ```

- **Update to the latest version:** pull the new code, then run the script again. It remembers your answers, so just press Enter, and it keeps your passcode.

  ```bash
  git pull
  ```

  ```bash
  ./setup.sh
  ```

- **Lost your passcode:** this sets a new one. Then remove and re-add the connector in Claude.

  ```bash
  ./setup.sh --new-passcode
  ```

- **Backups:** Fly.io snapshots the volume daily and keeps snapshots for 5 days. If you lose the volume, you only have to paste cookies again, reconnect Claude and re-save My Foods. Your diary itself lives in MyFitnessPal.
- **Stop paying / delete everything:** this deletes the server and its data. Then remove the connector in Claude.

  ```bash
  fly apps destroy <app-name>
  ```

## Troubleshooting

**"Client ID not found", or Claude asks for the passcode again and again**
More than one Fly.io machine is running. The server keeps its state in one database on one volume, so it must run as exactly one machine. Fix:

```bash
fly scale count 1
```

Then remove and re-add the connector in Claude.

**`/setup` says "MyFitnessPal did not accept those cookies" or "doesn't look like a full cookie header"**
You probably copied a single cookie (for example `__Secure-next-auth.session-token`) or a cut-off value. Copy the **entire** `cookie:` request header from a `www.myfitnesspal.com` request, as described in [step 3](#3-connect-the-server-to-myfitnesspal-once), right after logging in. Don't use the *Application → Cookies* list.

**The connector is added, but Claude doesn't use the food tools**
Click **+ → Connectors** in the chat and switch **MyFitnessPal** on, then **start a new chat**. Chats that were open before you added the connector don't see it.

**Claude skips the passcode page and shows 0 tools**
Claude remembers a login for that exact URL from an earlier server, for example if you deleted an app and created a new one with the same name. Remove the connector in Claude completely and add it again.

**Claude asks for the passcode again after a restart**
That shouldn't happen, because logins are stored on the volume. If it does, check that only one machine runs (see above) and that the volume is mounted (`fly status` should show `mfp_data`).

**"MyFitnessPal login lost – open …/setup"**
The MyFitnessPal session ended. Paste a fresh cookie header on `/setup`.

**`fly apps create` fails / app name taken**
App names are global on Fly.io. Run `./setup.sh` again and choose another name.

**Search finds nothing or odd results**
Try the English name. Or save the food once with exact numbers ("save 'oat porridge' as My Food: 350 kcal, 12 g protein…").

Still stuck? [Open an issue](https://github.com/jepperonn/mfp-mcp/issues/new/choose). Never include cookies, passcodes or your server URL.

## FAQ

**Is this official?**
No. It is a hobby project, not affiliated with MyFitnessPal or Anthropic. It uses MyFitnessPal's undocumented website interface, which can change at any time.

**Is it safe?**
Everything is behind your passcode, and wrong guesses are rate-limited. MyFitnessPal cookies are encrypted on your server, and Claude's tokens are stored only as hashes. The code is small and open for you to read. Still, your server holds a login to your MyFitnessPal account. Keep the passcode private and read [SECURITY.md](SECURITY.md). Automated use may break MyFitnessPal's terms; that risk is yours.

**Why can't I just use someone else's server, or share mine?**
Whoever runs the server holds your MyFitnessPal login, and whoever has the passcode can read and edit the diary. The server is built for exactly one person. Run your own.

**Does it work on Claude's Free plan?**
Yes. Free allows one custom connector. Projects and higher usage limits depend on your plan.

**What does it cost?**
Fly.io bills about $3–4/month for the machine and volume ([check current pricing](https://fly.io/docs/about/pricing/)). MyFitnessPal Premium is not required, and this project is free.

**Can it read my weight, exercise or water?**
Not yet. Only food diary, goals and food search.

## For developers

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src && pytest
```

Run locally (in a browser, open `http://localhost:8000/setup`. Test MCP with `npx @modelcontextprotocol/inspector` against `http://localhost:8000/mcp`):

```bash
PUBLIC_URL=http://localhost:8000 MCP_PASSCODE=test SECRET_KEY=$(openssl rand -base64 32) DATA_DIR=./data python -m mfp_mcp.server
```

The tests use a fake MyFitnessPal. They need no account or cookies. An opt-in live smoke test (`tests/live/`) checks the real MyFitnessPal endpoints. It only touches the diary date 2001-01-01 and cleans up afterwards. How the undocumented endpoints behave is written down in [docs/how-mfp-works.md](docs/how-mfp-works.md). Dependencies are pinned with hashes in `requirements.lock`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Built on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Starlette](https://github.com/Kludex/starlette)/[Uvicorn](https://github.com/Kludex/uvicorn), [HTTPX](https://www.python-httpx.org), [lxml](https://lxml.de) and [cryptography](https://cryptography.io). Their licenses apply to them. The MyFitnessPal client in this repo is its own implementation. It does not use or copy code from other MyFitnessPal libraries.

Other community projects in this space, if this one doesn't fit you: [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal), [delize/myfitness-mcp](https://github.com/delize/myfitness-mcp), [AdamWalt/myfitnesspal-mcp-python](https://github.com/AdamWalt/myfitnesspal-mcp-python).

MyFitnessPal and Claude are trademarks of their respective owners and are used here only to describe what this project works with.

## License

[MIT](LICENSE) © 2026 Jeppe Rønn
