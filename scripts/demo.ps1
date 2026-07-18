$ErrorActionPreference = "Stop"

if (-not $env:QUEUECTL_DATABASE_URL) {
    $env:QUEUECTL_DATABASE_URL = "sqlite:///./demo-queuectl.db"
}

queuectl config set max-retries 3
queuectl config set backoff-base 2
queuectl enqueue '{"id":"demo-hello","command":"echo hello from queuectl","priority":"high"}'
queuectl enqueue '{"id":"demo-fail","command":"python -c \"import sys; sys.exit(1)\"","max_retries":2}'
queuectl status

Write-Host "Run workers in another terminal:"
Write-Host "  `$env:QUEUECTL_DATABASE_URL='$env:QUEUECTL_DATABASE_URL'; queuectl worker start --count 3"
Write-Host ""
Write-Host "Then inspect:"
Write-Host "  queuectl list"
Write-Host "  queuectl dlq list"
Write-Host "  queuectl metrics"

