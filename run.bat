@echo off
echo Starting SFX Server...
echo.

REM Check if SFXServer.exe exists
if exist "SFXServer.exe" (
    echo Widget URL: http://127.0.0.1:5123/
    echo Admin Panel: http://127.0.0.1:5123/admin
    echo.
    echo Press Ctrl+C to stop the server.
    echo.
    echo Launching SFXServer.exe...
    SFXServer.exe
) else (
    echo SFXServer.exe not found!
    echo.
    echo Please run build.bat to create the executable first.
    echo.
    pause
)
