@echo off
echo Building Kick SFX Widget executable...

REM Check if PyInstaller is installed
python -m pip show pyinstaller >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
)

REM Ensure eventlet is installed
echo Installing/checking eventlet...
python -m pip install eventlet

REM Create executable with dependencies
echo Creating executable...
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name "SFXServer" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "config.json;." ^
    --add-data "requirements.txt;." ^
    --hidden-import flask_socketio ^
    --hidden-import socketio ^
    --hidden-import engineio ^
    --hidden-import eventlet ^
    --hidden-import eventlet.wsgi ^
    --hidden-import eventlet.green ^
    --hidden-import eventlet.green.threading ^
    --hidden-import eventlet.green.socket ^
    --hidden-import eventlet.green.ssl ^
    --hidden-import gevent ^
    --hidden-import gevent.socket ^
    --hidden-import gevent.threading ^
    --hidden-import threading ^
    --hidden-import queue ^
    --hidden-import engineio.async_drivers.eventlet ^
    --hidden-import engineio.async_drivers.threading ^
    --hidden-import socketio.async_drivers.eventlet ^
    --hidden-import socketio.async_drivers.threading ^
    --hidden-import dns.resolver ^
    --collect-all flask_socketio ^
    --collect-all socketio ^
    --collect-all engineio ^
    server.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful!
    echo Executable created: dist\SFXServer.exe
    echo.
    echo IMPORTANT: Copy these folders next to the .exe file:
    echo - templates\
    echo - static\  
    echo - sfx_library\
    echo - config.json
    echo.
) else (
    echo Build failed. Check for errors above.
)

pause
