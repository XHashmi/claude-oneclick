# claude-oneclick

A **VPN-style one-click toggle** that routes **Claude Code** (CLI and the
VSCode extension) to custom model endpoints — DeepSeek (V3, V4 Pro,
V4 Flash, R1), NVIDIA NIMs (Llama 3.1 405B, Nemotron-70B, DeepSeek-R1),
OpenRouter,
Groq, Together AI, Fireworks, Ollama, or any custom OpenAI-compatible /
Anthropic-compatible backend.

Flip the switch in the web UI and every new shell, VSCode terminal, and
Claude Code session picks up the new model. Flip it off — Claude Code goes
back to talking to api.anthropic.com.

```
                  ┌─────────────────────────────────────┐
                  │ Claude OneClick                     │
                  │  [ Active: DeepSeek V4 Pro ] ON ●━━━│  ← VPN-style toggle
                  └─────────────────────────────────────┘
```

## What you get

- **Big VPN-style toggle** in a local web UI. ON wires up env vars
  system-wide; OFF tears them down.
- **Built-in presets** for DeepSeek V3 / V4 Pro / V4 Flash / R1, NVIDIA
  NIMs (Llama-405B, Nemotron-70B, DeepSeek-R1), OpenRouter, Groq, Together,
  Fireworks, and local Ollama.
- **Custom presets** — point at any base URL, paste an API key, set
  model + small/fast model, save it.
- **Live model dropdown** — fetches `GET /v1/models` (or Ollama's
  `/api/tags`) from the provider using your saved key, populates the
  picker.
- **Per-preset customization** — extra HTTP headers, sampling defaults
  (temperature / top_p / top_k / max_tokens), request timeout / retries
  / backoff, system-prompt prefix/suffix, model aliases, streaming
  on/off, prompt-cache passthrough.
- **Bundled translation proxy** — Claude Code speaks Anthropic's
  `/v1/messages`; most providers speak OpenAI's `/v1/chat/completions`.
  The local proxy translates both directions (text + tool calls + SSE
  streaming).
- **System-wide application** — drops a single source line into your
  shell rc files (Linux/macOS) **or** uses `setx` + `$PROFILE` + cmd.exe
  AutoRun (Windows) and patches VSCode `settings.json` so the integrated
  terminal and the Claude Code VSCode extension both inherit the env.
- **Skip the Claude Code login** — sets `CLAUDE_CODE_SKIP_LOGIN=1` so
  the CLI and VSCode extension authenticate via env vars and never
  prompt for an Anthropic OAuth login when you're using a custom
  provider.
- **Cross-platform, cross-arch** — Linux, macOS, **Windows on x86 / x64
  / ARM64**. No native binaries; just stdlib Python + the OS's own
  utilities (`setx`, `reg`, `cmd.exe`, PowerShell on Windows; bash/zsh
  rc on POSIX).
- **One-click launcher** — `.desktop` on Linux, `.command` on macOS,
  Start-Menu `.lnk` on Windows. Double-click and the UI opens.
- **Zero runtime dependencies** — pure Python stdlib.

## Install

### Zero-terminal install (double-click)

Don't want to type any commands? Download the repo as a ZIP from
GitHub (the green **Code → Download ZIP** button), unzip it, and:

| OS | What to double-click |
|---|---|
| **macOS** | `install.command` |
| **Windows** (x86 / x64 / ARM64) | `install.bat` |
| **Linux** (GNOME / KDE / Cinnamon / Xfce) | `install-linux.desktop` |

A terminal window pops up automatically, runs the install, and the web
UI opens in your browser. Press a key to close it when it's done.

> **Prerequisite:** Python 3.9+ must already be installed. If it isn't,
> the installer detects that and opens the python.org download page for
> you. On Windows, the Microsoft Store also has a one-click "Python
> 3.12" listing that picks the right arch (x64 or ARM64) automatically.
> A future release will bundle Python via PyInstaller so even that step
> isn't needed.

### One line

#### Linux / macOS

```sh
git clone https://github.com/xhashmi/claude-oneclick.git ~/.claude-oneclick
~/.claude-oneclick/install.sh --launch
```

That's it. The script:

1. Installs the package with `pip install --user -e .`
2. Drops a `source` block into `~/.bashrc` / `~/.zshrc` / `~/.profile`
3. Patches every VSCode `settings.json` it can find
4. Installs a desktop launcher
5. Opens the web UI

#### Windows (PowerShell, any arch — x86 / x64 / ARM64)

```powershell
git clone https://github.com/xhashmi/claude-oneclick.git $HOME\.claude-oneclick
$HOME\.claude-oneclick\install.ps1 -Launch
```

The PowerShell script does the equivalent for Windows: `setx` + `$PROFILE`
hook + cmd.exe AutoRun + VSCode settings.json + a Start-Menu `.lnk`.

