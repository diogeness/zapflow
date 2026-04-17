# ZapFlow — Documentação Técnica

## 1. Visão Geral

ZapFlow é uma plataforma de automação de WhatsApp **multi-instância** voltada para funis de atendimento receptivo. O sistema recebe mensagens de leads que entram em contato por conta própria e os conduz por uma sequência de respostas automatizadas: primeiro uma fase de passos fixos pré-configurados e, em seguida, uma fase de conversa livre com IA generativa.

**Característica fundamental:** o bot é 100% receptivo — jamais inicia contato com nenhum número. O funil só começa quando o próprio lead envia a primeira mensagem.

---

## 2. Stack Tecnológica

| Camada | Tecnologia | Versão |
|--------|-----------|--------|
| Backend | FastAPI | ≥ 0.115 |
| ORM / Modelos | SQLModel (Pydantic v2 + SQLAlchemy) | ≥ 0.0.22 |
| Banco de dados | SQLite com modo WAL (async via aiosqlite) | — |
| Templates HTML | Jinja2 + HTMX | — |
| Estilização | TailwindCSS (CDN) | — |
| Serviço WhatsApp | Baileys (Node.js/TypeScript) — microserviço interno | — |
| IA — chat | Groq Cloud, modelo `llama-3.1-8b-instant` | — |
| IA — transcrição de áudio | Groq Cloud, modelo `whisper-large-v3-turbo` | — |
| Autenticação | JWT (HS256) + Argon2 via pwdlib | — |
| Rate limiting | SlowAPI | ≥ 0.1 |
| Logs estruturados | structlog | ≥ 24.0 |
| Contêinerização | Docker + Docker Compose | — |
| Acesso externo (opcional) | Cloudflare Tunnel (`cloudflared`) | — |

---

## 3. Arquitetura Geral

```
┌────────────────────────────────────────────────────────┐
│                     Docker Compose                     │
│                                                        │
│   ┌─────────────────────┐   ┌──────────────────────┐  │
│   │     ZapFlow App      │   │  WhatsApp Microservice│  │
│   │  (FastAPI — :8000)   │◄──►│   (Baileys — :3100)  │  │
│   │                     │   │   Node.js/TypeScript  │  │
│   │  API REST + Web UI  │   │  Sessões Baileys      │  │
│   │  FunnelEngine        │   │  QR Code / Auth WA   │  │
│   │  Groq LLM / Whisper │   │  Envio/recebimento   │  │
│   │  SQLite (WAL)       │   │  de mensagens        │  │
│   └─────────────────────┘   └──────────────────────┘  │
│                                                        │
│   ┌──────────────────────────────────────────────┐     │
│   │   Cloudflared (opcional — profile: tunnel)   │     │
│   └──────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────┘
              │                        │
     Leads via WhatsApp         Operador via Browser
```

O **ZapFlow App** e o **WhatsApp Microservice** comunicam-se internamente via HTTP (rede Docker `zapflow`). O operador acessa o dashboard pelo browser. Leads interagem exclusivamente pelo WhatsApp.

---

## 4. Estrutura de Diretórios

