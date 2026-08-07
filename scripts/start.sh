#!/bin/bash

# PM Backend - Start Script (Mac/Linux)

set -e

echo "Starting PM Backend..."

# Load environment variables from .env if it exists
if [ -f .env ]; then
  export $(cat .env | grep -v '#' | xargs)
  echo "✓ Loaded environment variables from .env"
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
  echo "✗ Docker is not running. Please start Docker and try again."
  exit 1
fi

echo "✓ Docker is running"

# Build the Docker image
echo "Building Docker image..."
docker-compose build

# Start the container
echo "Starting container..."
docker-compose up -d

# Wait for container to be ready
echo "Waiting for service to be ready..."
sleep 3

# Check health
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
  echo ""
  echo "✓ PM Backend is running!"
  echo "  URL: http://localhost:8000"
  echo "  API: http://localhost:8000/api/health"
  echo ""
  echo "To stop the server, run: ./scripts/stop.sh"
else
  echo "✗ Failed to connect to server. Check logs with: docker-compose logs app"
  exit 1
fi
