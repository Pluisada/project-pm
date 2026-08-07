#!/bin/bash

# PM Backend - Stop Script (Mac/Linux)

set -e

echo "Stopping PM Backend..."

if docker-compose down; then
  echo "✓ PM Backend stopped successfully"
else
  echo "✗ Failed to stop containers"
  exit 1
fi
