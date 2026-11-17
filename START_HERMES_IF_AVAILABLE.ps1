# Invoked automatically by RUN_NGROK_DASHBOARD.bat. No separate startup command.
# Ollama runs on Windows host; its native GPU acceleration is independent of Docker.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$settings = @{}
Get-Content (Join-Path $root '.env') -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z_0-9]*)\s*=\s*(.*?)\s*$') {
        $settings[$matches[1]] = $matches[2].Trim('"').Trim("'")
    }
}
function Setting([string]$key, [string]$default) {
    if ($settings.ContainsKey($key) -and $settings[$key]) { return $settings[$key] }
    return $default
}
function Notice([string]$message) { Write-Host "[Hermes GPU] $message" }

if ((Setting 'AUTO_START_HERMES' '1') -eq '0') {
    Notice 'Auto-start disabled. Dashboard continues with rule-based reason summaries.'
    exit 0
}

$ollama = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
if (-not $ollama) { $ollama = Get-Command 'ollama' -ErrorAction SilentlyContinue }
$ollamaExe = if ($ollama) { $ollama.Source } else { $null }
if (-not $ollamaExe) {
    $paths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:PROGRAMFILES 'Ollama\ollama.exe')
    )
    foreach ($path in $paths) {
        if (Test-Path $path) { $ollamaExe = $path; break }
    }
}
if (-not $ollamaExe) {
    Notice 'ERROR: Ollama executable not installed. Hermes is unavailable.'
    exit 2
}

$gpuMode = (Setting 'OLLAMA_GPU_MODE' 'auto').ToLowerInvariant()
if ($gpuMode -notin @('auto', 'cpu')) {
    Notice "Unknown OLLAMA_GPU_MODE=$gpuMode, using auto."
    $gpuMode = 'auto'
}
if ($gpuMode -eq 'cpu') {
    # Explicit manual troubleshooting mode; never force CPU by default.
    $env:CUDA_VISIBLE_DEVICES = '-1'
    $env:HIP_VISIBLE_DEVICES = '-1'
    Notice 'CPU-only troubleshooting mode selected.'
} else {
    # Ollama detects suitable GPUs automatically. No manual CUDA device selector.
    $nvidia = Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue
    if (-not $nvidia) { $nvidia = Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue }
    if ($nvidia) {
        try {
            $names = @(& $nvidia.Source --query-gpu=name --format=csv,noheader 2>$null)
            if ($LASTEXITCODE -eq 0 -and $names.Count -gt 0) {
                Notice ('Detected NVIDIA GPU: ' + (($names | Where-Object { $_ }) -join ', '))
            } else { Notice 'NVIDIA utility found, but it could not report a usable GPU.' }
        } catch { Notice 'Could not query NVIDIA GPU.' }
    } else {
        try {
            $amd = @(Get-CimInstance Win32_VideoController -ErrorAction Stop | Where-Object { $_.Name -match 'AMD|Radeon' } | Select-Object -ExpandProperty Name)
            if ($amd.Count -gt 0) { Notice ('Detected AMD graphics: ' + ($amd -join ', ') + '; Ollama GPU support depends on model and driver compatibility.') }
            else { Notice 'No NVIDIA GPU utility detected. Ollama will select a compatible accelerator if available.' }
        } catch { Notice 'GPU information unavailable. Ollama will auto-detect supported accelerators.' }
    }
}

$tagsUrl = 'http://127.0.0.1:11434/api/tags'
$ready = $false
try { $null = Invoke-RestMethod -Uri $tagsUrl -TimeoutSec 3; $ready = $true } catch {}
if (-not $ready) {
    Notice 'Starting local Ollama server...'
    $env:OLLAMA_HOST = '0.0.0.0:11434'
    try { Start-Process -FilePath $ollamaExe -ArgumentList @('serve') -WindowStyle Hidden -ErrorAction Stop }
    catch { Notice ('ERROR: Unable to start Ollama: ' + $_.Exception.Message); exit 3 }
    for ($i=0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod -Uri $tagsUrl -TimeoutSec 2; $ready = $true; break } catch {}
    }
}
if (-not $ready) { Notice 'ERROR: Ollama did not become ready.'; exit 4 }

$model = Setting 'OLLAMA_REASON_MODEL' 'hermes3'
try {
    $tags = Invoke-RestMethod -Uri $tagsUrl -TimeoutSec 5
    $available = @($tags.models | Where-Object { $_.name -eq $model -or $_.name -like ($model + ':*') }).Count -gt 0
    if (-not $available -and (Setting 'AUTO_PULL_HERMES' '1') -eq '1') {
        Notice "Downloading $model. This may use several GB and require an internet connection."
        & $ollamaExe pull $model
        $available = ($LASTEXITCODE -eq 0)
    }
    if ($available) {
        Notice "Model $model is installed. Ollama automatically places supported model layers on GPU when memory permits."
        Notice 'To verify actual GPU offload after a reason-analysis request, run "ollama ps" and inspect the PROCESSOR column.'
    } else {
        Notice "ERROR: Model $model is not installed. Set AUTO_PULL_HERMES=1 and ensure internet access."
        exit 5
    }
} catch { Notice ('ERROR: Model verification unavailable: ' + $_.Exception.Message); exit 6 }
exit 0
