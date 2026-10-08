#!/usr/bin/env bash
# One-command setup: creates a Fly.io app + volume + secrets and deploys the server.
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { echo "Error: $*" >&2; exit 1; }

# 1. flyctl ----------------------------------------------------------------
if ! command -v fly >/dev/null 2>&1; then
  echo "flyctl is not installed."
  if command -v brew >/dev/null 2>&1; then CMD="brew install flyctl"; else CMD="curl -L https://fly.io/install.sh | sh"; fi
  read -r -p "Install it now with '$CMD'? [Y/n] " yn
  [[ "${yn:-Y}" =~ ^[Yy] ]] || die "flyctl is required."
  bash -c "$CMD"
  export PATH="$HOME/.fly/bin:$PATH"
  command -v fly >/dev/null 2>&1 || die "flyctl installed but not on PATH; open a new terminal and rerun ./setup.sh"
fi
command -v openssl >/dev/null 2>&1 || die "openssl is required."

if ! fly auth whoami >/dev/null 2>&1; then
  bold "Log in to Fly.io (a browser window will open)"
  fly auth login
fi

# 2. questions ---------------------------------------------------------------
DEFAULT_APP="mfp-$(openssl rand -hex 3)"
read -r -p "App name [$DEFAULT_APP]: " APP; APP="${APP:-$DEFAULT_APP}"
[[ "$APP" =~ ^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$ ]] || die "App name must be lowercase letters, digits and dashes (3-30 chars)."
read -r -p "Region (e.g. arn Stockholm, fra Frankfurt, iad Virginia) [arn]: " REGION; REGION="${REGION:-arn}"
SYS_TZ="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || true)"
read -r -p "Your time zone, used for 'today' [${SYS_TZ:-UTC}]: " TZNAME; TZNAME="${TZNAME:-${SYS_TZ:-UTC}}"

echo
echo "This creates one small Fly machine (512 MB) and a 1 GB volume. Fly bills this to your account,"
echo "roughly \$3-4 per month at the time of writing. See https://fly.io/docs/about/pricing/"
read -r -p "Continue? [y/N] " yn
[[ "${yn:-N}" =~ ^[Yy] ]] || die "Cancelled."

# 3. create + deploy ---------------------------------------------------------
sed -e "s/__APP__/$APP/g" -e "s/__REGION__/$REGION/g" -e "s#__TIMEZONE__#$TZNAME#g" fly.toml.template > fly.toml

if fly apps list 2>/dev/null | awk '{print $1}' | grep -qx "$APP"; then
  echo "App $APP already exists; reusing it."
else
  fly apps create "$APP"
fi
if ! fly volumes list --app "$APP" 2>/dev/null | grep -q mfp_data; then
  fly volumes create mfp_data --size 1 --region "$REGION" --app "$APP" --yes
fi

if fly secrets list --app "$APP" 2>/dev/null | grep -q SECRET_KEY; then
  echo "Secrets already set; keeping the existing passcode and key."
  PASSCODE="(unchanged - the one you got the first time)"
else
  SECRET_KEY="$(openssl rand -base64 32)"
  PASSCODE="$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 16)"
  printf 'SECRET_KEY=%s\nMCP_PASSCODE=%s\n' "$SECRET_KEY" "$PASSCODE" | fly secrets import --app "$APP" --stage
fi

fly deploy --app "$APP" --ha=false

# 4. done --------------------------------------------------------------------
URL="https://$APP.fly.dev"
echo
bold "Done."
echo "  Connector URL : $URL/mcp"
echo "  Passcode      : $PASSCODE   (save it in a password manager; it is not shown again)"
echo "  Next, open    : $URL/setup   and paste your MyFitnessPal cookie header (the page explains how)."
echo
echo "Then in Claude: Settings -> Connectors -> Add custom connector -> paste the connector URL."
