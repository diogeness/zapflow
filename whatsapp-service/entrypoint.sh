#!/bin/sh
# Fix ownership of mounted volumes (host UID may differ from appuser)
chown -R appuser:appgroup /data/media /data/sessions 2>/dev/null || true

# Drop to appuser and exec the main process
exec su-exec appuser node --dns-result-order=ipv4first dist/index.js
