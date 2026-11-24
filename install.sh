#!/bin/bash
# Installs the daily-update skill for Claude Code on this Mac.
# Safe to run more than once: it never overwrites an existing config or update history.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/skill"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$CLAUDE_DIR/skills/daily-update"
DATA="${DAILY_UPDATE_HOME:-$HOME/.config/daily-update}"
CONFIG="$DATA/config.json"

if [ ! -f "$SRC/SKILL.md" ] || [ ! -f "$SRC/scripts/daily_update.py" ] || [ ! -f "$SRC/config.example.json" ]; then
  echo "Error: the skill folder is missing or incomplete next to install.sh ($SRC)." >&2
  echo "Unzip the whole download and run install.sh from inside it." >&2
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  echo "Error: Python 3.8 or newer is needed and was not found." >&2
  echo "Run this in Terminal, wait for it to finish, then run install.sh again:" >&2
  echo "  xcode-select --install" >&2
  exit 1
fi

mkdir -p "$DEST/scripts"
cp "$SRC/SKILL.md" "$DEST/SKILL.md"
cp "$SRC/config.example.json" "$DEST/config.example.json"
cp "$SRC/scripts/daily_update.py" "$DEST/scripts/daily_update.py"
chmod +x "$DEST/scripts/daily_update.py"
echo "Installed the skill to $DEST"

mkdir -p "$DATA"
if [ -e "$CONFIG" ]; then
  echo "Kept your existing config at $CONFIG"
else
  cp "$SRC/config.example.json" "$CONFIG"
  chmod 600 "$CONFIG"
  echo "Created a new config at $CONFIG"
fi

if ! python3 "$DEST/scripts/daily_update.py" config >/dev/null; then
  echo "Error: the installed script did not run. See the message above." >&2
  exit 1
fi

if [ ! -d "$CLAUDE_DIR/projects" ]; then
  echo "Note: no Claude Code history was found at $CLAUDE_DIR/projects yet."
  echo "      Use Claude Code for some work first, then run /daily-update."
fi

echo ""
echo "Done. Next steps:"
echo "  1. Open a new Claude Code session (skills are loaded when a session starts)."
echo "  2. Type /daily-update"
echo "  3. The first time, Claude asks who the update is for. After that it just writes it."
