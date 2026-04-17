# Como Rodar o ZapFlow Localmente — Passo a Passo (WSL no Windows)

> **Para quem é este guia?**
> Para alguém que usa Windows e nunca rodou o projeto antes. Vamos usar o **WSL** (Subsistema Linux para Windows), que permite rodar comandos Linux dentro do Windows sem precisar de uma máquina separada. Não precisa saber programar.

---

## Visão geral do que vamos instalar

```
Windows
 └── WSL 2 (Ubuntu)          ← terminal Linux dentro do Windows
      ├── Git                 ← para baixar o código
      ├── Docker Engine       ← para rodar os containers da aplicação
      └── ZapFlow             ← o projeto em si
```

Todo o trabalho acontece dentro do terminal do WSL. O navegador (Chrome, Edge etc.) fica no Windows normal e acessa o sistema em `http://localhost:8000`.

---

## Passo 0 — Instalar o WSL (Windows Subsystem for Linux)

O WSL permite rodar um sistema Linux completo dentro do Windows. Você vai precisar do Windows 10 (versão 2004 ou mais recente) ou Windows 11.

### Como instalar

1. Abra o **Prompt de Comando** ou o **PowerShell** como Administrador:
   - Aperte `Win + S`, digite `powershell`, clique com o botão direito em **Windows PowerShell** e escolha **Executar como administrador**

2. Execute o comando abaixo e aguarde terminar (pode demorar alguns minutos):
   ```powershell
   wsl --install
   ```
   Esse comando instala o WSL 2 com Ubuntu automaticamente.

3. **Reinicie o computador** quando solicitado.

4. Após reiniciar, o Ubuntu vai abrir automaticamente para concluir a instalação. Quando pedir, **crie um nome de usuário e senha** para o Ubuntu (não precisa ser o mesmo do Windows).

   > Anote essa senha — ela será usada quando um comando pedir `sudo` (equivalente a "executar como administrador" no Linux).

5. Pronto! Você agora tem um terminal Ubuntu. Para abrir no futuro:
   - Aperte `Win + S` e procure por **Ubuntu**
   - Ou abra o **Windows Terminal** e escolha Ubuntu no menu de abas

### Verificar a instalação

Dentro do terminal Ubuntu, execute:
```bash
uname -a
# Deve aparecer algo como: Linux ... Microsoft ... x86_64 GNU/Linux
```

---

## Passo 1 — Instalar Git e Docker no WSL

Todos os comandos a seguir devem ser executados **dentro do terminal Ubuntu (WSL)**.

### Instalar Git

```bash
sudo apt update && sudo apt install -y git
```

Verifique:
```bash
git --version
# Deve aparecer: git version 2.x.x
```

### Instalar Docker Engine

O Docker vai rodar os containers da aplicação. Vamos instalar o Docker Engine direto no WSL (sem precisar do Docker Desktop):

```bash
# 1. Baixar e executar o script oficial de instalação do Docker
curl -fsSL https://get.docker.com | sh

# 2. Adicionar seu usuário ao grupo docker (para não precisar de sudo sempre)
sudo usermod -aG docker $USER

# 3. Iniciar o serviço Docker
sudo service docker start
```

Feche o terminal Ubuntu e abra novamente para aplicar o grupo. Depois verifique:

```bash
docker --version
docker compose version
# Deve aparecer as versões instaladas
```

> **Dica:** O WSL fecha o Docker quando você fecha o terminal. Para iniciar o Docker automaticamente ao abrir o WSL, adicione esta linha ao final do arquivo `~/.bashrc`:
> ```bash
> echo 'sudo service docker start > /dev/null 2>&1' >> ~/.bashrc
> ```
> Isso vai iniciar o Docker silenciosamente toda vez que você abrir o terminal.

---

## Passo 2 — Clonar o repositório

Ainda no terminal Ubuntu, clone o projeto na sua pasta pessoal do Linux (não dentro de `/mnt/c/`, pois isso seria mais lento e pode causar problemas de permissão):

```bash
cd ~
git clone https://github.com/diogeness/zapflow.git
cd zapflow
```

Você vai ter uma pasta `~/zapflow` com todo o código do projeto.

> **Por que não clonar em `C:\Users\...`?**
> As pastas do Windows ficam em `/mnt/c/` dentro do WSL e têm desempenho reduzido. Manter o projeto dentro do sistema de arquivos do Linux (`~/`) é mais rápido e evita problemas de permissão com o Docker.