```
zapflow/
├── app/                        # Aplicação Python (FastAPI)
│   ├── main.py                 # App factory, lifespan, inicialização de serviços
│   ├── config.py               # Configurações via variáveis de ambiente (pydantic-settings)
│   ├── database.py             # Engine SQLite async + migrações inline
│   ├── api/                    # Rotas REST
│   │   ├── auth.py             # Login, JWT, criação do admin
│   │   ├── campaigns.py        # CRUD de campanhas e passos
│   │   ├── instances.py        # Gerenciamento de instâncias WhatsApp
│   │   ├── leads.py            # Listagem e ações sobre leads
│   │   ├── dashboard.py        # Métricas agregadas
│   │   └── webhooks.py         # Receptor de eventos do microserviço Baileys
│   ├── models/                 # Entidades SQLModel (mapeamento ORM)
│   │   ├── user.py
│   │   ├── instance.py
│   │   ├── campaign.py         # Campaign + CampaignStep
│   │   ├── lead.py
│   │   └── message.py
│   ├── schemas/                # Schemas Pydantic (request/response DTOs)
│   ├── services/               # Lógica de negócio
│   │   ├── waha.py             # Cliente HTTP para o microserviço Baileys
│   │   ├── funnel.py           # Motor do funil híbrido (fase fixa + IA)
│   │   ├── groq_llm.py         # Cliente Groq para chat completions
│   │   ├── groq_whisper.py     # Cliente Groq para transcrição de áudio
│   │   ├── humanizer.py        # Simulação de comportamento humano (delays, status)
│   │   └── tabulation.py       # Tabulação automática de leads via LLM
│   ├── templates/              # Templates Jinja2 (HTML com HTMX)
│   └── web/                    # Rotas web (páginas renderizadas server-side)
├── whatsapp-service/           # Microserviço Node.js/TypeScript (Baileys)
│   ├── src/
│   │   ├── index.ts            # Servidor Express + health check
│   │   ├── session-manager.ts  # Gerenciamento de sessões Baileys
│   │   ├── webhook.ts          # Envio de eventos para o ZapFlow App
│   │   ├── humanizer.ts        # Delays no lado do microserviço
│   │   ├── rate-limiter.ts     # Controle de taxa de envio por sessão
│   │   ├── media.ts            # Resolução de caminhos de mídia
│   │   └── routes/
│   │       ├── sessions.ts     # Endpoints de sessão (criar, QR, status, desconectar)
│   │       └── messages.ts     # Endpoints de envio de mensagens
│   └── Dockerfile
├── data/                       # Volume persistente
│   ├── zapflow.db              # Banco SQLite
│   ├── backups/
│   └── media/                  # Arquivos de mídia por campanha/passo
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 5. Modelos de Dados

### `Instance` (instâncias WhatsApp)
Representa uma sessão WhatsApp conectada ao microserviço Baileys.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | int PK | Identificador |
| `name` | str (único) | Nome da sessão no microserviço |
| `phone_number` | str | Número conectado |
| `status` | str | `disconnected` / `connecting` / `connected` |
| `api_key` | str | Chave de autenticação da sessão (se configurada) |
| `saved_contacts` | str (JSON) | Lista de números da agenda (para filtragem) |

### `Campaign` (campanhas)
Agrupa a configuração do funil, o prompt de IA e os passos fixos.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | int PK | Identificador |
| `instance_id` | int FK | Instância WhatsApp vinculada |
| `name` | str | Nome da campanha |
| `system_prompt` | str | Prompt de sistema para a IA |
| `ai_model` | str | Modelo Groq a usar |
| `ai_temperature` | float | Temperatura das respostas (0–2) |
| `is_active` | bool | Campanha aceitando novos leads |
| `context_file` | str | Caminho para arquivo de base de conhecimento (≤ 50 KB) |
| `ignore_groups` | bool | Ignora mensagens de grupos (padrão: `true`) |
| `ignore_contacts` | bool | Ignora contatos salvos na agenda |
| `ai_enabled` | bool | Habilita/desabilita fase IA após passos fixos |
| `max_ai_interactions` | int | Limite de respostas IA por lead (0 = ilimitado) |

### `CampaignStep` (passos do funil fixo)
Cada passo representa uma mensagem a ser enviada automaticamente.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `campaign_id` | int FK | Campanha proprietária |
| `step_order` | int | Ordem de execução |
| `message_type` | str | `text`, `image`, `video`, `document`, `audio_ptt` |
| `content` | str | Texto da mensagem ou legenda |
| `media_urls` | str (JSON) | Array de arquivos de mídia `[{"path": "...", "name": "..."}]` |
| `delay_seconds` | int | Delay antes de enviar este passo |
| `wait_reply` | bool | Aguarda resposta do lead para avançar |
| `view_once` | bool | Envia mídia como "visualização única" |

### `Lead` (leads)
Representa um contato que iniciou interação com alguma campanha.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `phone` | str | Número do lead |
| `name` | str | Nome (quando disponível) |
| `current_step` | int | `>= 0` fase fixa; `-1` fase IA |
| `status` | str | `active`, `completed`, `paused`, `blocked` |
| `tabulation` | str | Resumo gerado pelo LLM |
| `tab_interest_level` | str | `hot`, `warm`, `cold`, `unresponsive` |
| `ai_interactions_count` | int | Quantidade de respostas IA enviadas |

### `Message` (mensagens)
Histórico completo de mensagens por lead.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `lead_id` | int FK | Lead proprietário |
| `direction` | str | `inbound` / `outbound` |
| `message_type` | str | Tipo de mensagem |
| `content` | str | Conteúdo textual (transcrição para áudios) |
| `is_from_ai` | bool | Indica respostas geradas pelo LLM |
| `evolution_msg_id` | str | ID único da mensagem no Baileys (deduplicação) |

---

## 6. Fluxo do Funil (FunnelEngine)

O `FunnelEngine` (`app/services/funnel.py`) é o coração do sistema. Ele é acionado por cada webhook de mensagem recebida e opera de forma completamente assíncrona.

```
Lead envia mensagem
       │
       ▼
