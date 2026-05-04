# One-click installer for claude-oneclick on Windows (x86, x64, ARM64).
#
# Run from PowerShell as the current user (no admin needed):
#
#   irm https://raw.githubusercontent.com/xhashmi/claude-oneclick/main/install.ps1 | iex
#
# Or from a checkout:
#
#   .\install.ps1
#   .\install.ps1 -Uninstall
#
# Architecture: this script doesn't ship any native binaries. As long as
# the `python` on PATH matches your Windows arch (x86 / x64 / ARM64) the
# whole tool runs. CPython publishes installers for all three.

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSCommandPath

function Resolve-Python {
    foreach ($cmd in @("py", "python3", "python")) {
        $path = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($path) {
            try {
                $ver = & $path.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
                if ([Version]$ver -ge [Version]"3.9") { return $path.Source }
            } catch {}
        }
    }
    throw "Python 3.9+ not found. Install from https://www.python.org/downloads/windows/ (x64 or ARM64) or `winget install -e --id Python.Python.3.12`."
}

$Python = Resolve-Python
Write-Host "==> Python: $Python" -ForegroundColor Cyan

if ($Uninstall) {
    Write-Host "==> Removing shell + VSCode integration..." -ForegroundColor Cyan
    & $Python -m claude_oneclick._post_install --uninstall
    Write-Host "    To remove the package itself: pip uninstall claude-oneclick" -ForegroundColor DarkYellow
    exit 0
}

Write-Host "==> Installing claude-oneclick (pip --user -e)" -ForegroundColor Cyan
& $Python -m pip install --user --upgrade pip > $null
& $Python -m pip install --user -e $RepoRoot

Write-Host "==> Wiring system env + VSCode + launcher" -ForegroundColor Cyan
& $Python -m claude_oneclick _post_install

# pip --user puts the entrypoint scripts (claude-oneclick.exe, coc.exe) in
# %APPDATA%\Python\PythonX\Scripts, which is NOT on PATH out of the box.
# Idempotently append it to the User-scope PATH so the next-opened terminal
# resolves the command.
Write-Host "==> Ensuring user-scripts directory is on PATH" -ForegroundColor Cyan
$ScriptsDir = (& $Python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))").Trim()
if ($ScriptsDir -and (Test-Path $ScriptsDir)) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = if ($current) { $current.Split(";") } else { @() }
    $already = $false
    foreach ($e in $entries) {
        if ($e -and ($e.TrimEnd("\") -ieq $ScriptsDir.TrimEnd("\"))) { $already = $true; break }
    }
    if (-not $already) {
        $new = if ($current) { "$current;$ScriptsDir" } else { $ScriptsDir }
        [Environment]::SetEnvironmentVariable("Path", $new, "User")
        Write-Host "    Added $ScriptsDir to your User PATH." -ForegroundColor Green
        Write-Host "    Open a NEW terminal for it to take effect." -ForegroundColor Yellow
    } else {
        Write-Host "    Already on PATH ($ScriptsDir)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    Could not resolve user-scripts dir; you may need to add it manually." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==> Installed." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Close and reopen your terminal (so PATH and env vars refresh)."
Write-Host "  2. Pin the 'Claude OneClick' shortcut from the Start Menu, or run:"
Write-Host "       claude-oneclick ui"
Write-Host "  3. Pick a preset, paste your API key, flip the toggle ON."
Write-Host ""

if ($Launch) {
    Write-Host "==> Launching UI..."
    & $Python -m claude_oneclick ui
}
