# Changelog

All notable changes to claude-oneclick. The version a user sees is the
base version concatenated with the install's commit SHA
(e.g. `0.2.0+abc1234`); release tags are pure semver.

## [Unreleased]

(Nothing yet — bumps land here on the way to the next tag.)

## [0.2.0] — 2026-05-04

First end-to-end-complete release. Adds CI, pipeline-correctness
fixes, UX polish, OS-keychain storage, and the publishing pipeline.

### Added
- GitHub Actions CI matrix (Ubuntu / macOS / Windows × Python 3.9 /
  3.11 / 3.13) running on every push.
- Vision passthrough: Anthropic image blocks → OpenAI `image_url`
  parts (base64 + URL forms). Per-preset `disable_vision` opt-out.
- Tool-result `is_error: true` sentinel passed to upstream as
  `[tool error] …` since OpenAI's `role=tool` has no native flag.
- Anthropic `disable_parallel_tool_use` → OpenAI
  `parallel_tool_calls: false` mapping.
- JSON-mode bridge: `tool_choice.tool` named `json_response` (or any
  preset-configured name) ALSO sets `response_format: json_object`.
- `Idempotency-Key` header on every upstream call (one uuid per
  inbound request, reused across retries).
- Optional fallback preset hop: when the primary exhausts retries,
  the proxy takes one fallback hop with that preset's model/auth.
- ⚡ **Test this preset** button on the Setup tab — sends a tiny
  end-to-end ping through our own proxy and shows latency + the
  upstream's reply.
- Per-preset, per-day token ledger (`~/.config/claude-oneclick/usage.json`).
  Persistent footer in the UI shows "Today: 12.3K in / 4.7K out".
- Optional cost estimation when presets carry `cost_per_1m_input` /
  `cost_per_1m_output`.
- OS-keychain storage for API keys: macOS Keychain, GNOME/KDE
  Secret Service, Windows Credential Manager. Toggle in Settings →
  API key storage. Resolver transparently falls back from per-preset
  field → keychain → empty.
- `docs/img/` placeholder for screenshots referenced in the README.
- PyPI publish workflow on `v*.*.*` tag push using OIDC trusted-
  publishing (no PyPI tokens stored in the repo). Auto-creates a
  GitHub Release with sdist + wheel attached.

### Changed
- `__base_version__` bumped from `0.1.0` to `0.2.0`. Per-commit SHA
  appended at install time gives `0.2.0+sha7chars`.
- README documents the four "zero-terminal install" double-click
  paths and the `pip install git+https://…` flow.

### Fixed
- Live-catalog ZIP-update path for non-git installs (downloaded
  branch ZIP extracted in place; `_buildinfo.py` re-stamped).
- `/v1/v1/chat/completions` URL doubling on bases that already
  include `/v1` (OpenRouter, Groq, Together convention).
- `Connection: keep-alive` removed from streamed responses (HTTP/1.0
  default in BaseHTTPRequestHandler can't honor it without
  Content-Length).

## [0.1.0]

Initial implementation: VPN-style env toggle, translation proxy,
preset library, system-wide env (POSIX rc files + Windows registry),
VSCode settings.json patching, autostart, secret-developer menu,
diagnostic wiring strip, copy-to-other-tools panel, group-level API
key sharing.