---

## Passo 3 — Criar sua chave Groq (gratuita)

O ZapFlow usa a IA da Groq para responder os leads e transcrever áudios. A conta gratuita já é suficiente para começar.

1. Acesse https://console.groq.com no seu navegador Windows
2. Crie uma conta (pode usar Google ou GitHub para entrar)
3. No menu lateral, clique em **API Keys**
4. Clique em **Create API Key**, dê um nome (ex: `zapflow`) e clique em **Submit**
5. **Copie a chave** — ela começa com `gsk_`. Você só vai conseguir vê-la uma vez.

---

## Passo 4 — Configurar o arquivo `.env`

O arquivo `.env` é onde ficam as configurações e senhas do sistema. O projeto já tem um arquivo de exemplo. Copie-o no terminal WSL:

```bash
cp .env.example .env
```

### Editar o arquivo `.env`

Você pode editar pelo terminal mesmo, usando o `nano` (editor de texto simples):

```bash
nano .env
```

Navegue com as setas do teclado. Para salvar: `Ctrl + O` → Enter. Para sair: `Ctrl + X`.

Ou, se tiver o VS Code instalado no Windows, execute:

```bash
code .env
```

O VS Code abre o arquivo diretamente do WSL. Salve normalmente com `Ctrl + S`.

### Conteúdo do `.env` e o que preencher

```env
APP_NAME=ZapFlow
ENVIRONMENT=development
SECRET_KEY=CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32
ADMIN_USERNAME=admin
ADMIN_PASSWORD=CHANGE_ME
APP_BASE_URL=http://localhost:8000

DATABASE_URL=sqlite+aiosqlite:///./data/zapflow.db

WA_SERVICE_URL=http://wa-service:3100
WA_SERVICE_API_KEY=
WA_WEBHOOK_CALLBACK_URL=http://app:8000

GROQ_API_KEY=gsk_YOUR_GROQ_API_KEY_HERE
GROQ_DEFAULT_MODEL=llama-3.1-8b-instant
GROQ_WHISPER_MODEL=whisper-large-v3-turbo

CLOUDFLARE_TUNNEL_TOKEN=
```

| Campo | O que colocar |
|-------|--------------|
| `SECRET_KEY` | Uma sequência aleatória longa e segura (veja como gerar abaixo) |
| `ADMIN_PASSWORD` | A senha que você vai usar para entrar no dashboard |
| `WA_SERVICE_API_KEY` | Qualquer senha que você inventar — serve para proteger o microserviço WhatsApp (ex: `minha-chave-secreta-123`) |
| `GROQ_API_KEY` | A chave que você copiou no Passo 3 |

### Como gerar o `SECRET_KEY` (no terminal WSL):

```bash
openssl rand -hex 32
```

Copie o resultado e cole no `.env` no campo `SECRET_KEY`.

### Exemplo de `.env` preenchido (use os seus próprios valores, não copie estes):

```env
APP_NAME=ZapFlow
ENVIRONMENT=development
SECRET_KEY=4a7f2c9d1e8b3f6a0c5d2e9b7a4f1c8e3d6b9a2f5c0e7d4b1a8f3c6e9d2b5a0
ADMIN_USERNAME=admin
ADMIN_PASSWORD=MinhaSenh@Forte123
APP_BASE_URL=http://localhost:8000

DATABASE_URL=sqlite+aiosqlite:///./data/zapflow.db

WA_SERVICE_URL=http://wa-service:3100
WA_SERVICE_API_KEY=minha-chave-secreta-123
WA_WEBHOOK_CALLBACK_URL=http://app:8000

GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GROQ_DEFAULT_MODEL=llama-3.1-8b-instant
GROQ_WHISPER_MODEL=whisper-large-v3-turbo

CLOUDFLARE_TUNNEL_TOKEN=
```

> **Importante:** os campos `WA_SERVICE_URL` e `WA_WEBHOOK_CALLBACK_URL` usam nomes internos dos containers Docker (`wa-service` e `app`). Não altere esses valores para uso local.

Salve o arquivo após preencher.

---

## Passo 5 — Subir o sistema

No terminal WSL, dentro da pasta `zapflow`, execute:

```bash
docker compose up -d --build
```

