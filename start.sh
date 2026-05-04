#!/usr/bin/env bash
# Double-click (or `./start.sh`) to open the Claude OneClick UI.
# Assumes you ran install.sh at least once.
set -e
cd "$(dirname "$0")"

# Prefer the installed CLI if it's on PATH.
if command -v claude-oneclick >/dev/null 2>&1; then
  exec claude-oneclick ui
fi

# Fall back to whichever python has the package importable.
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c "import claude_oneclick" >/dev/null 2>&1; then
    exec "$cmd" -m claude_oneclick ui
  fi
done

echo "Claude OneClick is not installed yet."
echo "Run ./install.sh first, then come back here."
exit 1
