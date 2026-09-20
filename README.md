# ZapFlow — WhatsApp Multi-Instance Automation

ZapFlow is a minimal receptive automation platform for WhatsApp with a hybrid funnel (fixed steps + AI). It uses a small microservice for WhatsApp sessions, Groq Cloud for LLM and Whisper audio transcription, and a FastAPI backend.

Quick links

- Dashboard: http://localhost:8000
- API docs (dev): http://localhost:8000/docs

Supported stack

- Backend: FastAPI + SQLModel + SQLite
- Frontend: Jinja2 templates + HTMX
- WhatsApp microservice: Baileys-based service (in /whatsapp-service)
- AI: Groq Cloud (LLM + Whisper)

Security note (before making the repo public)

- Do NOT commit `.env` or any secret to the repository. Move local `.env` files out of the repo (this repo already ignores `.env`).
- If you accidentally committed secrets in history, remove them and rotate the keys.

Quickstart (Docker)

1. Copy and edit environment file:

```bash
cp .env.example .env
# Edit .env and add your GROQ_API_KEY and any other secrets
```

2. Start with Docker Compose:

```bash
docker compose up -d --build
```

3. Open the dashboard at `http://localhost:8000` and the API docs at `/docs`.

Running locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Environment variables

- Use `.env.example` as a template. Important vars include `SECRET_KEY`, `ADMIN_PASSWORD`, `GROQ_API_KEY`, and `WA_SERVICE_API_KEY`.

Important files and structure

- `app/` — backend code (APIs, models, services)
- `whatsapp-service/` — Baileys-based microservice (Node.js)
- `data/` — SQLite DB and uploaded media (this folder is ignored by Git and may contain private media)
- `docker-compose.yml` — service composition for local development

Translating docs

- The original Portuguese README has been preserved as `README.pt.md`.

Further reading and troubleshooting

- See `doc/how-to-run-local.md` (English) for an expanded local setup guide.

If you want, I can also open a PR with these changes and add a short `SECURITY.md` describing how to handle leaked keys.
