@echo off
rem mc-art: the deterministic engine of this skill. No model, no credentials.
rem
rem   bin\mc-art.cmd list-groups --source <jar> [--filter bow]
rem   bin\mc-art.cmd evidence    --source <jar> --name bow [--member bow_standby]
rem   bin\mc-art.cmd render      --plan my.plan.json --out outputs\mine
rem   bin\mc-art.cmd measure     a.png b.png c.png --baseline src.png
rem
rem The Windows entry point. bin/mc-art is the POSIX one; bin/mc-art.ps1 is the
rem PowerShell-native one. All three find Python the same way.
rem
rem A candidate counts only if it actually RUNS and prints exactly 1. "The command
rem exists" is not evidence: on Windows `python3` is often a 0-byte Microsoft
rem Store stub that exists, is found by `where`, and exits 9009 without running
rem anything -- which is exactly how this engine looked "missing" on a machine
rem that had Python installed all along.
setlocal enableextensions
pushd "%~dp0.." || exit /b 1

set "PY="
if defined MC_ART_PYTHON call :mc_art_try "%MC_ART_PYTHON%" ""
if not defined PY call :mc_art_try python ""
if not defined PY call :mc_art_try python3 ""
if not defined PY call :mc_art_try py "-3"
if not defined PY call :mc_art_try py ""

if not defined PY (
  echo mc-art: no working Python found. Tried %%MC_ART_PYTHON%%, python, python3, py -3, py. 1>&2
  echo mc-art: each candidate must run -c "print(1)" and print 1. 1>&2
  echo mc-art: set MC_ART_PYTHON to a real interpreter if yours is elsewhere. 1>&2
  popd
  endlocal
  exit /b 127
)

%PY% -m mc_art %*
set "MC_ART_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %MC_ART_CODE%

:mc_art_try
rem %~1 = executable, %~2 = extra argument (may be empty)
set "MC_ART_OUT="
for /f "delims=" %%O in ('%~1 %~2 -c "print(1)" 2^>nul') do set "MC_ART_OUT=%%O"
if "%MC_ART_OUT%"=="1" set "PY=%~1 %~2"
set "MC_ART_OUT="
exit /b 0
