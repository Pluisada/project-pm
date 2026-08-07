#!/bin/bash

# Test script for running all tests in different modes
# Usage: ./test.sh [command]
#
# Commands:
#   frontend:unit    Run frontend unit tests (Vitest)
#   frontend:e2e     Run frontend E2E tests with dev server (Playwright)
#   frontend:docker  Run frontend E2E tests with Docker backend (Playwright)
#   backend:unit     Run backend unit tests (pytest)
#   backend:build    Run backend build validation
#   all:dev          Run all tests in development mode
#   all:docker       Run all tests with Docker (requires running container)
#   build            Build frontend for production
#   verify           Verify build artifacts

set -e

COMMAND="${1:-all:dev}"

echo "Running: $COMMAND"
echo ""

case "$COMMAND" in
  frontend:unit)
    echo "Running frontend unit tests..."
    cd frontend
    npm run test:unit
    cd ..
    ;;

  frontend:e2e)
    echo "Running frontend E2E tests (dev mode)..."
    cd frontend
    npm run test:e2e
    cd ..
    ;;

  frontend:docker)
    echo "Running frontend E2E tests (Docker backend)..."
    echo "NOTE: Requires Docker container running on http://localhost:8000"
    cd frontend
    npx playwright test --config=playwright.docker.config.ts
    cd ..
    ;;

  backend:unit)
    echo "Running backend unit tests..."
    cd backend
    python3 -m pytest test_main.py test_static_files.py -v
    cd ..
    ;;

  backend:build)
    echo "Validating backend Python syntax..."
    python3 -m py_compile backend/main.py backend/test_main.py backend/test_static_files.py
    echo "✓ Backend syntax valid"
    ;;

  build)
    echo "Building frontend for production..."
    cd frontend
    npm run build
    cd ..
    echo "✓ Frontend built to frontend/out/"
    ;;

  verify)
    echo "Verifying build artifacts..."
    bash verify_build.sh
    ;;

  all:dev)
    echo "Running all tests in development mode..."
    echo ""
    echo "1. Backend syntax validation..."
    python3 -m py_compile backend/main.py backend/test_main.py backend/test_static_files.py
    echo "   ✓ Backend syntax valid"
    echo ""
    echo "2. Frontend unit tests..."
    cd frontend
    npm run test:unit
    cd ..
    echo ""
    echo "3. Frontend E2E tests (dev mode)..."
    cd frontend
    npm run test:e2e
    cd ..
    echo ""
    echo "✓ All development tests passed!"
    ;;

  all:docker)
    echo "Running all tests with Docker backend..."
    echo ""
    echo "Prerequisite: Docker container running on http://localhost:8000"
    echo "Start with: ./scripts/start.sh"
    echo ""
    echo "1. Backend syntax validation..."
    python3 -m py_compile backend/main.py backend/test_main.py backend/test_static_files.py
    echo "   ✓ Backend syntax valid"
    echo ""
    echo "2. Frontend unit tests..."
    cd frontend
    npm run test:unit
    cd ..
    echo ""
    echo "3. Frontend E2E tests (Docker backend)..."
    cd frontend
    npx playwright test --config=playwright.docker.config.ts
    cd ..
    echo ""
    echo "✓ All Docker tests passed!"
    ;;

  *)
    echo "Unknown command: $COMMAND"
    echo ""
    echo "Available commands:"
    echo "  frontend:unit    Run frontend unit tests"
    echo "  frontend:e2e     Run frontend E2E tests with dev server"
    echo "  frontend:docker  Run frontend E2E tests with Docker backend"
    echo "  backend:unit     Run backend unit tests"
    echo "  backend:build    Validate backend Python syntax"
    echo "  build            Build frontend for production"
    echo "  verify           Verify build artifacts"
    echo "  all:dev          Run all tests in development mode"
    echo "  all:docker       Run all tests with Docker backend"
    exit 1
    ;;
esac

echo ""
echo "✓ Complete!"