Anti-flood check (máx 5 msgs / 30s por lead)
       │ ok
       ▼
Lead status == "active"?
       │ sim
       ▼
Tipo de mensagem é áudio?
  ├─ sim → Transcrição Groq Whisper Large V3 Turbo
  └─ não → usa texto diretamente
       │
       ▼
Salva mensagem inbound no banco
       │
       ▼
Adquire lock por lead (evita race conditions em mensagens simultâneas)
       │
       ▼
current_step >= 0?
  ├─ sim → ── FASE FIXA ──
  │          Envia próximo CampaignStep (texto, imagem, áudio PTT, vídeo, doc)
  │          step.wait_reply == true → aguarda próxima mensagem do lead
  │          step.wait_reply == false → avança imediatamente para o próximo passo
  │          Ao esgotar todos os passos → current_step = -1 (vai para fase IA)
  │
  └─ não (== -1) → ── FASE IA ──
                    Verifica ai_enabled e max_ai_interactions
                    Debounce de N segundos (agrupa mensagens rápidas)
                    Busca histórico de mensagens (últimas HISTORY_LIMIT)
                    Carrega context_file (base de conhecimento, opcional)
                    Chama Groq LLM (system_prompt + histórico)
                    Salva resposta no banco
                    Envia via Humanizer
       │
       ▼
Humanizer
  ├─ Texto: mostra status "digitando...", aguarda delay proporcional ao tamanho
  ├─ Áudio: mostra status "gravando...", aguarda delay proporcional à duração
  └─ Delay base + jitter aleatório (evita padrão mecânico)
       │
       ▼
Mensagem enviada via WhatsApp Client → microserviço Baileys
```

### Detalhes de concorrência

- **Lock por lead**: cada lead possui um `asyncio.Lock` para evitar que duas mensagens simultâneas avancem o step duas vezes.
- **Debounce na fase IA**: se o lead enviar várias mensagens em sequência rápida, o sistema aguarda `AI_DEBOUNCE_SECONDS` antes de responder, agrupando o conteúdo.
- **Anti-flood**: leads que enviarem mais de 5 mensagens em 30 segundos são ignorados temporariamente.

---

## 7. Microserviço WhatsApp (Baileys)

Localizado em `whatsapp-service/`, é um servidor **Express.js** escrito em TypeScript que encapsula a biblioteca **Baileys** (implementação WhatsApp Web via WebSocket reverso).

### Responsabilidades
- Gerenciar múltiplas sessões WhatsApp simultâneas (multi-instância)
- Fornecer QR Code para autenticação de cada sessão
- Persistir estado de sessão em volume Docker (`/data/sessions`)
- Receber requisições de envio de mensagens do ZapFlow App
- Emitir webhooks para o ZapFlow App a cada mensagem recebida ou mudança de status de sessão

### Endpoints principais

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/health` | Health check do serviço |
| `POST` | `/api/sessions` | Cria nova sessão |
| `GET` | `/api/sessions` | Lista sessões ativas |
| `GET` | `/api/sessions/:name` | Status de uma sessão |
| `DELETE` | `/api/sessions/:name` | Encerra e remove sessão |
| `GET` | `/api/:name/auth/qr` | QR Code para autenticação |
| `POST` | `/api/:name/sendText` | Envia mensagem de texto |
| `POST` | `/api/:name/sendImage` | Envia imagem |
| `POST` | `/api/:name/sendAudio` | Envia áudio PTT |
| `POST` | `/api/:name/sendDocument` | Envia documento |
| `POST` | `/api/:name/sendPresence` | Atualiza status (digitando/gravando) |
| `GET` | `/media/:session/:filename` | Serve arquivos de mídia recebidos |

