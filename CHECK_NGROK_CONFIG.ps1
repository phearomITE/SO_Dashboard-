$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'

if (-not (Test-Path $envPath)) {
    Write-Error '.env was not found.'
    exit 1
}

$config = @{}
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $name, $value = $line.Split('=', 2)
        $config[$name.Trim()] = $value.Trim()
    }
}

$token = [string]$config['NGROK_AUTHTOKEN']
$dashboardToken = [string]$config['DASHBOARD_ADMIN_TOKEN']

if (-not $token -or $token -match 'CHANGE_ME|REPLACE_WITH') {
    Write-Error 'Set a newly generated NGROK_AUTHTOKEN in .env.'
    exit 1
}
if (-not $dashboardToken -or $dashboardToken.Length -lt 32 -or $dashboardToken -match 'CHANGE_ME|REPLACE_WITH') {
    Write-Error 'Set DASHBOARD_ADMIN_TOKEN to a private random value of at least 32 characters.'
    exit 1
}

Write-Host 'ngrok tunnel and dashboard sign-in settings are present and meet minimum length checks.'

# Validate all required secrets before Docker Compose begins.
foreach ($name in @('DB_PASSWORD', 'TELEGRAM_BOT_TOKEN')) {
    $value = [string]$config[$name]
    if (-not $value -or $value -match 'CHANGE_ME|REPLACE_WITH') {
        Write-Error "Set $name in .env before starting the bot and dashboard."
        exit 1
    }
}
