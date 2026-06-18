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
# Use npx -y to avoid interactive package prompts in CI
npx -y localtunnel --port 8000 > lt.log 2>&1 &
LT_PID=$!

echo "=== Waiting for Public API Endpoint ==="
for i in {1..12}; do
    if grep -q "url is" lt.log 2>/dev/null; then
        break
    fi
    sleep 2
done

echo "=== Your Public API Endpoint ==="
cat lt.log
echo "================================"

# Keep script running and monitor processes
while kill -0 $SERVER_PID 2>/dev/null; do
    # Periodically check if localtunnel is still alive, restart if dead
    if ! kill -0 $LT_PID 2>/dev/null; then
        echo "Localtunnel died, restarting..."
        npx -y localtunnel --port 8000 > lt.log 2>&1 &
        LT_PID=$!
        sleep 5
        cat lt.log
    fi
    sleep 15
done

# Cleanup if exited
kill $LT_PID 2>/dev/null || true
