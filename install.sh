#!/usr/bin/env bash
# One-click installer for claude-oneclick (Linux + macOS).
#
# Usage:
#   ./install.sh                  install everything, no UI launch
#   ./install.sh --launch         install and open the web UI
#   ./install.sh --uninstall      remove shell + VSCode + launcher integration
#
# This script:
#   1. Verifies Python 3.9+ is available.
#   2. pip-installs the package (--user, editable) so `claude-oneclick`
#      lands on PATH.
#   3. Drops a single `source` line into your shell rc files inside a
#      marker block (idempotent + reversible via --uninstall).
#   4. Patches every VSCode user settings.json it finds with our managed
#      env keys (also idempotent).
#   5. Installs a desktop launcher (.desktop on Linux, .command on macOS).
#
# No sudo. No system-scope changes. Safe to re-run.

set -euo pipefail

UNINSTALL=0
LAUNCH=0
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    --launch)    LAUNCH=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- pick a Python ---------------------------------------------------------

resolve_python() {
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
        printf '%s\n' "$cmd"
        return 0
      fi
    fi
  done
  echo "error: Python 3.9+ not found." >&2
  echo "       Install via your package manager (e.g. 'apt install python3', 'brew install python')." >&2
  return 1
}

PY="$(resolve_python)"
echo "==> Python: $PY ($($PY --version))"

# ---- uninstall path --------------------------------------------------------

if [[ "$UNINSTALL" == "1" ]]; then
  echo "==> Removing shell + VSCode + launcher integration"
  "$PY" -m claude_oneclick _post_install --uninstall
  echo "==> Done. To remove the package itself: pip uninstall claude-oneclick"
  exit 0
fi

# ---- install path ----------------------------------------------------------

echo "==> Installing claude-oneclick (pip --user -e)"
"$PY" -m pip install --user --upgrade pip >/dev/null
"$PY" -m pip install --user -e "$REPO_ROOT"

echo "==> Wiring shell rc + VSCode + launcher"
"$PY" -m claude_oneclick _post_install

# Detect whether ~/.local/bin is on PATH; if not, install.sh injects an
# `export PATH="$HOME/.local/bin:$PATH"` line into the same shell-rc block
# our env file is sourced from. (Only fires if missing — idempotent.)
LOCAL_BIN="$HOME/.local/bin"
case ":$PATH:" in
  *":$LOCAL_BIN:"*) ;;
  *)
    echo "==> ~/.local/bin not on PATH — adding it to your shell rc"
    "$PY" - <<'PY'
import os, pathlib
home = pathlib.Path.home()
local_bin = home / ".local/bin"
marker_begin = "# >>> claude-oneclick PATH >>>"
marker_end = "# <<< claude-oneclick PATH <<<"
block = (
    f"\n{marker_begin}\n"
    f'case ":$PATH:" in\n'
    f'  *":{local_bin}:"*) ;;\n'
    f'  *) export PATH="{local_bin}:$PATH" ;;\n'
    f'esac\n'
    f"{marker_end}\n"
)
for rc in (".bashrc", ".zshrc", ".profile"):
    p = home / rc
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    if marker_begin in text:
        continue
    p.write_text(text + block, encoding="utf-8")
PY
    ;;
esac

cat <<'EOF'

==> Installed.

Next steps:
  1. Restart your terminal (or `source ~/.bashrc` / `source ~/.zshrc`).
  2. Pick a preset and paste your API key:
       claude-oneclick ui
  3. Flip the toggle ON. Every new shell, VSCode terminal, and Claude Code
     session inherits the override until you flip OFF.

Useful commands:
  claude-oneclick presets        # list built-in + custom presets
  claude-oneclick use deepseek   # switch active preset
  claude-oneclick on             # turn ON the active preset
  claude-oneclick off            # turn OFF
  claude-oneclick status         # current state
  claude-oneclick models <name>  # live /v1/models from the provider

EOF

if [[ "$LAUNCH" == "1" ]]; then
  echo "==> Launching UI..."
  exec "$PY" -m claude_oneclick ui
fi
