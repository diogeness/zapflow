# How to Run ZapFlow Locally (Quick Guide)

This guide explains how to run ZapFlow locally for development or testing. It covers both Docker Compose and running the backend without Docker.

Prerequisites
- Docker & Docker Compose (recommended)
- Python 3.11+ (if running without Docker)
- Optional: Redis and the WhatsApp microservice (`whatsapp-service`) when running locally without Docker

Quick Docker setup
1. Copy the example environment file and edit it:

```bash
cp .env.example .env
# Edit .env and set values for:
# - SECRET_KEY (generate with: python -c "import secrets; print(secrets.token_hex(32))")
# - ADMIN_PASSWORD
# - GROQ_API_KEY (from https://console.groq.com)
# - WA_SERVICE_API_KEY (if using the bundled whatsapp-service)
```

2. Start services:

```bash
docker compose up -d --build
```

3. Open the dashboard at `http://localhost:8000`.

Running without Docker
1. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit your .env
```

2. Start the app:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Notes and tips
- The project expects a Groq API key for AI features. Without it, AI-related functionality will fail but most parts of the app will still run.
- The `.env` file should never be committed. This repo already lists `.env` in `.gitignore`. Move any local `.env` file out of the repository before publishing.
- If you need to expose the app to the internet, you can enable the Cloudflare Tunnel profile in `docker compose` and set `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.

Troubleshooting
- Check container logs: `docker compose logs -f app`
- Confirm the WhatsApp microservice is healthy: `docker compose ps`
- If you change `.env`, restart the app container: `docker compose restart app`
