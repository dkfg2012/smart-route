# Release Checklist

Before uploading this folder to GitHub:

1. Run `rg -n "API key patterns|API_KEY|TOKEN|PASSWORD" .`.
2. Confirm only `.env.example`, docs, and source references remain.
3. Do not commit a real `.env`.
4. Do not commit model caches, `.venv`, `node_modules`, runtime traces, or Docker volumes.
5. Start from a clean clone with `cp .env.example .env`.
6. Keep `MODEL_BACKEND_MODE=mock` for public demos without API keys.
7. Use a private secret store or GitHub Actions secrets for real backend keys.

This exported folder was prepared without local `.env`, virtualenv, frontend dependencies, model caches, runtime traces, or generated dashboard `dist`.
