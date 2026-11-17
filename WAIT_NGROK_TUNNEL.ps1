$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$urlFile = Join-Path $logDir 'ngrok_public_url.txt'
Remove-Item $urlFile -ErrorAction SilentlyContinue
$cfg = @{}
Get-Content (Join-Path $root '.env') | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $parts = $line.Split('=',2)
        $cfg[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
}
$port = [string]$cfg['NGROK_INSPECTOR_PORT']
if (-not $port) { $port = '4040' }
if ($port -notmatch '^\d{1,5}$' -or [int]$port -gt 65535 -or [int]$port -lt 1) { throw 'Invalid NGROK_INSPECTOR_PORT' }
$api = "http://127.0.0.1:$port/api/tunnels"
$public = $null
for ($attempt = 0; $attempt -lt 35; $attempt++) {
    try {
        $data = Invoke-RestMethod -Uri $api -TimeoutSec 3
        $public = @($data.tunnels | Where-Object { $_.public_url -match '^https://' } | Select-Object -First 1)
        if ($public.Count -gt 0 -and $public[0].public_url) { break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $public -or -not $public[0].public_url) {
    Write-Warning "ngrok inspector did not report an HTTPS URL at $api"
    docker compose --profile public logs --tail 35 ngrok
    throw 'ngrok is not connected. Check your authtoken, account bandwidth allowance, and tunnel logs.'
}
$url = [string]$public[0].public_url
Set-Content -Path $urlFile -Value $url -Encoding UTF8
Write-Host "[ngrok] Public dashboard: $url"
Write-Host "[ngrok] Saved public URL to logs\ngrok_public_url.txt"
try { Start-Process $url | Out-Null } catch { Write-Warning 'Cannot open browser automatically. Copy the URL above.' }
