# Stack tools

Written for: the team. Status: **confirmed** (checked on a machine or in the account), **received** (access exists, setup not yet verified), **proposed** (no decision yet).

## Infrastructure

| Tool | Purpose | Status | Notes |
|---|---|---|---|
| Hostinger VPS KVM 2 | production host: 2 vCPU, 8 GB RAM, 100 GB NVMe, Docker | received | access in place. IP and login only in `.env` |
| Hostinger AI Router | one access point to 150+ models (Claude, GPT, DeepSeek and others), one credit balance | received | key only in `.env`. Base URL to confirm in the panel |
| Free Hostinger domain | public URL for the judges | received | name in `.env` (`PUBLIC_DOMAIN`) |
| Hostinger connector / API / CLI | manage the VPS and DNS from the editor (Claude Code, MCP) | proposed | docs.hostinger.com/hostinger-connector, developers.hostinger.com |
| Hostinger MCP server | the same functions through MCP | proposed | github.com/hostinger/api-mcp-server |
| Docker + docker compose | packaging the application | confirmed | `Dockerfile`, `docker-compose.yml`; image built on the VPS by `deploy/deploy.sh` |
| Caddy | HTTPS on the VPS | not used | Traefik already serves HTTPS on the VPS |

## Local machine

| Tool | Version | Status |
|---|---|---|
| Git | 2.43 or newer | required |
| Python | 3.12 | required |
| uv | current | required |
| Claude Code | — | in use |

How the environment is set up on a particular machine is not described in the repository.

## Application stack

| Layer | Tool | Status |
|---|---|---|
| Backend | Python 3.12, FastAPI, uvicorn | confirmed |
| Database | SQLite (rounds, answers, disputes) | confirmed |
| Classifier | Hostinger AI Router, three models (claude-haiku-5.5, deepseek-v4.1-flash, gemini-3.7-flash), OpenAI-compatible client | confirmed |
| Tests | pytest | confirmed |
| Frontend | plain HTML/CSS/JS without a build step | confirmed |
| News collection | RSS (feedparser) from 35 outlets, TF-IDF cosine clustering | confirmed |
| Text extraction from URLs | trafilatura | confirmed |

## Secrets and access

| Item | Where it lives | In git |
|---|---|---|
| AI Router key | `.env` | no |
| VPS IP, login | `.env` | no |
| SSH key for the VPS | `secrets/` or the personal machine | no |
| Hostinger account password, 2FA | password manager / Hostinger panel | no |
