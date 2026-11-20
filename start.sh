#!/bin/sh
set -e

mkdir -p /data/chrome /data/uploads
rm -f /data/chrome/SingletonLock /data/chrome/SingletonCookie /data/chrome/SingletonSocket

CHROME_BIN=$(ls /ms-playwright/chromium-*/chrome-linux/chrome /ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | head -n 1)
if [ -z "$CHROME_BIN" ]; then
  CHROME_BIN=$(command -v chromium || command -v google-chrome || true)
fi

if [ -n "$CHROME_BIN" ]; then
  "$CHROME_BIN" \
    --headless=new \
    --no-sandbox \
    --disable-dev-shm-usage \
    --remote-debugging-port=9222 \
    --remote-allow-origins='*' \
    --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
    --user-data-dir=/data/chrome \
    about:blank >/data/chrome.log 2>&1 &
fi

exec gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 "app:create_app()"
