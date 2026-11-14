# Infrastructure

Written for: the team. Only non-secret facts are recorded here. Keys and passwords live in `.env` and in the SSH agent on the local machine; they are not in the repository.

## Server

- **Host:** `srv2160832.hstgr.cloud`, IP `31.220.55.73`. Hostinger VPS KVM 2.
- **OS:** Ubuntu 24.04.5 LTS. 2 vCPU, 7.8 GB RAM, 96 GB disk.
- **Access:** SSH key login, user `deploy` (passwordless sudo, docker group). Root login only by key. Password login disabled in `/etc/ssh/sshd_config.d/00-hardening.conf`.
- **Firewall (ufw):** ports 22, 80, 443 and 32768 open. Everything else closed.
- **Docker:** 29.9.3, Compose 5.7.1.

## What already runs on the server

| Container | What it is | Ports | Notes |
|---|---|---|---|
| `traefik-traefik-1` | Hostinger's reverse proxy; issues HTTPS itself (Let's Encrypt) | 80, 443 | config in `/docker/traefik`. Do not change without need |
| `hermes-agent-duo3-hermes-agent-1` | Hermes Agent application from the Hostinger template | 32768 | published to the internet; ufw does not filter Docker ports |
| `hello-hello-1` | our test hello-world, `/opt/spotthespin/hello` | not published | reachable only through Traefik |

Public address of the hello-world: https://srv2160832.hstgr.cloud (HTTP 200, Let's Encrypt certificate).

## How new services connect on the server

- Our containers do not publish ports. Traefik finds them through labels (`traefik.enable=true`, `Host(...)`, `certresolver=letsencrypt`).
- Label template: see `/opt/spotthespin/hello/docker-compose.yml`.
- **Important:** after updating or restarting Docker, restart Traefik: `docker restart traefik-traefik-1`. Otherwise the Docker socket inside the container breaks and Traefik stops seeing labels.

## Not done yet, waiting for a decision

- **Reboot.** The updates require a reboot. Hermes and Traefik will restart, so it is scheduled by agreement.
- **Hermes on port 32768.** Currently open to the internet. It can be closed only by changing the port publication in Hermes's configuration.
- **Passwordless sudo** for user `deploy`. Convenient for automation; a question for the team.
- **Dockerfile and `docs/DEPLOY.md`.** Not yet; they come with the first real application.

## What is local

- `.env` (git-ignored): `VPS_HOST`, `VPS_USER=deploy`, `PUBLIC_DOMAIN`, `AI_ROUTER_*`.
- SSH key: the private key stays on the personal machine and is never transferred. Its public part is already in `authorized_keys` on the server.
