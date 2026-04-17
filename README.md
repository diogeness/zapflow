# ZapFlow — Plataforma de Automação WhatsApp Multi-Instância

MVP de automação receptiva para WhatsApp com funil híbrido (passos fixos + IA), usando Evolution API, Groq Cloud e FastAPI.

## Stack

- **Backend**: FastAPI + SQLModel + SQLite (WAL)
- **Frontend**: Jinja2 + HTMX + TailwindCSS (CDN)
- **WhatsApp**: Evolution API v2.2.3 (Docker)
- **IA**: Groq Cloud — Llama 3.1 8B (chat) + Whisper Large V3 Turbo (áudio)
- **Auth**: JWT (httpOnly cookie) + Argon2

## Início Rápido

### 1. Configurar ambiente

```bash
cp .env.example .env
# Edite o .env com suas chaves:
#   - SECRET_KEY (gere com: python -c "import secrets; print(secrets.token_hex(32))")
#   - ADMIN_PASSWORD (senha forte)
#   - EVOLUTION_API_KEY
#   - GROQ_API_KEY (https://console.groq.com)
#   - WEBHOOK_SECRET (gere com: python -c "import secrets; print(secrets.token_hex(16))")
```

### 2. Subir com Docker Compose

```bash
docker compose up -d --build
```

Serviços:
| Serviço | Porta | Descrição |
|---------|-------|-----------|
| app | 8000 | ZapFlow (dashboard + API) |
| evolution-api | 8080 | Gerenciador WhatsApp |
| redis | 6379 | Cache da Evolution API |

### 3. Acessar

- **Dashboard**: http://localhost:8000
- **API Docs** (dev): http://localhost:8000/docs
- **Evolution API**: http://localhost:8080

Login padrão: `admin` / senha definida em `ADMIN_PASSWORD`.

## Fluxo de Uso

1. **Criar Instância** — conecte um número WhatsApp via QR Code
2. **Criar Campanha** — vincule a uma instância, defina o prompt do sistema
3. **Adicionar Passos** — mensagens fixas do funil (texto, imagem, áudio PTT, documento)
4. **Ativar Campanha** — leads que enviarem mensagem entram no funil automaticamente
5. **Acompanhar** — dashboard com métricas, lista de leads, histórico de mensagens, tabulação automática

> **Importante**: O bot é 100% receptivo — nunca envia mensagem para quem não fez contato primeiro.

## Arquitetura do Funil

```
Lead envia mensagem
  → Anti-flood (5 msgs/30s)
  → Transcrição de áudio (Groq Whisper)
  → Se current_step >= 0: Fase Fixa (passos pré-definidos)
  → Se current_step == -1: Fase IA (conversa livre com LLM)
  → Humanizer (delay + presença typing/recording)
  → Resposta enviada via Evolution API
```

## Acesso Externo (Cloudflare Tunnel)

Para compartilhar com colegas:

```bash
# No .env, configure CLOUDFLARE_TUNNEL_TOKEN
docker compose --profile tunnel up -d
```

## Estrutura do Projeto

```
zapflow/
├── app/
│   ├── api/           # Rotas API (auth, webhooks, instances, campaigns, leads, dashboard)
│   ├── models/        # SQLModel (user, instance, campaign, lead, message)
│   ├── schemas/       # Pydantic schemas (request/response)
│   ├── services/      # Lógica de negócio (evolution, groq, funnel, humanizer, tabulation)
│   ├── templates/     # Jinja2 templates
│   ├── web/           # Rotas web (páginas HTML)
│   ├── config.py      # Configuração via env vars
│   ├── database.py    # Engine SQLite async
│   └── main.py        # App factory + lifespan
├── data/              # SQLite DB + media uploads
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Limites do Groq Free Tier

| Modelo | Req/min | Req/dia | Tokens/min |
|--------|---------|---------|------------|
| llama-3.1-8b-instant | 30 | 14.400 | 14.400 |
| whisper-large-v3-turbo | 20 | 2.000 | — |

O sistema usa semáforo (2 req simultâneos) + intervalo mínimo de 2s entre chamadas.

## Desenvolvimento Local (sem Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# configure .env

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Necessário: Evolution API e Redis rodando separadamente.
