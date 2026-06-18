#!/bin/bash

# Exit on error
set -e

echo "=== System Information ==="
uname -a
python3 --version
google-chrome --version || true

echo "=== Installing dependencies ==="
pip install -r requirements.txt

# Create profile directory
mkdir -p .chrome_profile_uc

echo "=== Starting FastAPI Server in background ==="
python3 chatgpt_api.py &
SERVER_PID=$!

# Wait for server to start up
sleep 8

echo "=== Starting Tunnel (localtunnel) ==="
# Use npx to launch localtunnel on port 8000
npx localtunnel --port 8000 > lt.log 2>&1 &
LT_PID=$!

# Wait for localtunnel to print the URL
sleep 6

echo "=== Your Public API Endpoint ==="
cat lt.log
echo "================================"

# Keep script running and monitor processes
while kill -0 $SERVER_PID 2>/dev/null; do
    # Periodically print server status
    sleep 30
done

# Cleanup if exited
kill $LT_PID 2>/dev/null || true
