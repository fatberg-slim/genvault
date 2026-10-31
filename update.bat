@echo off
cd /d "%~dp0"
echo === genvault updater ===
echo.

if not exist "serve.py" (
  echo ERROR: this does not look like a genvault folder.
  echo Put update.bat inside your genvault folder and run it from there.
  pause
  exit /b 1
)

rem --- locate the new zip (drag it onto update.bat, or auto-find in Downloads) ---
set "ZIP=%~1"
if not "%ZIP%"=="" goto havezip
echo Looking for the newest genvault*.zip in your Downloads folder...
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "$z=Get-ChildItem \"$env:USERPROFILE\Downloads\genvault*.zip\" -ErrorAction SilentlyContinue ^| Sort-Object LastWriteTime -Descending ^| Select-Object -First 1; if($z){$z.FullName}"`) do set "ZIP=%%F"

:havezip
if "%ZIP%"=="" (
  echo Could not find an update zip.
  echo Drag the downloaded genvault zip onto update.bat, or put it in your Downloads folder, then run again.
  pause
  exit /b 1
)
if not exist "%ZIP%" (
  echo ERROR: zip not found: %ZIP%
  pause
  exit /b 1
)
echo Using update: %ZIP%
echo.

echo IMPORTANT: quit genvault first  (tray icon -^> Quit)  so its files are not in use.
echo Press any key once genvault is closed, or close this window to cancel.
pause >nul

set "STAGE=%TEMP%\genvault-update"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
echo Extracting...
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%STAGE%' -Force"
if errorlevel 1 (
  echo ERROR: could not extract the zip.
  pause
  exit /b 1
)

rem --- the zip wraps a genvault\ folder; fall back to the zip root if not ---
set "SRC=%STAGE%\genvault"
if not exist "%SRC%\serve.py" for /d %%D in ("%STAGE%\*") do if exist "%%D\serve.py" set "SRC=%%D"
if not exist "%SRC%\serve.py" set "SRC=%STAGE%"
if not exist "%SRC%\serve.py" (
  echo ERROR: that zip does not contain a genvault install.
  pause
  exit /b 1
)

rem --- copy new program files over this folder ---------------------------------
rem NEVER touched: config.json, your library, the database, .venv, logs.
echo Updating files...
robocopy "%SRC%" "%CD%" /E /R:1 /W:1 /XF config.json update.bat /XD .venv logs __pycache__ .git >nul
if %ERRORLEVEL% GEQ 8 (
  echo ERROR: copying files failed. Make sure genvault is fully closed, then run update.bat again.
  pause
  exit /b 1
)

echo Refreshing dependencies (quick if nothing changed)...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install -r "requirements.txt" --quiet --disable-pip-version-check
) else (
  echo No .venv yet - running first-time setup instead.
  call setup.bat
  exit /b 0
)

echo.
echo ============================================================
echo  Update complete.  Start genvault as usual:
echo     genvault-tray.bat
echo  The version next to the logo should match the changelog.
echo  Any database schema changes migrate automatically on start.
echo ============================================================
pause
