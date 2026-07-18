#!/usr/bin/env bash
set -euo pipefail

export QUEUECTL_DATABASE_URL="${QUEUECTL_DATABASE_URL:-sqlite:///./demo-queuectl.db}"

queuectl config set max-retries 3
queuectl config set backoff-base 2
queuectl enqueue '{"id":"demo-hello","command":"echo hello from queuectl","priority":"high"}'
queuectl enqueue '{"id":"demo-fail","command":"python -c \"import sys; sys.exit(1)\"","max_retries":2}'
queuectl status

echo "Run workers in another terminal:"
echo "  QUEUECTL_DATABASE_URL=$QUEUECTL_DATABASE_URL queuectl worker start --count 3"
echo
echo "Then inspect:"
echo "  queuectl list"
echo "  queuectl dlq list"
echo "  queuectl metrics"

