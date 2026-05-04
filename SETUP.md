# Setup guide

End-to-end walkthrough: from a fresh machine to Claude Code routed
through DeepSeek V4 Pro (or any other provider). About 5 minutes.

## 0. Prerequisites

- **Python 3.9 or newer.** Check with `python3 --version`.
  - Linux: `sudo apt install python3` / `sudo dnf install python3` / etc.
  - macOS: `brew install python` or
    [python.org installer](https://www.python.org/downloads/macos/).
  - Windows (any arch — x86, x64, ARM64):
    `winget install -e --id Python.Python.3.12` or the
    [python.org installer](https://www.python.org/downloads/windows/).
    On ARM64 Windows, choose the ARM64 installer; everything else just
    works.
- **Claude Code** already installed (CLI and/or VSCode extension).
- An **API key** from the provider you want to route to. Common ones:
  - DeepSeek — https://platform.deepseek.com → API Keys
  - NVIDIA NIMs — https://build.nvidia.com → your account → API keys
  - OpenRouter — https://openrouter.ai/keys
  - Groq — https://console.groq.com/keys
  - Together AI — https://api.together.xyz/settings/api-keys
  - Fireworks — https://fireworks.ai/account/api-keys
  - Local Ollama — no key needed; just run `ollama serve`.

## 1. Install

### Option A — Zero-terminal install (double-click)

If you'd rather not type any commands, download the repo as a ZIP from
GitHub (**Code → Download ZIP** on the repo page), unzip it, and
double-click:

- **macOS:** `install.command`
- **Windows:** `install.bat`
- **Linux:** `install-linux.desktop` (right-click → *Allow Launching*
  on first use)

A terminal pops up briefly to run the install. The web UI opens
automatically when it's done. Press any key to close the window.

If Python isn't already installed, the installer opens the python.org
download page for you. On Windows, the Microsoft Store's "Python 3.12"
listing is the easiest path — one click and it picks the right
architecture (x64 or ARM64) automatically.

### Option B — One-line terminal install

If you're already in a terminal:

#### Linux / macOS

```sh
git clone https://github.com/xhashmi/claude-oneclick.git ~/.claude-oneclick
~/.claude-oneclick/install.sh --launch
```

The installer:

1. `pip install --user -e` the package — `claude-oneclick` and the short
   alias `coc` land on your PATH.
2. Drops a `# >>> claude-oneclick >>>` block into `~/.bashrc`, `~/.zshrc`,
   and `~/.profile`. The block is a single `source` line; reversible.
3. Patches every VSCode user `settings.json` it can find
   (`~/.config/Code/User/settings.json`,
   `~/.config/VSCodium/User/settings.json`, etc.) under the
   `terminal.integrated.env.linux` / `.osx` key.
4. Installs a desktop launcher (`.desktop` on Linux,
   `~/Applications/claude-oneclick.command` on macOS).
5. Opens the web UI at http://127.0.0.1:47823 .

#### Windows (x86, x64, ARM64)

In PowerShell (no admin needed):

```powershell
git clone https://github.com/xhashmi/claude-oneclick.git $HOME\.claude-oneclick
$HOME\.claude-oneclick\install.ps1 -Launch
```

The installer:

1. `pip install --user -e` the package.
2. Writes `ANTHROPIC_*` and `CLAUDE_CODE_SKIP_LOGIN` env vars under
   `HKCU\Environment` via `setx` — that broadcasts `WM_SETTINGCHANGE`
   so processes spawned afterward (Explorer-launched VSCode, Claude
   Code) inherit them.
3. Drops a marker block into your PowerShell `$PROFILE`
   (`Documents\PowerShell\Microsoft.PowerShell_profile.ps1` and the
   Windows PowerShell 5 equivalent).
4. Sets `HKCU\Software\Microsoft\Command Processor\AutoRun` to source
   our env in every new `cmd.exe`.
5. Patches every VSCode user `settings.json` under
   `terminal.integrated.env.windows`.
6. Creates a Start-Menu shortcut **Claude OneClick** + a `.cmd` launcher.

## 2. First-run setup wizard

The launcher opens http://127.0.0.1:47823. The wizard is 4 steps:

1. **Welcome.** Click *Get started*.
2. **Pick a provider.** Click any of the built-in cards (DeepSeek V4
   Pro, NVIDIA NIMs, …) or hit *Skip wizard* if you want to add a
   custom one.
3. **Paste your API key.** The hint text under the input shows where to
   get a key for the provider you picked. The key is stored in
   `~/.config/claude-oneclick/config.json` (mode `0600` on POSIX) and
   never leaves your machine except when calling the upstream.
4. **Finish.** Click *Finish & toggle ON*. The big toggle in the
   header flips to ON, the proxy starts, and your env files get
   rewritten.

## 3. Use Claude Code

Open a **new** terminal (so it inherits the env block) and run
`claude`. Claude Code reads `ANTHROPIC_BASE_URL` from the env and sends
its requests to the local translation proxy on `127.0.0.1:47824`,
which forwards them to your provider in OpenAI format.

Verify with:

```sh
echo $ANTHROPIC_BASE_URL
# http://127.0.0.1:47824
echo $ANTHROPIC_MODEL
# deepseek-v4-pro
```

In VSCode: open a new window after the install. The Claude Code
extension's child processes inherit the env via
`terminal.integrated.env.<os>` in `settings.json`.

## 4. Customize a preset

In the UI, click the preset in the sidebar, then:

- **General** — base URL, API key, format (OpenAI vs Anthropic).
- **Models** — *Refresh* pulls the live model list from the provider
  (`/v1/models` or Ollama's `/api/tags`); type or pick the main + small
  models. Add aliases if you want
  `claude-sonnet-4` to map to `deepseek-v4-pro`.
- **Sampling** — default temperature, top_p, top_k, max_tokens, request
  timeout, retries, retry backoff. Defaults only apply when Claude
  Code's request omits them.
- **Headers** — extra HTTP headers (OpenRouter's `HTTP-Referer` /
  `X-Title`, NVIDIA's `accept`, enterprise `X-` headers).
- **Advanced** — system-prompt prefix/suffix, *Disable streaming*,
  *Pass cache_control markers*, **Enable reasoning mode** + a
  3-stop slider for `reasoning_effort` (low / medium / high).
- **Logs** — live tail of `proxy.log` for debugging.

## 5. Settings

Click the cog at the bottom of the sidebar:

- **UI port / Proxy port** — change the localhost ports.
- **Theme** — auto / light / dark.
- **Log level** — info / debug.
- **Autostart proxy when toggle is ON** — pre-warm the proxy so the
  first Claude Code request doesn't pay the spawn latency.
- **Open browser when running `claude-oneclick ui`** — disable for
  headless setups.
- **Skip Claude Code login** — `CLAUDE_CODE_SKIP_LOGIN=1`. Stops the
  VSCode extension from prompting for an Anthropic OAuth login when
  you're routing to a custom provider.
- **Start Claude OneClick when the system starts** — registers a
  per-OS autostart entry:
  - Linux: `~/.config/autostart/claude-oneclick.desktop`
  - macOS: `~/Library/LaunchAgents/com.claude-oneclick.boot.plist`
    (loaded with `launchctl load -w`)
  - Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ClaudeOneClick`

  At boot it runs `python -m claude_oneclick _boot`, which re-applies
  the saved state (re-exports env on Windows; restarts the proxy if
  the toggle was ON).

- **Model discovery cache TTL** — how long to remember a provider's
  `/v1/models` reply.

## 6. CLI cheatsheet

```sh
claude-oneclick ui                         # open the UI
claude-oneclick presets                    # list every preset
claude-oneclick set-key deepseek-v4-pro <KEY>
claude-oneclick use deepseek-v4-pro
claude-oneclick on                         # toggle ON
claude-oneclick status                     # current state
claude-oneclick models deepseek-v4-pro     # live /v1/models from upstream
claude-oneclick set deepseek-v4-pro reasoning_enabled true
claude-oneclick set deepseek-v4-pro reasoning_effort high
claude-oneclick add-alias deepseek-v4-pro claude-sonnet-4 deepseek-v4-pro
claude-oneclick set-header openrouter HTTP-Referer https://my-app.example
claude-oneclick autostart enable           # start at boot
claude-oneclick autostart disable
claude-oneclick off                        # back to Anthropic
claude-oneclick toggle                     # flip ON↔OFF
```

## 7. Troubleshooting

- **`claude-oneclick: command not found`** — your `~/.local/bin` isn't
  on PATH yet. Fix: `export PATH="$HOME/.local/bin:$PATH"` or relogin.
- **`claude` still talks to api.anthropic.com** — you opened the
  terminal *before* turning the toggle ON. Open a new terminal.
- **VSCode integrated terminal doesn't see the env** — restart the
  VSCode window (`Developer: Reload Window`).
- **Provider returns 401** — the API key on the preset is wrong. Open
  the UI → General tab → re-paste the key.
- **Provider returns 400 / weird shape errors** — try ticking
  *Disable streaming* on the Advanced tab. Some providers' SSE is
  flaky.
- **`Refresh model list` says "401"** — the upstream needs the API
  key for `/v1/models`. Save the key first, then refresh.
- **Windows: env vars don't appear** — close and reopen the terminal.
  `setx` only takes effect for new processes.

## 8. Uninstall

```sh
~/.claude-oneclick/install.sh --uninstall   # POSIX
.\.claude-oneclick\install.ps1 -Uninstall   # Windows
pip uninstall claude-oneclick
```

That removes the marker block from your shell rc files, strips our
managed keys from VSCode `settings.json`, clears the Windows
`HKCU\Environment` entries, removes the desktop launcher, and removes
the autostart entry. Your config + presets stay in
`~/.config/claude-oneclick/` — delete it manually for a full reset.
