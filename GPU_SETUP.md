# GPU acceleration for Hermes / Ollama

**Start everything with only:** ` .\RUN_NGROK_DASHBOARD.bat `

The launcher automatically calls `START_HERMES_IF_AVAILABLE.ps1`, then starts the existing bot, dashboard and ngrok containers. The GPU is for **Hermes reason summarization**, not for PostgreSQL, ngrok, or normal dashboard map rendering.

## Requirements on Windows

- Install the Windows Ollama application (https://ollama.com/download/windows).
- Install a current compatible NVIDIA or AMD GPU driver. Ollama selects compatible GPU acceleration automatically when GPU memory permits.
- The `.env` includes `OLLAMA_URL=http://host.docker.internal:11434` and `OLLAMA_REASON_MODEL=hermes3`, allowing the dashboard Docker container to call Windows Ollama.
- Existing `.env` secrets and other settings are preserved.

## Model download

The project uses `AUTO_PULL_HERMES=0` by default to avoid an unexpected multi-GB download during start. If Hermes is not yet installed, set `AUTO_PULL_HERMES=1` in `.env` **once**; then run the normal launcher. When the model is installed, optionally set it back to 0. Download requires internet and available disk and GPU/RAM capacity.

`OLLAMA_GPU_MODE=auto` leaves device allocation to Ollama. `OLLAMA_GPU_MODE=cpu` is an optional troubleshooting setting only. On a compatible GPU with enough VRAM, Ollama can offload some or all layers; the launcher cannot guarantee full GPU offload.

## Verify hardware acceleration

After the AI has processed a reason, run `ollama ps` in a Windows terminal and check its **PROCESSOR** column (GPU / CPU / split). `nvidia-smi` can also show NVIDIA GPU utilization. Model execution may fall back to CPU if drivers, VRAM, or hardware support are insufficient.

## Important limits

This package cannot install GPU drivers, Ollama, or models on your PC remotely. The startup script diagnoses missing components and starts the rest of your system regardless. The GPU is used only by Ollama inference, while the rest of the system remains in Docker.
