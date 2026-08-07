@echo off
REM PM Backend - Stop Script (Windows)

echo Stopping PM Backend...

docker-compose down
if errorlevel 1 (
    echo Failed to stop containers
    exit /b 1
)

echo PM Backend stopped successfully
