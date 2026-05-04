#!/usr/bin/env bash
# Double-click in Finder to open the Claude OneClick UI on macOS.
# `.command` is the Finder-double-clickable extension for shell scripts.
set -e
cd "$(dirname "$0")"

if command -v claude-oneclick >/dev/null 2>&1; then
  exec claude-oneclick ui
fi

for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c "import claude_oneclick" >/dev/null 2>&1; then
    exec "$cmd" -m claude_oneclick ui
  fi
done

osascript -e 'display dialog "Claude OneClick is not installed yet.\n\nDouble-click install.command first." buttons {"OK"} default button 1' >/dev/null
exit 1
