# Context Primer — VPS Automation Stack (Hostinger)

> **Como usar:** cole este bloco no início de uma **thread nova** de debug, seguido só do problema
> atual e dos logs relevantes. Evita reexplicar a arquitetura e mantém a conversa curta.
> Atualize a seção "Estado atual / pendências" sempre que resolver ou abrir algo.
> _Última atualização: 27/07/2026_

---

## 1. Topologia

- **Host:** VPS Hostinger. **Docker Engine/Swarm 29.6.2**, **1 node** (2 vCPU / 8.3 GB RAM).
- **Orquestração:** **Docker Swarm** para a maioria dos serviços (rede overlay `10.0.1.x`).
- **DOIS componentes rodam FORA do Swarm**, como **Compose standalone** (redes bridge `172.16.x.x`):
  - **Nginx Proxy Manager** — container `nginx-proxy-npm-1` (`172.16.1.x`).
  - **OpenClaw** — container `openclaw-xtop-openclaw-1` (`172.16.2.x`), imagem gerenciada Hostinger.
- **Reverse proxy:** NPM (migrado de Traefik — incompatível com Docker 29.x).

## 2. Stack (componentes, imagens e versões)

| Componente | Imagem / versão | Onde roda | Porta / nota |
|---|---|---|---|
| n8n | `n8nio/n8n:2.1.5` | Swarm, **queue mode** | editor (1) · webhook (2 réplicas) · worker (1) |
| Redis | `redis:latest` | Swarm | fila do n8n; **não publicado no host** |
| PostgreSQL | `postgres:14` | Swarm | DB do n8n (`n8ndb`); **sem porta publicada** (ver seção 5) |
| Evolution API | `evoapicloud/evolution-api:v2.3.7` | Swarm | WhatsApp API; auth mode = `apikey` |
| Portainer | `portainer/portainer-ce:2.20.1` (+ `agent:2.20.1`) | Swarm | gestão de containers; **admin só via túnel SSH** (porta 9000) |
| Nginx Proxy Manager | `jc21/nginx-proxy-manager:latest` | **Compose standalone (fora do Swarm)** | `nginx-proxy-npm-1`; portas 80/443 públicas; **admin (81) só via túnel SSH** |
| OpenClaw | `ghcr.io/hostinger/hvps-openclaw:latest` | **Compose standalone (fora do Swarm)** | `openclaw-xtop-openclaw-1`; **Control UI (54516) só via túnel SSH** — parado no momento (ver seção 6) |

- **Modelo LLM padrão:** `gpt-4o-mini` (definido por custo).
- **Tags `:latest`** (Redis, NPM, OpenClaw) flutuam — digest fixado do que roda hoje está no relatório detalhado.
- **Relatório detalhado** (inventário completo, digests fixados, redes, volumes, mounts, env mascarado):
  `arquitetura-vps-*.md`, gerado por `gerar-arquitetura-vps.sh`. Regenerar quando mudar algo estrutural.

## 3. n8n — específicos e gotchas

- **Queue mode** com serviços separados (não é single-instance).
- **API pública habilitada e validada** (curl 200 com API key). Base: `/api/v1` no domínio do editor (via NPM).
- **Objetivo em aberto:** conectar Claude Code ao n8n via MCP `n8n-mcp` (czlonkowski), **read-only** (diagnóstico, sem escrita).
- ⚠️ **`$env` bloqueado** em nós por padrão no n8n 2.x → `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`.
- Banco `n8ndb` no serviço `postgres_postgres`; conexão via rede overlay interna (nome do serviço), não afetada pela remoção da porta publicada (seção 5).

## 4. OpenClaw — específicos e gotchas

- `.env` em `/docker/openclaw-xtop/.env`. Recriar após editar: `docker compose up -d --force-recreate`.
- **Control UI:** host `:54516` → proxy interno `:18789`. Exige localhost/HTTPS (device identity) —
  satisfeito naturalmente pelo túnel SSH (seção 5).