O que acontece aqui:
- `--build` constrói as imagens Docker pela primeira vez (pode demorar 3–5 minutos — só na primeira vez)
- `-d` roda tudo em segundo plano

Ao final, deve aparecer algo como:

```
✔ Container zapflow_whatsapp  Started
✔ Container zapflow_app       Started
```

### Verificar se está rodando

```bash
docker compose ps
```

Devем aparecer os dois containers com status `running` ou `healthy`:

```
NAME                  STATUS
zapflow_app           Up (healthy)
zapflow_whatsapp      Up (healthy)
```

Se algum container estiver com status `Exit` ou `Restarting`, veja os logs:
```bash
docker compose logs app
docker compose logs wa-service
```

---

## Passo 6 — Acessar o dashboard

Abra qualquer navegador no Windows (Chrome, Edge, Firefox) e acesse:

```
http://localhost:8000
```

O WSL 2 repassa automaticamente as portas para o Windows, então `localhost` funciona normalmente no navegador.

Faça login com:
- **Usuário**: `admin` (ou o valor que você colocou em `ADMIN_USERNAME`)
- **Senha**: o valor que você colocou em `ADMIN_PASSWORD`

---

## Passo 7 — Conectar um número WhatsApp

1. No dashboard, clique em **Instâncias** no menu lateral
2. Clique em **Nova Instância**
3. Dê um nome (ex: `meu-numero`) e confirme
4. Um QR Code vai aparecer na tela
5. No WhatsApp do celular que você quer usar:
   - Abra o WhatsApp → três pontinhos → **Dispositivos conectados** → **Conectar dispositivo**
   - Escaneie o QR Code com a câmera
6. Aguarde alguns segundos. O status deve mudar para **Conectado** (verde)

> O número conectado **não deve ser o número principal do seu celular** em produção, pois o WhatsApp pode bloquear números com comportamento automatizado. Recomenda-se usar um número de chip secundário.

---

## Passo 8 — Criar uma campanha e começar a usar

1. Acesse **Campanhas** → **Nova Campanha**
2. Selecione a instância que você acabou de conectar
3. Preencha o **Prompt do Sistema** — é aqui que você descreve quem é o "atendente" virtual (ex: *"Você é um atendente da empresa X. Responda de forma simpática e profissional..."*)
4. Adicione **Passos Fixos** (opcional): são as mensagens automáticas enviadas antes de a IA assumir (saudação, apresentação, etc.)
5. Ative a campanha clicando em **Ativar**

Pronto! Qualquer pessoa que enviar mensagem para o número conectado vai entrar no funil automaticamente.

---

## Parar e reiniciar

**Parar tudo:**
```bash
docker compose down
```

**Reiniciar:**
```bash
docker compose up -d
```

> Os dados (banco de dados, sessões WhatsApp, arquivos de mídia) ficam salvos e não são perdidos ao parar.

**Para parar E apagar todos os dados** (cuidado — irreversível):
```bash
docker compose down -v
```

---

## Atualizar o sistema (puxar nova versão)

```bash
git pull
docker compose up -d --build
```

---

## Rodando sem Cloudflare Tunnel (acesso apenas local)

Por padrão, o sistema já roda **sem** o Cloudflare Tunnel. O dashboard fica acessível no seu computador em `http://localhost:8000`.

Isso é suficiente para testar e usar localmente. Se precisar acessar de outro computador na mesma rede Wi-Fi (ex: celular ou outro computador), descubra o IP da sua máquina Windows:

No terminal WSL:
```bash
ip route show default | awk '{print $3}'
# Retorna o IP do gateway — o IP da sua máquina Windows na rede local é parecido com isso
```

Ou abra o Prompt de Comando do **Windows** (não o WSL) e execute:
```cmd
ipconfig
# Procure "Adaptador Ethernet" ou "Wi-Fi" → "Endereço IPv4": ex: 192.168.1.10
```

Depois acesse de qualquer dispositivo na mesma rede:
```
http://192.168.1.10:8000
```

> Se for usar dessa forma, atualize `APP_BASE_URL` no `.env` com esse IP e reinicie o sistema (`docker compose restart app`) para que os webhooks funcionem corretamente.

---

## Ativando o Cloudflare Tunnel (acesso externo pela internet)

O Cloudflare Tunnel serve para expor o sistema para a internet **sem precisar abrir portas no roteador** ou ter IP fixo. É muito útil para receber mensagens de WhatsApp de qualquer lugar e acessar o dashboard remotamente.

