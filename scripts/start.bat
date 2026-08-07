@echo off
REM PM Backend - Start Script (Windows)

echo Starting PM Backend...

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running. Please start Docker and try again.
    exit /b 1
)

echo Docker is running

REM Build the Docker image
echo Building Docker image...
docker-compose build
if errorlevel 1 (
    echo Failed to build Docker image
    exit /b 1
)

REM Start the container
echo Starting container...
docker-compose up -d
if errorlevel 1 (
    echo Failed to start container
    exit /b 1
)

REM Wait for container to be ready
echo Waiting for service to be ready...
timeout /t 3 /nobreak

REM Check health (using curl if available, otherwise just report)
echo.
echo PM Backend is starting!
echo   URL: http://localhost:8000
echo   API: http://localhost:8000/api/health
echo.
echo To stop the server, run: scripts\stop.bat
