@echo off
setlocal EnableExtensions DisableDelayedExpansion
title UtopiaPhyCyclus

set "PROJECT_DIR=%~dp0"
pushd "%PROJECT_DIR%" >nul 2>&1
if errorlevel 1 goto :folder_error

set "VENV_DIR=%CD%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "MAIN_APP=%CD%\utopia_phycyclus_app.py"
set "REQUIREMENTS=%CD%\requirements.txt"

echo ========================================================
echo UtopiaPhyCyclus - High School Physics Question Bank
echo ========================================================
echo.

if not exist "%MAIN_APP%" goto :app_error
if not exist "%REQUIREMENTS%" goto :requirements_error
if not exist "%VENV_PYTHON%" goto :find_python

:venv_ready
echo Starting UtopiaPhyCyclus...
"%VENV_PYTHON%" -m services.launcher %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto :runtime_error
goto :success

:find_python
echo [SETUP] Creating a Python 3.10 - 3.12 virtual environment...
for %%V in (3.12 3.11 3.10) do call :try_py %%V
if defined PYTHON_COMMAND goto :create_venv

python -c "import sys; sys.exit(0 if (3,10) le sys.version_info[:2] and sys.version_info[:2] le (3,12) and sys.maxsize gt 2**32 else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_COMMAND=python"
if defined PYTHON_COMMAND goto :create_venv
goto :python_error

:try_py
if defined PYTHON_COMMAND exit /b 0
py -%1 -c "import sys; sys.exit(0 if sys.maxsize gt 2**32 else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_COMMAND=py -%1"
exit /b 0

:create_venv
echo Using Python:
%PYTHON_COMMAND% --version
%PYTHON_COMMAND% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_error
if not exist "%VENV_PYTHON%" goto :venv_error
goto :venv_ready

:folder_error
echo [ERROR] Cannot open the project folder.
goto :failed

:app_error
echo [ERROR] utopia_phycyclus_app.py was not found.
echo Keep this launcher in the project root folder.
goto :failed

:requirements_error
echo [ERROR] requirements.txt was not found.
goto :failed

:dependency_error
echo.
echo [ERROR] Dependency installation failed.
echo Confirm that the Python package index is reachable and try again.
goto :failed

:port_error
echo.
echo [ERROR] No free port was found in the range 8501-8510.
echo Close an existing UtopiaPhyCyclus window and try again.
goto :failed

:python_error
echo [ERROR] A usable 64-bit CPython 3.10 - 3.12 was not found.
echo Install Python 3.12 with Python Launcher and Add to PATH enabled.
goto :failed

:venv_error
echo [ERROR] The Python virtual environment could not be created.
goto :failed

:runtime_error
echo.
echo [ERROR] The service stopped with exit code %EXIT_CODE%.
goto :failed

:failed
popd
pause
exit /b 1

:success
popd
exit /b 0