### 1. Criar uma conta Cloudflare (gratuita)

Acesse https://www.cloudflare.com no navegador Windows e crie uma conta. Não precisa ter um domínio próprio para começar.

### 2. Criar o tunnel

1. Acesse https://one.dash.cloudflare.com
2. No menu lateral, vá em **Networks → Tunnels**
3. Clique em **Create a tunnel**
4. Escolha **Cloudflared** e clique em **Next**
5. Dê um nome ao tunnel (ex: `zapflow`) e clique em **Save tunnel**
6. Na próxima tela, você vai ver um comando como:
   ```
   cloudflared tunnel --no-autoupdate run --token eyJhbGc...
   ```
   Copie **apenas o token** — a parte longa depois de `--token`

### 3. Configurar um domínio público

Ainda na tela do tunnel (aba **Public Hostname**):
1. Clique em **Add a public hostname**
2. Preencha:
   - **Subdomain**: `zapflow` (ou o nome que quiser)
   - **Domain**: escolha um domínio que você tenha ou use um dos domínios gratuitos do Cloudflare
   - **Service Type**: `HTTP`
   - **URL**: `app:8000`
3. Salve.

### 4. Adicionar o token ao `.env`

Abra o arquivo `.env` no terminal WSL e preencha:

```env
CLOUDFLARE_TUNNEL_TOKEN=eyJhbGc...cole_seu_token_aqui
```

Também atualize `APP_BASE_URL` com o domínio público que você configurou:

```env
APP_BASE_URL=https://zapflow.seudominio.com
```

### 5. Subir com o perfil do tunnel ativado

```bash
docker compose --profile tunnel up -d --build
```

Agora o sistema estará acessível pela URL pública configurada no Cloudflare.

### Para parar incluindo o tunnel:

```bash
docker compose --profile tunnel down
```

---

## Resumo dos comandos mais usados

| Ação | Comando (no terminal WSL) |
|------|---------|
| Iniciar Docker (se não iniciou) | `sudo service docker start` |
| Subir tudo (sem tunnel) | `docker compose up -d --build` |
| Subir tudo (com tunnel) | `docker compose --profile tunnel up -d --build` |
| Ver status dos containers | `docker compose ps` |
| Ver logs em tempo real | `docker compose logs -f` |
| Ver logs de um serviço | `docker compose logs -f app` |
| Parar tudo | `docker compose down` |
| Reiniciar um serviço | `docker compose restart app` |
| Atualizar após git pull | `docker compose up -d --build` |

---

## Solução de problemas comuns

### "Cannot connect to the Docker daemon" ao rodar docker compose
O serviço Docker não está rodando. Inicie-o:
```bash
sudo service docker start
```

### O QR Code não aparece ou expira rápido
- Aguarde alguns segundos após criar a instância e recarregue a página.
- Certifique-se de que o container `wa-service` está com status `healthy`:
  ```bash
  docker compose ps
  ```

### Erro "Connection refused" ao acessar localhost:8000
- O container `app` pode ainda estar iniciando. Aguarde 30 segundos e tente novamente.
- Verifique os logs: `docker compose logs app`

### O bot não responde às mensagens
- Verifique se a campanha está **Ativa**.
- Verifique se a instância está com status **Conectado**.
- Confirme que a `GROQ_API_KEY` está correta no `.env`.
- Veja os logs para erros: `docker compose logs -f app`

### Esqueci a senha do admin
Veja a senha que está no `.env`:
```bash
grep ADMIN_PASSWORD .env
```
Ou edite o `.env`, mude `ADMIN_PASSWORD` e reinicie:
```bash
docker compose restart app
```
> Obs: isso atualiza a senha apenas se o usuário admin ainda não existia no banco. Se já existia, será necessário apagar o banco ou usar um script para redefinir a senha.

### Docker diz "permission denied"
```bash
sudo usermod -aG docker $USER
# Feche e abra o terminal WSL novamente
```

### O WSL não abre ou diz "wsl --install" não funcionou
- Certifique-se de que a Virtualização está habilitada no BIOS/UEFI do seu computador.
- No Windows 10, verifique se está na versão 2004 ou mais recente: `Win + R` → `winver`.
- Tente atualizar o kernel do WSL manualmente: https://aka.ms/wsl2kernel
