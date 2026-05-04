@echo off
rem Double-click to open the Claude OneClick UI.
rem Assumes you ran install.bat at least once.
rem
rem Steps: pick a Python that has the package, launch `python -m
rem claude_oneclick ui`. The UI server will open the browser tab
rem itself.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Try the installed `claude-oneclick` command first - shortest path.
where claude-oneclick >nul 2>&1
if !errorlevel! equ 0 (
  start "" claude-oneclick ui
  exit /b 0
)

rem Fall back to module invocation via the same Python that has it.
set PY=
for %%C in (py python3 python) do (
  if not defined PY (
    where %%C >nul 2>&1
    if !errorlevel! equ 0 (
      %%C -c "import claude_oneclick" >nul 2>&1
      if !errorlevel! equ 0 set PY=%%C
    )
  )
)

if not defined PY (
  echo Claude OneClick is not installed yet.
  echo Double-click install.bat first, then come back here.
  pause
  exit /b 1
)

start "" %PY% -m claude_oneclick ui
exit /b 0
