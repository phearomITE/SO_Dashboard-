# Checks actual Docker -> Windows Ollama connectivity, not merely local Windows service.
$ErrorActionPreference = 'Continue'
$py = @'
import json,os,sys,urllib.request
url=os.getenv('OLLAMA_URL','').rstrip('/')
model=os.getenv('OLLAMA_REASON_MODEL','hermes3')
if not url:
    print('[Hermes] ERROR OLLAMA_URL is empty in Docker');sys.exit(2)
try:
    with urllib.request.urlopen(url+'/api/tags',timeout=8) as response:
        data=json.load(response)
    names=[x.get('name','') for x in data.get('models',[])]
    if not any(x==model or x.startswith(model+':') for x in names):
        print('[Hermes] ERROR Model not available in Docker Ollama:',model);sys.exit(3)
    print('[Hermes] Docker can reach Ollama and model:',model)
except Exception as exc:
    print('[Hermes] ERROR Docker cannot reach Ollama:',str(exc));sys.exit(4)
'@
$py | docker compose exec -T so-bot python -c "import sys;exec(sys.stdin.read())"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[Hermes] AI NOT READY: check Ollama GPU, Windows firewall, and OLLAMA_HOST=0.0.0.0:11434.' -ForegroundColor Yellow
    exit 1
}
Write-Host '[Hermes] Ready: Docker-to-Ollama connectivity confirmed.' -ForegroundColor Green
exit 0
