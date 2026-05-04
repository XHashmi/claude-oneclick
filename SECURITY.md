# Security notes

This document captures the security posture of `claude-oneclick`, the
threat model it's designed against, and the specific defenses in code.
Last reviewed against the codebase on the same commit as the version in
the changelog.

## Threat model

The tool runs as a normal user-level process. The threats it guards
against, in priority order:

1. **A malicious web page in your browser**, while the UI is running on
   localhost. Without defense, the page could fetch your API keys and
   silently flip your toggle.
2. **A second user on the same multi-user machine** — limited by OS
   file permissions; not a primary target.
3. **An attacker with shell access to your account** — already game
   over (they can read `config.json` directly); we don't try to defend.
4. **Supply-chain risk on install** — minimized by being a single-repo
   pure-Python tool with zero runtime deps.

The tool is **not** a sandbox. The proxy forwards requests verbatim;
your API key is sent to whatever upstream you configure. Don't put a
hostile URL in `base_url`.

## Defenses in code

### 1. Localhost-only bind

Both the UI server (`server.py`) and the translation proxy (`proxy.py`)
bind to `127.0.0.1` by default. The default port choices (47823 / 47824)
are unassigned by IANA so we don't collide with anything well-known.

### 2. CSRF tokens

Every mutating endpoint (`POST`, `DELETE`) requires the request to echo
back a per-launch CSRF token via `X-CSRF`. The token is set as a cookie
(`SameSite=Strict`) on the first GET. Comparison uses
`secrets.compare_digest` to avoid timing leaks.

`GET /api/export` also requires CSRF because it returns API keys in
plaintext. `GET /api/state` does not, but it redacts every saved API
key to a boolean (`api_key_set: true|false`).

### 3. DNS-rebinding defense

`SameSite=Strict` cookies are not enough. A hostile page on
`evil.com` could resolve `evil.com` to `127.0.0.1`, then talk to our
server as same-origin — and the cookie *is* legitimately same-site by
that point.

The defense: **every** request handler checks the `Host:` header and
rejects anything that isn't `127.0.0.1`/`localhost`/`[::1]` (with or
without the port). Rebound origins fail this check because the page
was loaded under a foreign hostname, so `Host:` is the foreign name.
Implemented in `server._Handler._host_ok` and `proxy._Handler._host_ok`.

### 4. Path-traversal defense

`server._serve_static` resolves the requested path under `UI_DIR` and
calls `Path.relative_to()` — any `../` escape raises `ValueError` and
returns 400.

### 5. JSONC parser

VSCode `settings.json` may contain comments and trailing commas. The
parser strips them via two regexes (`_LINE_COMMENT`, `_BLOCK_COMMENT`)
and a trailing-comma cleanup. Catastrophic backtracking is bounded:
`_BLOCK_COMMENT` uses non-greedy `.*?` against `re.DOTALL`, which
runs in linear time on the typical ≤ 100 KB input.

### 6. Subprocess invocations

Every `subprocess.run` call passes an `args` list (never `shell=True`).
That defeats command injection on values flowing from `config.json`
(API keys, base URLs, etc.) into `setx` / `reg` / `launchctl`.

### 7. Shell rc / PowerShell `$PROFILE` injection

We only edit between `# >>> claude-oneclick >>>` / `# <<< claude-oneclick <<<`
markers. A re-install is idempotent (the existing block is stripped
before the new one is appended). Uninstall strips the block and leaves
all other user content alone (covered by `tests/test_shell.py`).

Values written into `env.sh` go through `shlex.quote()`. PowerShell
`env.ps1` uses single-quoted strings (literal in PS), with `'` escaped
to `''`. Cmd `env.cmd` escapes `^`, `&`, `|`, `<`, `>` and rejects
embedded newlines.

### 8. File mode 0600 on `config.json`

`config.save()` `chmod`s the config to 0600 on POSIX. On Windows,
NTFS inherits the user-profile ACL, which is user-only by default.

### 9. Request-size cap on the proxy

`/v1/messages` accepts up to `_MAX_BODY_BYTES` = 32 MiB. Larger
requests get a 413, preventing OOM from a malformed `Content-Length`.

### 10. UI XSS defense

All user-supplied strings rendered into the DOM go through `escape()`
(maps `&<>"'` to entities). User-supplied text inserted via
`textContent` (e.g. preset notes) is also safe by browser API
contract.

### 11. Secrets never echo back

`/api/state` and the sidebar preset list redact `api_key` to a boolean
(`api_key_set`). The UI's *General* tab leaves the API-key input blank
on edit and shows `(saved — leave blank to keep)` as placeholder.
Saving a blank key does **not** clear the existing one.

### 12. Subprocess-detached proxy daemon

The proxy is started in a detached child process (`start_new_session`
on POSIX, `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows)
with stdin closed. Child inherits no file descriptors except the
explicit log file.

### 13. Autostart entries are user-scope

Linux: `~/.config/autostart/claude-oneclick.desktop`. macOS:
`~/Library/LaunchAgents/...`. Windows:
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` — never
`HKLM`. No setuid, no admin elevation, no system-scope writes.

## Known limitations / non-goals

- API keys are stored in **plaintext** in `~/.config/claude-oneclick/config.json`.
  Encrypting at rest would require OS-keychain integration (Linux
  Secret Service, macOS Keychain, Windows DPAPI) and is out of scope
  for this version. The file mode (0600) and user-profile ACLs are the
  only protection. Anyone who can read your home dir can read your
  keys.
- Same-user, same-host processes can call the proxy directly and use
  your provider quota. Mitigated by localhost-only bind, but not
  prevented. A unix-socket variant with mode 0600 would close that;
  not currently implemented.
- We do not validate provider TLS certificates beyond what
  `urllib.request` does by default. (For HTTPS that's fine; for
  enterprise mitm proxies, set `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`
  in your shell.)
- The translation proxy's outbound calls follow HTTP redirects (the
  default for `urllib.request`). A redirect from
  `https://api.deepseek.com` to a different host would be honored.
  Mitigated by the user choosing the upstream URL.

## Reporting

If you find a vulnerability, please open a private security advisory
on the GitHub repo rather than a public issue.
