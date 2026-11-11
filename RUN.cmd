@echo off
setlocal

title JARVIS
cd /d "%~dp0"

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "SOURCE_ROOT=%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOCAL_TOOLS=%PROJECT_ROOT%\Tools"
set "LOCAL_CODEX="

for /d %%D in ("%PROJECT_ROOT%\Envirment\Codex*") do (
    if exist "%%~fD\codex.exe" set "LOCAL_CODEX=%%~fD\codex.exe"
)

if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] Source virtual environment not found:
    echo        %PROJECT_PYTHON%
    echo.
    echo Install Python dependencies from the Envirment directory first.
    pause
    exit /b 1
)

if not defined CODEX_CIL_CODEX_BIN set "CODEX_CIL_CODEX_BIN=%LOCAL_CODEX%"
if not exist "%CODEX_CIL_CODEX_BIN%" set "CODEX_CIL_CODEX_BIN=%LOCAL_CODEX%"
if not defined CODEX_CIL_TOOLS_DIR set "CODEX_CIL_TOOLS_DIR=%LOCAL_TOOLS%"
if not exist "%CODEX_CIL_TOOLS_DIR%" set "CODEX_CIL_TOOLS_DIR=%LOCAL_TOOLS%"

if not exist "%CODEX_CIL_CODEX_BIN%" (
    echo [ERROR] Codex runtime not found:
    echo        %CODEX_CIL_CODEX_BIN%
    pause
    exit /b 1
)

if not exist "%CODEX_CIL_TOOLS_DIR%" (
    echo [ERROR] Tools directory not found:
    echo        %CODEX_CIL_TOOLS_DIR%
    pause
    exit /b 1
)

set "PYTHONPATH=%SOURCE_ROOT%src;%PYTHONPATH%"
set "PYTHONUTF8=1"

if /I "%JARVIS_LAUNCH_DRY_RUN%"=="1" (
    echo [OK] Python: %PROJECT_PYTHON%
    echo [OK] Codex:  %CODEX_CIL_CODEX_BIN%
    echo [OK] Tools:  %CODEX_CIL_TOOLS_DIR%
    exit /b 0
)

echo Starting JARVIS...
"%PROJECT_PYTHON%" -m codex_cil
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo JARVIS exited with code %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
