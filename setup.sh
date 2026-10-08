#!/usr/bin/env bash
# Deploys the server to Fly.io: creates the app, a volume and two secrets, then deploys.
#
#   ./setup.sh                 first-time setup, and later: update to the latest code (re-run after `git pull`)
#   ./setup.sh --new-passcode  replace the passcode (if you lost it); you must reconnect Claude afterwards
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
new_passcode() { openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-20; }

MODE="setup"
case "${1:-}" in
  "") ;;
  --new-passcode) MODE="passcode" ;;
  -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "Unknown option '$1'. Run ./setup.sh --help" ;;
esac

# 1. prerequisites -------------------------------------------------------------
[ -f fly.toml.template ] || die "Run this script from the cloned mfp-mcp folder."
command -v openssl >/dev/null 2>&1 || die "openssl is required (macOS has it; on Linux: sudo apt install openssl)."
command -v curl >/dev/null 2>&1 || die "curl is required (on Linux: sudo apt install curl)."

if ! command -v fly >/dev/null 2>&1 && [ -x "$HOME/.fly/bin/fly" ]; then export PATH="$HOME/.fly/bin:$PATH"; fi
if ! command -v fly >/dev/null 2>&1; then
  echo "The Fly.io command-line tool (flyctl) is not installed."
  if command -v brew >/dev/null 2>&1; then CMD="brew install flyctl"; else CMD="curl -L https://fly.io/install.sh | sh"; fi
  read -r -p "Install it now with '$CMD'? [Y/n] " yn
  [[ "${yn:-Y}" =~ ^[Yy] ]] || die "flyctl is required. Install it yourself: https://fly.io/docs/flyctl/install/"
  bash -c "$CMD"
  export PATH="$HOME/.fly/bin:$PATH"
  command -v fly >/dev/null 2>&1 || die "flyctl was installed but is not on your PATH. Open a new terminal and run ./setup.sh again."
fi

if ! fly auth whoami >/dev/null 2>&1; then
  bold "Log in to Fly.io (a browser window opens; create an account there if you don't have one)"
  fly auth login || die "Fly.io login failed. Run 'fly auth login' and then ./setup.sh again."
fi

# Defaults come from an earlier run (fly.toml), so re-running only needs Enter presses.
toml_get() {
  if [ -f fly.toml ]; then sed -n "s/^ *$1 = '\(.*\)'.*/\1/p" fly.toml | head -1; fi
}
PREV_APP="$(toml_get app)"; PREV_REGION="$(toml_get primary_region)"; PREV_TZ="$(toml_get TIMEZONE)"

# 2. --new-passcode ------------------------------------------------------------
if [ "$MODE" = "passcode" ]; then
  [ -n "$PREV_APP" ] || die "No fly.toml found. Run ./setup.sh first."
  PASSCODE="$(new_passcode)"
  printf 'MCP_PASSCODE=%s\n' "$PASSCODE" | fly secrets import --app "$PREV_APP"
  echo
  bold "New passcode: $PASSCODE"
  echo "Save it in a password manager. In Claude, disconnect and reconnect the connector, and enter the new passcode."
  exit 0
fi

# 3. questions -------------------------------------------------------------------
DEFAULT_APP="${PREV_APP:-mfp-$(openssl rand -hex 3)}"
echo "The app name becomes your server address: https://<app-name>.fly.dev"
read -r -p "App name [$DEFAULT_APP]: " APP; APP="${APP:-$DEFAULT_APP}"
[[ "$APP" =~ ^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$ ]] || die "App name must be 3-30 characters: lowercase letters, digits and dashes."

DEFAULT_REGION="${PREV_REGION:-arn}"
echo "Pick a Fly.io region near you, e.g. arn (Stockholm), ams (Amsterdam), fra (Frankfurt), lhr (London),"
echo "iad (Virginia), sjc (California), syd (Sydney). Full list: fly platform regions"
read -r -p "Region [$DEFAULT_REGION]: " REGION; REGION="${REGION:-$DEFAULT_REGION}"
[[ "$REGION" =~ ^[a-z]{3}$ ]] || die "A region is a 3-letter code such as arn or iad."

