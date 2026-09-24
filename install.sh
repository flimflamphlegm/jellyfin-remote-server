#!/bin/bash
# Installs the Jellyfin remote as a LaunchAgent so it starts at login.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.user.jellyfinremote"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CONFIG="$DIR/config.json"
PYTHON="$(command -v python3 || true)"

case "$DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "This folder is in Documents, Desktop or Downloads. macOS blocks background" >&2
    echo "services from reading those, so the remote wouldn't start. Move it first:" >&2
    echo "  mv \"$DIR\" ~/ && cd ~/$(basename "$DIR") && ./install.sh" >&2
    exit 1
    ;;
esac

if [ -z "$PYTHON" ]; then
  echo "python3 not found. Install it with: xcode-select --install" >&2
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "First-time setup."
  echo "Create an API key in Jellyfin: Dashboard > API Keys > +"
  read -r -p "Jellyfin API key: " API_KEY
  read -r -p "Jellyfin URL [http://localhost:8096]: " JF_URL
  read -r -p "Dashboard port [8097]: " PORT
  read -r -p "Only show music for this Jellyfin user (blank = anyone): " JF_USER
  JF_URL="${JF_URL:-http://localhost:8096}"
  PORT="${PORT:-8097}"
  API_KEY="$API_KEY" JF_URL="$JF_URL" PORT="$PORT" JF_USER="${JF_USER:-}" "$PYTHON" - "$CONFIG" <<'PY'
import json, os, sys
json.dump({
    "jellyfin_url": os.environ["JF_URL"],
    "api_key": os.environ["API_KEY"].strip(),
    "port": int(os.environ["PORT"]),
    "device_filter": "",
    "user_filter": os.environ["JF_USER"].strip(),
    "no_progress_clients": ["cliamp"],
}, open(sys.argv[1], "w"), indent=2)
PY
  chmod 600 "$CONFIG"
  echo "Saved $CONFIG"
fi

echo "Checking the connection to Jellyfin..."
if ! "$PYTHON" "$DIR/server.py" --check; then
  echo "Fix config.json (or delete it and rerun ./install.sh), then try again." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$DIR/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <!-- Allowed in a desktop login (Aqua) and in SSH/background sessions (Background). -->
  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
    <string>Background</string>
  </array>
  <!-- Restart after crashes, but not after a clean exit (e.g. another copy already has the port). -->
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
  </dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/jellyfinremote.log</string>
  <key>StandardErrorPath</key><string>$DIR/jellyfinremote.err</string>
</dict>
</plist>
PLIST

UID_NOW="$(id -u)"
launchctl bootout "gui/$UID_NOW/$LABEL" 2>/dev/null || true
launchctl bootout "user/$UID_NOW/$LABEL" 2>/dev/null || true

# A desktop login uses the gui domain; SSH sessions only have the user domain.
# Either way the plist is in ~/Library/LaunchAgents, so it also starts at every login.
if launchctl bootstrap "gui/$UID_NOW" "$PLIST" 2>/dev/null; then
  echo "Started in your desktop session."
elif launchctl bootstrap "user/$UID_NOW" "$PLIST"; then
  echo "Started in the background session (no desktop login, e.g. over SSH)."
else
  echo "launchd refused to start it. You can still run it by hand: python3 server.py" >&2
  exit 1
fi

PORT_NOW="$("$PYTHON" -c "import json;print(json.load(open('$CONFIG')).get('port', 8097))")"

# Confirm it actually answers, rather than trusting launchctl.
for _ in 1 2 3 4 5; do
  sleep 1
  if curl -s -o /dev/null "http://127.0.0.1:$PORT_NOW/api/state"; then
    RUNNING=1
    break
  fi
done
if [ -z "${RUNNING:-}" ]; then
  echo "It was registered but isn't answering on port $PORT_NOW." >&2
  echo "Check $DIR/jellyfinremote.err" >&2
  exit 1
fi

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost)"
echo "Installed. Open http://$IP:$PORT_NOW on your phone."
echo "Logs: $DIR/jellyfinremote.log and $DIR/jellyfinremote.err"