A **autenticação** entre o ZapFlow App e este microserviço é feita via header `X-Api-Key`.

---

## 8. Serviços Python

### `WhatsAppClient` (`services/waha.py`)
Cliente HTTP assíncrono (`httpx.AsyncClient`) que abstrai toda comunicação com o microserviço Baileys. Gerencia sessões, QR codes, envio de diferentes tipos de mídia e controle de presença.

### `GroqLLMClient` (`services/groq_llm.py`)
Cliente para a API Groq Cloud:
- **`generate_response`**: gera resposta de chat com base no prompt de sistema e histórico de mensagens.
- **`generate_tabulation`**: analisa conversa e retorna resumo + nível de interesse (`hot/warm/cold/unresponsive`).
- Rate limiting interno: semáforo com `GROQ_MAX_CONCURRENT` slots e intervalo mínimo de `GROQ_MIN_INTERVAL` segundos entre chamadas.

### `GroqWhisperClient` (`services/groq_whisper.py`)
Transcrição de mensagens de áudio (`.ogg` PTT) usando o modelo Whisper Large V3 Turbo via Groq Cloud. Retorna o texto transcrito para ser processado pelo funil como mensagem normal.

### `Humanizer` (`services/humanizer.py`)
Simula comportamento humano nas respostas:
- **`simulate_typing`**: ativa o status "digitando..." no WhatsApp e aguarda `len(texto) × 50ms + jitter`.
- **`simulate_recording`**: ativa o status "gravando..." para respostas em áudio.
- **`step_delay`**: pausa entre passos do funil com leve aleatoriedade.
- Delay configurável: `HUMANIZER_MIN_DELAY` a `HUMANIZER_MAX_DELAY` segundos.

### `TabulationService` (`services/tabulation.py`)
Tabulação automática de leads via LLM como tarefa periódica em background:
- Verifica leads que estão na fase IA, inativos há mais de `TABULATION_IDLE_MINUTES` minutos e ainda não foram tabulados.
- Envia as últimas 20 mensagens para o Groq LLM e salva o resumo e nível de interesse no banco.
- Executa a cada 5 minutos.

---

## 9. Autenticação e Segurança

- **Autenticação**: JWT (algoritmo `HS256`) com validade de 24 horas, armazenado em cookie `httpOnly` (dashboard web) ou via Bearer token (API REST).
- **Hash de senha**: Argon2 via `pwdlib` — resistente a ataques de força bruta por GPU.
- **Rate limiting**: `SlowAPI` protege os endpoints públicos contra abuso.
- **CORS**: configurado via `CORSMiddleware` do FastAPI.
- **Container hardening**: `no-new-privileges`, `read_only: true` + `tmpfs` no container principal.
- **Admin**: criado automaticamente no primeiro boot com credenciais definidas em `.env`.

---

## 10. Banco de Dados e Migrações

- **Engine**: SQLite com modo WAL (`journal_mode=WAL`), `synchronous=NORMAL` e `busy_timeout=5000ms` para suportar múltiplas conexões assíncronas sem conflito.
- **ORM**: SQLModel (Pydantic v2 + SQLAlchemy 2.x) com suporte a `AsyncSession`.
- **Migrações**: executadas inline no `database.py` via `ALTER TABLE ADD COLUMN IF NOT EXISTS` a cada inicialização — abordagem leve, sem dependência de Alembic em runtime. O diretório `alembic/` existe para uso futuro em operações mais complexas.
- **Deduplicação de mensagens**: índice na coluna `evolution_msg_id` da tabela `messages` para evitar processamento duplicado de webhooks.

---

## 11. Webhook e Processamento de Eventos

O endpoint `/api/webhooks/waha` (`app/api/webhooks.py`) recebe eventos do microserviço Baileys:

| Evento | Ação |
|--------|------|
| `message` | Dispara `_handle_message()` de forma assíncrona via `asyncio.create_task` |
| `session.status` | Atualiza o status da instância no banco (`connected`, `connecting`, `disconnected`) |
| `ping` | Retorna `{"ok": true}` imediatamente (health check do microserviço) |

