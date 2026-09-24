#!/bin/bash
set -euo pipefail
LABEL="com.user.jellyfinremote"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootout "user/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed $LABEL. config.json and logs were left in place."
