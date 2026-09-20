# ZapFlow Technical Architecture (English)

Overview

ZapFlow is composed of:
- `app/`: FastAPI backend implementing the dashboard, REST APIs, and business logic (funnel, tabulation, humanizer)
- `whatsapp-service/`: Baileys-based microservice that manages WhatsApp sessions and message sending
- `data/`: SQLite database and uploaded media files

Key components

- Instances: represent connected WhatsApp sessions managed by the microservice. Each instance may have an `api_key` to protect webhook actions.
- Campaigns: funnel configuration, system prompt for AI, and ordered `CampaignStep` messages.
- FunnelEngine: orchestrates inbound messages, anti-flood checks, optional audio transcription (Groq Whisper), fixed-step flow, and AI flow via Groq LLM.

Groq integration

- `GroqLLMClient` (`app/services/groq_llm.py`): chat completions with internal rate-limiting using a semaphore and a minimum interval between calls.
- `GroqWhisperClient` (`app/services/groq_whisper.py`): audio transcription for incoming voice messages.

Security and configuration

- Secrets are read from environment variables. Use `.env.example` as a template. Important variables include `SECRET_KEY`, `ADMIN_PASSWORD`, `GROQ_API_KEY`, and `WA_SERVICE_API_KEY`.
- The application uses JWT (HS256) stored in an `httpOnly` cookie for the dashboard, and Bearer tokens for the API.

Data model (high level)
- `Instance` — connected WhatsApp session, `phone_number`, `status`, optional `api_key`.
- `Campaign` — funnel settings, `system_prompt`, `ai_model`, `is_active`.
- `CampaignStep` — ordered step messages (text, image, audio_ptt, document).
- `Lead` — contact that started a conversation, `current_step`, `status`, and AI tabulation results.

Operational notes

- The repo includes `docker-compose.yml` to run `app` and `wa-service` together.
- Uploaded media and session directories are stored under `data/` and are ignored by Git to avoid leaking private media.
