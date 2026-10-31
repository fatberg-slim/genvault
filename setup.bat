@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo === genvault v2 setup ===
echo.

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo ERROR: Python was not found.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" during install, then run this again.
  pause
  exit /b 1
)
echo Using Python: %PY%
echo.

echo Creating local environment (.venv) ...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: could not create the virtual environment.
  pause
  exit /b 1
)

echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r "requirements.txt" --disable-pip-version-check
if errorlevel 1 (
  echo ERROR: dependency install failed. Check your internet connection and run again.
  pause
  exit /b 1
)
echo.

rem --- library location: ask the user, never silently write to C:\ ------------
if exist "config.json" (
  echo config.json already exists - keeping your current settings.
  ".venv\Scripts\python.exe" -c "import json;c=json.load(open('config.json',encoding='utf-8'));print('  library:',c.get('library_root'));print('  port:   ',c.get('port'))"
  goto ffmpegcheck
)

echo Where should your genvault library live? This folder will hold ALL your
echo imported media, the database and thumbnails - pick a drive with space.
echo.
set "DEFLIB=%USERPROFILE%\genvault"
set "LIBROOT="
set /p LIBROOT=Library folder [press Enter for %DEFLIB%]:
if not defined LIBROOT set "LIBROOT=%DEFLIB%"

set "GVPORT="
set /p GVPORT=Port [press Enter for 8077]:
if not defined GVPORT set "GVPORT=8077"

".venv\Scripts\python.exe" "init_config.py" "%LIBROOT%" "%GVPORT%"
if errorlevel 1 (
  echo ERROR: could not write config.json
  pause
  exit /b 1
)
echo.

:ffmpegcheck
set "FFOK="
where ffmpeg >nul 2>nul && set "FFOK=1"
if not defined FFOK if exist "C:\ffmpeg\bin\ffmpeg.exe" set "FFOK=1"
if defined FFOK (
  echo ffmpeg: found - good.
) else (
  echo WARNING: ffmpeg was NOT found. Thumbnails, video metadata and audio
  echo waveforms need it.
  echo   1^) Download "ffmpeg-release-essentials.zip" from https://www.gyan.dev/ffmpeg/builds/
  echo   2^) Unzip it and copy ffmpeg.exe and ffprobe.exe into  C:\ffmpeg\bin
)
echo.
echo ============================================================
echo  Setup complete.  Start genvault by double-clicking:
echo     genvault-tray.bat      (tray icon - recommended)
echo  then open  http://127.0.0.1:8077   (or your chosen port)
echo ============================================================
pause