> Windows ARM64 users: install the **ARM64** Python build from
> [python.org](https://www.python.org/downloads/windows/) or
> `winget install -e --id Python.Python.3.12` (it picks the right arch).
> Everything else just works — no native code in the loop.

### Manual / pipx

```sh
pipx install /path/to/claude-oneclick
claude-oneclick _post_install
```

## Usage

### From the UI (recommended)

```sh
claude-oneclick ui
```

Opens `http://127.0.0.1:47823` (the UI port). On the page:

1. Pick a preset in the sidebar (e.g. **DeepSeek V4 Pro**).
2. Click the **General** tab → paste your API key → click **Save key**.
3. Click the **Models** tab → click **Refresh** to pull the live model
   list from the provider → pick `deepseek-v4-pro` for the main model
   and `deepseek-v4-flash` for the small/fast slot (or whatever ids the
   provider serves).
4. (Optional) Click the **Advanced** tab → tick **Enable reasoning mode**
   to send `reasoning_effort` on every request — DeepSeek V4 Pro and
   Flash both honor it; other upstreams ignore the field.
5. Click **Use this preset**.
6. Flip the big toggle in the header **ON**.

That's it — open a new terminal or VSCode window, run `claude`, and you're
talking to DeepSeek V4. Flip OFF to go back to Anthropic.

### From the CLI

```sh
claude-oneclick presets               # list everything
claude-oneclick set-key deepseek-v4 sk-...   # save your API key
claude-oneclick use deepseek-v4       # switch active preset
claude-oneclick on                    # toggle ON
claude-oneclick status                # show current state
claude-oneclick models deepseek-v4    # live /v1/models from upstream
claude-oneclick off                   # toggle OFF
claude-oneclick toggle                # flip
```

### Add a custom preset

```sh
claude-oneclick add my-deepseek \
    --base-url https://api.deepseek.com \
    --api-key  sk-... \
    --model    deepseek-v4 \
    --small-fast-model deepseek-chat \
    --format   openai \
    --label    "My DeepSeek"

claude-oneclick set my-deepseek request_timeout_seconds 600
claude-oneclick set-header my-deepseek X-Custom-Header value
claude-oneclick add-alias my-deepseek claude-sonnet-4 deepseek-v4
```

Or skip the CLI and do it all in the **+ New preset** button in the UI.

## How it actually works

```
┌──────────────────────────────────────────────────────────┐
│ ~/.config/claude-oneclick/                               │
│   config.json   <- presets, API keys, on/off state       │
│   env.sh        <- exports (POSIX)                       │
│   env.ps1       <- exports (Windows PowerShell)          │
│   env.cmd       <- exports (Windows cmd.exe AutoRun)     │
└──────────────────────────────────────────────────────────┘
                       │
                       │ sourced from ~/.bashrc / ~/.zshrc
                       │ or HKCU\Environment via `setx`
                       ▼
        ┌──────────────────────────────────────┐
        │ ANTHROPIC_BASE_URL=http://127.0.0.1:…│
        │ ANTHROPIC_AUTH_TOKEN=…               │
        │ ANTHROPIC_MODEL=deepseek-v4          │
        │ ANTHROPIC_SMALL_FAST_MODEL=deepseek… │
        │ CLAUDE_CODE_SKIP_LOGIN=1             │
        └──────────────────────────────────────┘
                       │
                       ▼
            ┌──────────────────────┐
            │ Claude Code (CLI)    │
            │ Claude Code (VSCode) │
            └──────────────────────┘
                       │
                       ▼   (when format == "openai")
            ┌──────────────────────┐    OpenAI-compat upstream:
            │ 127.0.0.1:47824      │ ─► DeepSeek, NVIDIA NIMs,
            │ translation proxy    │    Groq, Together, Ollama, …
            └──────────────────────┘
```

The proxy is **only** started when the active preset has `format: "openai"`.
For Anthropic-format endpoints (OpenRouter passthrough, a self-hosted
Anthropic-shaped server) Claude Code is pointed straight at the upstream
and the proxy stays off.

### Why not just `export ANTHROPIC_BASE_URL=…` manually?

You can. This tool just makes it:

- **Reversible** — one toggle, no editing rc files by hand.
- **Multi-provider** — switch between presets without retyping URLs.
- **Translatable** — most cheap/fast/open-source providers speak OpenAI,
  not Anthropic. You'd otherwise need a separate proxy.
- **VSCode-aware** — the env wins inside the integrated terminal AND
  inside the Claude Code extension's spawned processes.
- **Login-free** — `CLAUDE_CODE_SKIP_LOGIN=1` makes the extension stop
  asking you to OAuth into Anthropic.

## Uninstall

```sh
claude-oneclick off               # back to Anthropic default
./install.sh --uninstall          # or .\install.ps1 -Uninstall on Windows
pip uninstall claude-oneclick
```

The uninstall step strips the marker block from your shell rc files,
removes our managed keys from VSCode settings, clears the Windows env
vars, and removes the desktop launcher. Your config and presets stay
in `~/.config/claude-oneclick/` (delete it manually to fully reset).

## Trust boundary

Everything runs locally under your user account. No daemons with
elevated privileges. No system-scope (HKLM / `/etc`) writes. No telemetry.
Your API keys live in `~/.config/claude-oneclick/config.json` (mode 0600
on POSIX); the UI never echoes them back to the browser after the first
save.

## Ports

- UI: **47823** (configurable in Settings)
- Proxy: **47824** (configurable in Settings)

These are picked from IANA's unassigned range — neither port is the
default for any major application as of writing.

## License

MIT.