SYS_TZ="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || true)"
DEFAULT_TZ="${PREV_TZ:-${SYS_TZ:-UTC}}"
read -r -p "Your time zone, used to decide what 'today' is [$DEFAULT_TZ]: " TZNAME; TZNAME="${TZNAME:-$DEFAULT_TZ}"
[[ "$TZNAME" =~ ^[A-Za-z0-9_+/-]+$ ]] || die "Time zone must look like Europe/Copenhagen or America/New_York."
if [ -d /usr/share/zoneinfo ] && [ ! -f "/usr/share/zoneinfo/$TZNAME" ]; then
  die "Unknown time zone '$TZNAME'. Use a name like Europe/Copenhagen or America/New_York."
fi

if [ "$APP" != "$PREV_APP" ]; then
  echo
  echo "This creates one small Fly.io machine (shared CPU, 512 MB) and a 1 GB volume, billed to your Fly.io account."
  echo "At the time of writing that is roughly \$3-4 per month. Check current prices: https://fly.io/docs/about/pricing/"
  read -r -p "Continue? [y/N] " yn
  [[ "${yn:-N}" =~ ^[Yy] ]] || die "Cancelled. Nothing was created."
fi

# 4. create + deploy -------------------------------------------------------------
sed -e "s/__APP__/$APP/g" -e "s/__REGION__/$REGION/g" -e "s#__TIMEZONE__#$TZNAME#g" fly.toml.template > fly.toml

if fly status --app "$APP" >/dev/null 2>&1; then
  echo "App $APP already exists in your Fly.io account; updating it."
else
  fly apps create "$APP" || die "Could not create app '$APP'. The name may be taken by someone else: run ./setup.sh again and pick another name."
fi

if ! fly volumes list --app "$APP" 2>/dev/null | grep -q mfp_data; then
  fly volumes create mfp_data --size 1 --region "$REGION" --app "$APP" --yes \
    || die "Could not create the volume. Is '$REGION' a valid region? See: fly platform regions"
fi

PASSCODE=""
if fly secrets list --app "$APP" 2>/dev/null | grep -q SECRET_KEY; then
  echo "Secrets already set; keeping your existing passcode. (Lost it? ./setup.sh --new-passcode)"
else
  PASSCODE="$(new_passcode)"
  # Piped via stdin so the values never show up in the process list or shell history.
  printf 'SECRET_KEY=%s\nMCP_PASSCODE=%s\n' "$(openssl rand -base64 32)" "$PASSCODE" | fly secrets import --app "$APP" --stage
fi

bold "Deploying (Fly.io builds the image on its servers; the first time takes a few minutes)"
fly deploy --app "$APP" --ha=false || die "Deploy failed. Check the output above, then run ./setup.sh again."
# Exactly one machine: state lives in SQLite on one volume. Two machines cause "Client ID not found" errors.
fly scale count 1 --app "$APP" --yes >/dev/null 2>&1 || true

URL="https://$APP.fly.dev"
printf 'Waiting for %s to answer' "$URL"
for _ in $(seq 1 30); do
  if curl -fsS "$URL/health" >/dev/null 2>&1; then OK=1; break; fi
  printf '.'; sleep 4
done
echo
[ "${OK:-}" = 1 ] || echo "The server did not answer yet. Check with: fly logs --app $APP"

# 5. done ------------------------------------------------------------------------
echo
bold "Done. Your server: $URL"
echo
echo "  Connector URL : $URL/mcp"
if [ -n "$PASSCODE" ]; then
  echo "  Passcode      : $PASSCODE"
  echo "                  Save it in a password manager now. It is not shown again."
else
  echo "  Passcode      : unchanged (the one you saved the first time)"
fi
echo
echo "Next steps (see README):"
echo "  1. Open $URL/setup and paste your MyFitnessPal cookie header + passcode."
echo "  2. In Claude (web or desktop): Customize -> Connectors -> + Add -> Add custom connector -> paste the connector URL."
