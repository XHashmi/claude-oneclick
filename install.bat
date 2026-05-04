@echo off
rem Double-clickable installer for Windows (x86, x64, ARM64).
rem
rem Usage: in Explorer, open the unzipped claude-oneclick folder and
rem double-click this file. A Command Prompt window opens, runs the
rem install, and launches the web UI in your default browser.
rem
rem No commands typed.

setlocal EnableDelayedExpansion

cd /d "%~dp0"

cls
echo ================================================================
echo  Claude OneClick installer (Windows)
echo ================================================================
echo.
echo This will:
echo   1. Verify Python 3.9+ is available
echo   2. Install the package to your user account
echo   3. Wire up env vars + VSCode + Start Menu shortcut
echo   4. Open the web UI
echo.
echo Nothing requires admin / UAC. Works on x86, x64, ARM64.
echo.

rem ---- find Python ---------------------------------------------------------

set PY=
for %%C in (py python3 python) do (
  if not defined PY (
    where %%C >nul 2>&1
    if !errorlevel! equ 0 (
      %%C -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
      if !errorlevel! equ 0 set PY=%%C
    )
  )
)

if not defined PY (
  echo ==^> Python 3.9+ was not found.
  echo.
  echo You need to install it first. The recommended ways:
  echo.
  echo   1^) Microsoft Store: search for "Python 3.12" - one click,
  echo      auto-picks x64 or ARM64 for your machine.
  echo   2^) winget install -e --id Python.Python.3.12
  echo   3^) Official installer: https://www.python.org/downloads/windows/
  echo.
  echo Opening the python.org download page now.
  start "" "https://www.python.org/downloads/windows/"
  echo.
  echo After installing Python, come back and double-click this file again.
  echo.
  pause
  exit /b 1
)

echo ==^> Found Python: %PY%
%PY% --version
echo.

rem ---- delegate to install.ps1 --------------------------------------------

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Launch
if errorlevel 1 (
  echo.
  echo Installer reported an error. See the messages above.
  pause
  exit /b 1
)

echo.
echo Done. The Claude OneClick UI should now be open in your browser.
echo You can re-open it any time from the Start Menu or by running:
echo     claude-oneclick ui
echo.
pause
