#!/bin/bash
# Double-clickable installer for macOS.
#
# Usage: in Finder, open the unzipped claude-oneclick folder and
# double-click this file. Terminal opens automatically (because of the
# .command extension), runs the install, and launches the web UI.
#
# No commands typed. Closes when you press a key after install.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

clear
cat <<'BANNER'
================================================================
 Claude OneClick installer (macOS)
================================================================

This will:
  1. Verify Python 3.9+ is available
  2. Install the package to your user account
  3. Wire up shell rc + VSCode + a desktop launcher
  4. Open the web UI

Nothing requires admin / sudo.

BANNER

# ---- find Python ----------------------------------------------------------

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      PY="$cand"
      break
    fi
  fi
done

if [[ -z "$PY" ]]; then
  cat <<'NEEDPY'
==> Python 3.9+ was not found.

You need to install it first. The official installer is at:

    https://www.python.org/downloads/macos/

Opening that page now. After installing Python, come back and
double-click this file again.

NEEDPY
  open "https://www.python.org/downloads/macos/" || true
  echo "Press any key to exit..."
  read -rsn1
  exit 1
fi

echo "==> Found Python: $PY ($($PY --version))"
echo

# ---- run the installer ----------------------------------------------------

bash "$REPO_ROOT/install.sh" --launch

# When --launch is passed, install.sh `exec`s the UI and never returns.
# Just in case it does (e.g. UI immediately exits), keep the window open.
echo
echo "Press any key to close this window..."
read -rsn1
