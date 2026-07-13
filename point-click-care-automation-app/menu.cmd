@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:menu
cls
echo ============================================================
echo                      GATEWAY PCC
echo ============================================================
echo.
echo   One app, two modes:
echo     * SERVER  = the website (accounts + facility vault).
echo                 Run this on your host / or locally to test.
echo     * DESKTOP = the launcher each user runs; it signs in to
echo                 a server and drives Firefox on their PC.
echo.
echo   1^) Install dependencies (isolated uv environment)
echo   2^) Run the SERVER  (website at http://127.0.0.1:5000)
echo   3^) Run the DESKTOP agent (for testing)
echo   4^) Build the DESKTOP installer (GatewayPCC-Setup.exe)
echo   5^) Exit
echo.
set /p choice="Select an option [1-5]: "

if "%choice%"=="1" goto install
if "%choice%"=="2" goto runserver
if "%choice%"=="3" goto rundesktop
if "%choice%"=="4" goto buildexe
if "%choice%"=="5" goto end
echo Invalid choice.
pause
goto menu

:install
echo.
echo --- Checking for uv ...
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Installing uv ...
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    echo.
    echo Please close and reopen this window so 'uv' is on PATH, then run option 1 again.
    pause
    goto menu
)
echo --- Creating isolated virtual environment with uv ...
uv venv
echo --- Installing dependencies ...
uv sync
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Review the output above.
    pause
    goto menu
)
echo.
echo Dependencies installed.
pause
goto menu

:runserver
echo.
if not exist ".venv" goto needdeps
echo --- Starting the Gateway PCC SERVER (website) at http://127.0.0.1:5000
echo --- Default admin: admin@gatewaypcc.com / Epicle@1234
uv run python -m backend.server
pause
goto menu

:rundesktop
echo.
if not exist ".venv" goto needdeps
echo --- Starting the Gateway PCC DESKTOP agent ...
echo --- A browser opens at http://127.0.0.1:5000 (set the Server on the sign-in screen).
echo --- (Tip: to avoid a clash when the server also runs here, use a different port:)
echo ---      set GATEWAY_PORT=5055  then run option 3 again.
uv run python -m backend.desktop
pause
goto menu

:buildexe
echo.
if not exist ".venv" goto needdeps
echo --- Installing the build tool (PyInstaller) into the environment ...
uv pip install pyinstaller
if errorlevel 1 (
    echo PyInstaller install failed. Review the output above.
    pause
    goto menu
)
echo.
echo --- Step 1 of 2: Building GatewayPCC.exe (this can take a minute) ...
uv run pyinstaller --noconfirm GatewayPCC.spec
if errorlevel 1 goto buildfail
if not exist "dist\GatewayPCC.exe" goto buildfail

echo.
echo --- Step 2 of 2: Building the one-click installer with Inno Setup ...
call :ensureinno
if not defined ISCC (
    echo.
    echo Could not find or install Inno Setup automatically.
    echo The standalone program is ready at:  %cd%\dist\GatewayPCC.exe
    echo.
    echo To get the one-click installer, install "Inno Setup 6" from
    echo     https://jrsoftware.org/isdl.php
    echo then run option 4 again.
    pause
    goto menu
)

"%ISCC%" installer.iss
if errorlevel 1 goto buildfail

echo.
echo ============================================================
echo   Build complete.
echo.
echo   SHARE THIS with your users (install / uninstall / reinstall
echo   via Windows "Add or remove programs"):
echo       %cd%\installer\GatewayPCC-Setup.exe
echo.
echo   (Standalone program also available at dist\GatewayPCC.exe)
echo ============================================================
pause
goto menu

:needdeps
echo No environment found. Run option 1 first.
pause
goto menu

:buildfail
echo.
echo Build failed. Review the output above.
pause
goto menu

:ensureinno
:: Make sure Inno Setup's compiler (ISCC.exe) is available, installing it if not.
call :findiscc
if defined ISCC exit /b 0
echo     Inno Setup not found - installing it now ...
where winget >nul 2>nul
if not errorlevel 1 (
    echo     - Installing via winget ...
    winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
    call :findiscc
    if defined ISCC exit /b 0
)
echo     - Downloading Inno Setup from jrsoftware.org ...
set "ISDL=%TEMP%\innosetup-latest.exe"
if exist "%ISDL%" del /q "%ISDL%" >nul 2>nul
powershell -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://jrsoftware.org/download.php/is.exe' -OutFile $env:ISDL -UseBasicParsing } catch { exit 1 }"
if not exist "%ISDL%" (
    echo     - Download failed (no internet / blocked?).
    exit /b 1
)
echo     - Running the Inno Setup installer (approve the Windows prompt if it appears) ...
"%ISDL%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
call :findiscc
if defined ISCC exit /b 0
exit /b 1

:findiscc
:: Locate the Inno Setup command-line compiler (ISCC.exe).
:: Subroutine so the %ProgramFiles(x86)% paths (which contain parentheses) are
:: never parsed inside an IF (...) block.
set "ISCC="
for /f "delims=" %%i in ('where ISCC 2^>nul') do set "ISCC=%%i"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
exit /b

:end
echo Goodbye.
endlocal
exit /b 0
