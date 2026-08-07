#!/bin/bash

echo "Verifying Part 3: Frontend Build Integration"
echo ""

# Check frontend build exists
echo "1. Frontend Build Files:"
if [ -f "frontend/out/index.html" ]; then
  echo "  ✓ index.html exists"
  SIZE=$(du -sh frontend/out | cut -f1)
  echo "    Size: $SIZE"
else
  echo "  ✗ index.html NOT found"
  exit 1
fi

if [ -d "frontend/out/_next" ]; then
  echo "  ✓ _next directory (JS/CSS) exists"
  FILE_COUNT=$(find frontend/out/_next -type f | wc -l)
  echo "    Files: $FILE_COUNT"
else
  echo "  ✗ _next directory NOT found"
  exit 1
fi

# Check for no node_modules in build
echo ""
echo "2. Build Portability (no node_modules):"
if grep -r "node_modules" frontend/out 2>/dev/null; then
  echo "  ✗ WARNING: node_modules references found"
else
  echo "  ✓ No node_modules references (portable)"
fi

# Check static assets
echo ""
echo "3. Static Assets:"
ASSET_COUNT=$(find frontend/out -type f \( -name "*.css" -o -name "*.js" -o -name "*.svg" -o -name "*.ico" \) | wc -l)
echo "  ✓ Found $ASSET_COUNT static files"

# Check for required files
echo ""
echo "4. Required Files:"
for file in "favicon.ico" "_not-found.html"; do
  if [ -f "frontend/out/$file" ]; then
    echo "  ✓ $file"
  else
    echo "  ⚠ $file missing"
  fi
done

# Build size check
echo ""
echo "5. Build Size:"
TOTAL_SIZE=$(du -sk frontend/out | cut -f1)
SIZE_MB=$((TOTAL_SIZE / 1024))
echo "  Total: ${SIZE_MB}MB"
if [ $SIZE_MB -lt 10 ]; then
  echo "  ✓ Under 10MB limit"
else
  echo "  ⚠ WARNING: Over 10MB limit"
fi

echo ""
echo "✓ Build verification complete!"