O processamento da mensagem ocorre em background para não bloquear o retorno HTTP ao microserviço (responde imediatamente com `200 OK`).

### Lógica de roteamento de mensagens
1. Identifica a instância pelo nome da sessão
2. Filtra grupos e contatos da agenda conforme configuração da campanha
3. Localiza ou cria o lead no banco
4. Se é o primeiro contato → chama `FunnelEngine.start_funnel()`
5. Se já existe → chama `FunnelEngine.process_inbound()`

---

## 12. Dashboard e Interface Web

Interface server-side rendering (SSR) com **Jinja2 + HTMX**, sem frameworks JavaScript pesados.

### Principais páginas

| Rota | Descrição |
|------|-----------|
| `/` | Dashboard com métricas gerais |
| `/instances` | Listagem e gerenciamento de instâncias |
| `/campaigns` | CRUD de campanhas e passos do funil |
| `/leads` | Listagem de leads com filtros e tabulação |
| `/leads/{id}` | Detalhe do lead com histórico de mensagens |
| `/login` | Autenticação do operador |

HTMX é usado para atualizações parciais de página (cards de instâncias, tabelas de leads, métricas) sem recarregar a página inteira.

---

## 13. Infraestrutura e Deploy

### Docker Compose

```
┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│   zapflow_app    │    │  zapflow_whatsapp    │    │  zapflow_tunnel  │
│  FastAPI :8000   │◄──►│  Baileys/Express     │    │  Cloudflared     │
│  Volume: ./data  │    │  :3100               │    │  (opcional)      │
└──────────────────┘    │  Volume: sessions    └────└──────────────────┘
                        └─────────────────────┘
```

- `app` aguarda `wa-service` estar saudável antes de iniciar (`depends_on: condition: service_healthy`).
- `wa-service` expõe `/health` como health check.
- Todos os serviços compartilham a rede interna `zapflow` (bridge Docker).
- Volume `whatsapp_sessions` persiste as sessões Baileys entre restarts.
- Volume `./data` persiste o banco SQLite e os arquivos de mídia.

### Cloudflare Tunnel (opcional)
Perfil `tunnel` do Docker Compose. Permite expor o dashboard externamente sem abrir portas no firewall, ideal para ambientes onde o servidor não tem IP público fixo.

### Variáveis de ambiente relevantes

| Variável | Descrição |
|----------|-----------|
| `SECRET_KEY` | Chave de assinatura JWT (32 bytes hex) |
| `ADMIN_PASSWORD` | Senha do administrador |
| `WA_SERVICE_API_KEY` | Chave de autenticação entre app e microserviço Baileys |
| `WA_WEBHOOK_CALLBACK_URL` | URL interna para o microserviço enviar webhooks ao app |
| `GROQ_API_KEY` | Chave da API Groq Cloud |
| `GROQ_DEFAULT_MODEL` | Modelo LLM padrão (ex: `llama-3.1-8b-instant`) |
| `GROQ_WHISPER_MODEL` | Modelo de transcrição (ex: `whisper-large-v3-turbo`) |
| `HUMANIZER_MIN_DELAY` / `HUMANIZER_MAX_DELAY` | Limites de delay do humanizador (segundos) |
| `TABULATION_IDLE_MINUTES` | Tempo de inatividade para acionar tabulação automática |
| `AI_DEBOUNCE_SECONDS` | Janela de debounce para agrupar mensagens na fase IA |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token do Cloudflare Tunnel (perfil opcional) |

---

## 14. Limites e Considerações

- **Groq Free Tier**: limitado em RPM (requests per minute) e RPD (requests per day). O `GroqLLMClient` implementa semáforo e intervalo mínimo entre chamadas para respeitar esses limites. Em produção com alto volume, é recomendado usar uma conta paga.
- **SQLite**: adequado para uso single-node com volume moderado. Para cenários de alta concorrência ou múltiplos workers, considerar migração para PostgreSQL.
- **Multi-instância**: cada instância WhatsApp corresponde a uma sessão Baileys independente. O número de instâncias simultâneas suportadas depende dos recursos do servidor (memória RAM principalmente).
- **Arquivos de mídia**: armazenados localmente em `./data/media/`. Para produção em escala, considerar armazenamento externo (S3, R2 etc.).