- **Canais de chat:** nenhum ativo (Telegram e demais desabilitados). Acesso só via Control UI.
- **Parado atualmente** (27/07) — estava drenando créditos OpenAI em background mesmo ocioso, mesmo
  padrão do incidente já resolvido de *session trajectory* corrompida. Religar quando for investigar a causa.

## 5. Segurança / acesso — estado atual (27/07)

- **Firewall Hostinger:** só 22 (SSH), 80, 443 abertos ao mundo. Removida uma regra crítica
  `Accept TCP Any Any` que anulava todas as regras específicas e expunha tudo, inclusive o Postgres.
- **Acesso admin (NPM :81, Portainer :9000, OpenClaw :54516):** fechado publicamente; só via
  **túnel SSH** (local port forwarding):
  ```
  ssh -L 8181:localhost:81 -L 9090:localhost:9000 -L 54516:localhost:54516 root@<IP-da-VPS>
  ```
  Depois acessar via `http://localhost:8181` / `:9090` / `:54516` no navegador.
- **SSH:** autenticação por **chave** (ed25519); login por senha **desabilitado** no servidor.
- **Postgres (5432):** porta **não publicada** no host — removida via
  `docker service update --publish-rm published=5432,target=5432,mode=host postgres_postgres`.
  n8n continua acessando pela rede overlay interna, sem porta no host.
- **Redis (6379):** nunca esteve publicado — confirmado, sem ação necessária.

## 6. Histórico de problemas RESOLVIDOS (não rediagnosticar)

- ✅ **Consumo desgovernado de tokens OpenAI** — causado por arquivos de _session trajectory_ corrompidos do OpenClaw. Credenciais expostas foram rotacionadas.
- ✅ **n8n — Redis node "null argument error"**.
- ✅ **Evolution API — migração de payload v1 → v2**.
- ✅ **OpenAI API key inválida (401, desde 14:07 de 14/07)** — corrigida editando `OPENAI_API_KEY` no `.env` + `--force-recreate`.
- ✅ **Traefik incompatível com Docker 29.x** — trocado por Nginx Proxy Manager.
- ✅ **NPM roda em Swarm?** — não, é Compose standalone (junto com OpenClaw).
- ✅ **PostgreSQL exposto publicamente (alerta Hostinger, 27/07)** — causa dupla: (1) firewall tinha
  regra catch-all `Accept TCP Any Any` que anulava tudo; (2) Docker publicava 5432 em `0.0.0.0` via
  `mode: host`. Ambas corrigidas — ver seção 5.
- ✅ **SSH pedia senha mesmo após `PasswordAuthentication no`** — causa: `/etc/ssh/sshd_config.d/50-cloud-init.conf`
  sobrescrevia com `yes` (arquivos de `Include` são lidos **antes** do `sshd_config` principal, e o
  **primeiro** valor encontrado vence). Corrigido nesse arquivo específico.
- ✅ **`systemctl restart sshd` → "Unit not found"** — no Ubuntu o serviço se chama **`ssh`**, não `sshd`.

## 7. Estado atual / pendências

- ⏳ **Telegram + WhatsApp plugins desativados** no OpenClaw — reativar quando quiser.
- ⏳ **Conectar Claude Code → n8n via `n8n-mcp` (read-only)** — API já validada, falta ligar o MCP.
- ⏳ **OpenClaw parado** — religar quando quiser investigar/mitigar o consumo ocioso de tokens.
- ⏳ **Se acessar a VPS de outro computador** (não este Mac), precisa gerar/copiar a chave SSH pra lá também — a senha está desabilitada.

## 8. Contexto histórico (baixa prioridade)

- Explorou antes **Oracle Cloud Free Tier** (migração `VM.Standard.E2.1.Micro` → `A1.Flex` ARM), esbarrando em **falta de capacidade na região de São Paulo**.

---

### Regras para a thread de debug (opcional, cole se quiser)

- Respostas diretas e técnicas; recomendação primeiro, racional depois.
- Explicitar riscos, suposições e trade-offs.
- Não inventar números/comandos; se não souber, dizer.
- **Nunca** colar aqui API keys / credenciais — só caminhos de arquivo.
